# hal0-toolbox-cpu — llama.cpp CPU-only backend for CI smoke
#
# Target image:    hal0-toolbox-cpu:ci  (built in-workflow; never published)
# Local dev tag:   hal0-toolbox-cpu:dev
#
# Why a separate image:
#   The Vulkan toolbox (vulkan.Dockerfile) builds llama.cpp with
#   -DGGML_VULKAN=ON and depends on a Vulkan ICD at runtime.  On a
#   GitHub-hosted runner with no GPU and no Vulkan ICD that actually
#   serves model layers (Mesa's llvmpipe loads but doesn't usefully back
#   ggml-vulkan tensor allocations), llama-server cannot complete model
#   load — the slot lands in IDLE rather than READY because
#   /v1/models stays empty (see src/hal0/slots/manager.py:1422).  This
#   broke tier β integration on every commit since 2026-05-18 once the
#   modelless-READY adoption guard (commit dc71fcf) landed.
#
#   PLAN.md §10.2 names the CI tier "Vulkan-CPU baseline".  This image
#   is that baseline — same llama.cpp, same provider contract, just
#   compiled without the Vulkan backend so a model actually loads on a
#   plain Ubuntu runner.  Production stays on vulkan.Dockerfile / Strix
#   Halo iGPU; rocm.Dockerfile is unchanged.
#
# Provider contract (matches vulkan.Dockerfile so the LlamaServerProvider
# treats it identically when HAL0_TOOLBOX_IMAGE_VULKAN points here):
#   - binary path:      /opt/llama-vulkan/llama-server   (same path as the
#                       vulkan image — keeps provider/env defaults intact)
#   - lib path:         /opt/llama-vulkan/lib
#   - ENTRYPOINT MUST be llama-server itself; ContainerSpec.command[] is
#     ARGS ONLY (see llama_server.py:326 — never prepend "llama-server"
#     to command[0] or the binary sees its own name as a flag).
#   - runtime devices:  none (--device flags from ContainerSpec are
#                       harmless: docker accepts them, the CPU build
#                       simply ignores GPU hardware).
#
# Build:
#   docker build -t hal0-toolbox-cpu:dev -f packaging/toolbox/cpu.Dockerfile .
#
# Verify:
#   docker run --rm hal0-toolbox-cpu:dev --help | head
#   docker run --rm -v /path/to/model:/m hal0-toolbox-cpu:dev \
#       --model /m/qwen2.5-0.5b-instruct-q4_k_m.gguf --port 8081 -ngl 0

# ─── Stage 1 — builder ────────────────────────────────────────────────────────
FROM ubuntu:24.04 AS builder

# llama.cpp git ref.  Keep in lockstep with vulkan.Dockerfile so the CPU
# baseline reflects the same upstream version we ship to users.
ARG LLAMA_CPP_REF=master
ARG DEBIAN_FRONTEND=noninteractive

# Build deps: compiler toolchain, cmake, libcurl for --hf-repo, git for
# the clone.  No Vulkan SDK, no glslang — CPU-only.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        ninja-build \
        git \
        ca-certificates \
        pkg-config \
        libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch "${LLAMA_CPP_REF}" \
        https://github.com/ggml-org/llama.cpp.git . \
    || (git clone https://github.com/ggml-org/llama.cpp.git . \
        && git checkout "${LLAMA_CPP_REF}")

# Build llama.cpp CPU-only.
# - GGML_VULKAN=OFF / GGML_CUDA=OFF / GGML_HIP=OFF : no GPU backends.
# - GGML_NATIVE=OFF                                : portable -march settings
#                                                    so the image runs on any
#                                                    x86_64 runner (GHA uses
#                                                    rotating hardware).
# - LLAMA_CURL=ON                                  : --hf-repo / -hfr support.
# - BUILD_SHARED_LIBS=OFF                          : static link ggml.
RUN cmake -S . -B build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_VULKAN=OFF \
        -DGGML_CUDA=OFF \
        -DGGML_HIP=OFF \
        -DGGML_NATIVE=OFF \
        -DLLAMA_CURL=ON \
        -DBUILD_SHARED_LIBS=OFF \
        -DLLAMA_BUILD_TESTS=OFF \
        -DLLAMA_BUILD_EXAMPLES=ON \
        -DLLAMA_BUILD_SERVER=ON \
    && cmake --build build --config Release --target llama-server -j"$(nproc)"

# Stage the install layout the Provider expects.  We deliberately reuse
# /opt/llama-vulkan/ (not /opt/llama-cpu/) so HAL0_TOOLBOX_IMAGE_VULKAN
# can swap this image in without touching provider path resolution
# (LlamaServerProvider.build_env at llama_server.py:162 hardcodes
# /opt/llama-vulkan/llama-server as the default binary for backend=vulkan
# AND backend=cpu).
RUN mkdir -p /out/opt/llama-vulkan/bin /out/opt/llama-vulkan/lib \
    && cp build/bin/llama-server /out/opt/llama-vulkan/llama-server \
    && (find build -name '*.so*' -exec cp -av {} /out/opt/llama-vulkan/lib/ \; || true) \
    && strip /out/opt/llama-vulkan/llama-server || true

# ─── Stage 2 — runtime ────────────────────────────────────────────────────────
FROM ubuntu:24.04 AS runtime

ARG DEBIAN_FRONTEND=noninteractive

# Runtime deps — CPU-only, no Vulkan loader/driver.
# - libgomp1            : OpenMP runtime — llama.cpp threading.
# - libcurl4            : runtime side of LLAMA_CURL=ON.
# - libstdc++6          : C++ runtime (in base, listed defensively).
# - ca-certificates     : HTTPS for curl-backed model fetch.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        libcurl4 \
        libstdc++6 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# Copy the built binary + any shared libs from the builder.
COPY --from=builder /out/opt/llama-vulkan /opt/llama-vulkan

# Wire the lib path the Provider hands to systemd via HAL0_LD_PATH.
ENV LD_LIBRARY_PATH=/opt/llama-vulkan/lib \
    PATH=/opt/llama-vulkan:${PATH}

# Non-root user matching the systemd unit (User=hal0, Group=hal0).
# Mirrors vulkan.Dockerfile — 1000:1000 lines up with the host hal0 user
# when bind-mounted model paths are owned by 1000:1000.
RUN userdel --remove ubuntu 2>/dev/null || true \
    && groupadd --system --gid 1000 hal0 \
    && useradd  --system --uid 1000 --gid 1000 --shell /usr/sbin/nologin hal0

# Model bind-mount target.
RUN mkdir -p /var/lib/hal0/models && chown -R hal0:hal0 /var/lib/hal0

USER hal0
WORKDIR /var/lib/hal0

# Llama-server's default listen port is provided via --port; we expose
# the slot range default (8081) as documentation.
EXPOSE 8081

# Healthcheck mirrors vulkan.Dockerfile.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${HAL0_PORT:-8081}/v1/models" || exit 1

# ENTRYPOINT contract: Provider's ContainerSpec.command[] is ARGS only.
ENTRYPOINT ["/opt/llama-vulkan/llama-server"]

# Default CMD: print help. Real invocations override this from the
# Provider's ContainerSpec.command[].
CMD ["--help"]
