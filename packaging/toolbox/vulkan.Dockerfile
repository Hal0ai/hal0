# hal0-toolbox-vulkan — llama.cpp Vulkan backend for AMD iGPU (Strix Halo)
#
# Target image:    ghcr.io/hal0ai/hal0-toolbox-vulkan:v2
#                  (v2 pins LLAMA_CPP_REF=b9279 so /slots emits
#                  ``n_prompt_tokens`` and the dashboard can synthesise
#                  a KV-cache % gauge — see
#                  src/hal0/api/routes/slots.py:_scrape_llama_metrics.)
# Local dev tag:   hal0-toolbox-vulkan:dev
#
# Provider contract (src/hal0/providers/llama_server.py):
#   - binary path:      /opt/llama-vulkan/llama-server
#   - lib path:         /opt/llama-vulkan/lib
#   - ENTRYPOINT MUST be llama-server itself; ContainerSpec.command[] is
#     ARGS ONLY (see llama_server.py:326 — never prepend "llama-server"
#     to command[0] or the binary sees its own name as a flag).
#   - runtime devices:  /dev/kfd, /dev/dri/renderD128 (host iGPU)
#   - runtime groups:   video, render (added by ContainerSpec.group_add)
#
# Build:
#   docker build -t hal0-toolbox-vulkan:dev -f packaging/toolbox/vulkan.Dockerfile .
#
# Verify (no iGPU on this dev VM — vulkaninfo will load Mesa but list 0 devices):
#   docker run --rm hal0-toolbox-vulkan:dev vulkaninfo --summary
#   docker run --rm hal0-toolbox-vulkan:dev --help | head
#
# Verify on real iGPU host (haloai LXC or hal0-test):
#   docker run --rm --device=/dev/dri/renderD128 --device=/dev/kfd \
#       --group-add video --group-add render \
#       hal0-toolbox-vulkan:dev vulkaninfo --summary

# ─── Stage 1 — builder ────────────────────────────────────────────────────────
FROM ubuntu:24.04 AS builder

# llama.cpp git ref. Override at build time, e.g.:
#   docker build --build-arg LLAMA_CPP_REF=b4404 ...
# For :dev we track master; :v2 pins to b9279 (2026-05-22) — the
# first ref that reinstates per-slot ``n_prompt_tokens`` in /slots
# after the server.cpp restructure, which the dashboard's KV-cache %
# gauge depends on. Bump the default when bumping the published tag.
ARG LLAMA_CPP_REF=b9279
ARG DEBIAN_FRONTEND=noninteractive

# Build deps: compiler toolchain, cmake, Vulkan SDK headers, glslang
# (shader compiler — required for GGML_VULKAN), libcurl for the
# --hf-repo / model download convenience, git for the clone.
#
# NOTE: libvulkan-dev pulls in the loader headers; the actual ICD
# (mesa-vulkan-drivers) is only needed at runtime, not build time.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        ninja-build \
        git \
        ca-certificates \
        pkg-config \
        libvulkan-dev \
        glslang-tools \
        glslc \
        spirv-headers \
        spirv-tools \
        libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch "${LLAMA_CPP_REF}" \
        https://github.com/ggml-org/llama.cpp.git . \
    || (git clone https://github.com/ggml-org/llama.cpp.git . \
        && git checkout "${LLAMA_CPP_REF}")

# Build llama.cpp with Vulkan backend.
# - GGML_VULKAN=ON     : enable the Vulkan compute backend
# - LLAMA_CURL=ON      : enable --hf-repo / -hfr model fetching
# - BUILD_SHARED_LIBS=OFF : statically link ggml so the runtime image
#                          only needs the Vulkan loader, not ggml .so's.
# - CMAKE_BUILD_TYPE=Release : -O3, no asserts.
RUN cmake -S . -B build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_VULKAN=ON \
        -DLLAMA_CURL=ON \
        -DBUILD_SHARED_LIBS=OFF \
        -DLLAMA_BUILD_TESTS=OFF \
        -DLLAMA_BUILD_EXAMPLES=ON \
        -DLLAMA_BUILD_SERVER=ON \
    && cmake --build build --config Release --target llama-server -j"$(nproc)"

# Stage the install layout the Provider expects.
RUN mkdir -p /out/opt/llama-vulkan/bin /out/opt/llama-vulkan/lib \
    && cp build/bin/llama-server /out/opt/llama-vulkan/llama-server \
    && (find build -name '*.so*' -exec cp -av {} /out/opt/llama-vulkan/lib/ \; || true) \
    && strip /out/opt/llama-vulkan/llama-server || true

# ─── Stage 2 — runtime ────────────────────────────────────────────────────────
FROM ubuntu:24.04 AS runtime

ARG DEBIAN_FRONTEND=noninteractive

# Runtime deps:
# - mesa-vulkan-drivers : the AMD/RADV Vulkan ICD (this is the thing
#                         that talks to /dev/dri/renderD128 on Strix Halo)
# - vulkan-tools        : vulkaninfo for verification
# - libvulkan1          : Vulkan loader (pulled in by mesa anyway, listed
#                         explicitly so the runtime contract is obvious)
# - libgomp1            : OpenMP runtime — llama.cpp threads
# - libcurl4            : runtime side of LLAMA_CURL=ON
# - libc++ / libstdc++  : C++ runtime (already in ubuntu:24.04 base, but
#                         libstdc++6 listed defensively)
# - ca-certificates     : HTTPS for curl-backed model fetch
RUN apt-get update && apt-get install -y --no-install-recommends \
        mesa-vulkan-drivers \
        vulkan-tools \
        libvulkan1 \
        libgomp1 \
        libcurl4 \
        libstdc++6 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# Copy the built binary + any shared libs from the builder.
COPY --from=builder /out/opt/llama-vulkan /opt/llama-vulkan

# Wire the lib path the Provider hands to systemd via HAL0_LD_PATH.
# Also make the binary discoverable as `llama-server` on PATH for
# operator convenience (e.g. `docker run ... llama-server --help`).
ENV LD_LIBRARY_PATH=/opt/llama-vulkan/lib \
    PATH=/opt/llama-vulkan:${PATH}

# Non-root user matching the systemd unit (User=hal0, Group=hal0).
# At runtime the orchestrator adds `video` and `render` groups via
# ContainerSpec.group_add so /dev/dri/renderD128 is accessible.
#
# NOTE: ubuntu:24.04 ships with a default `ubuntu` user at UID/GID 1000.
# Remove it before creating hal0 at the same numeric ID so bind-mounted
# host paths owned by 1000:1000 (the host hal0 user) line up cleanly.
RUN userdel --remove ubuntu 2>/dev/null || true \
    && groupadd --system --gid 1000 hal0 \
    && useradd  --system --uid 1000 --gid 1000 --shell /usr/sbin/nologin hal0

# Model bind-mount target. ContainerSpec mounts the host models_base
# at the same path inside; ensure it exists with permissive perms so
# the bind-mount sits on a real dir.
RUN mkdir -p /var/lib/hal0/models && chown -R hal0:hal0 /var/lib/hal0

USER hal0
WORKDIR /var/lib/hal0

# Llama-server's default listen port is provided via --port; we expose
# the slot range default (8081) as documentation. Network mode is
# `host` at runtime per ContainerSpec, so EXPOSE is informational.
EXPOSE 8081

# Healthcheck mirrors the Provider's Tier 1 contract loosely — a real
# health probe also runs a sentinel chat completion, but a /v1/models
# 200 is enough for `docker inspect` to flip to healthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${HAL0_PORT:-8081}/v1/models" || exit 1

# ENTRYPOINT contract: the Provider's ContainerSpec.command[] is ARGS
# only — see llama_server.py:326. If we set the entrypoint to a shell
# or omit it, the Provider's command list ("--model", path, "--port", ...)
# would be interpreted as the executable.
ENTRYPOINT ["/opt/llama-vulkan/llama-server"]

# Default CMD: print help. Real invocations override this from the
# Provider's ContainerSpec.command[].
CMD ["--help"]
