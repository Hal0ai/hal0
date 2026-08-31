# hal0-toolbox-cpu — llama.cpp CPU-only backend, the `cpu` runner's image
#
# Published image:  ghcr.io/hal0ai/hal0-toolbox-cpu:v1
# Local dev tag:    hal0-toolbox-cpu:dev
#
# Built and pushed by the `cpu` row of .github/workflows/toolbox.yml on every
# push to main that touches packaging/toolbox/**, and digest-pinned in
# manifest.json under toolbox_images.cpu.
#
# WHAT THIS IMAGE IS FOR (updated on #2126):
#   It began life as the CI slot-integration baseline (#75) — the Vulkan
#   toolbox built llama.cpp with -DGGML_VULKAN=ON and needs a Vulkan ICD that
#   really backs ggml tensor allocations, which a GitHub-hosted runner does
#   not have (Mesa's llvmpipe loads but does not usefully serve model layers),
#   so llama-server never finished model load and the slot stuck in IDLE.
#
#   It is now ALSO the production image for the `cpu` runner
#   (hal0.runners.RUNNER_IMAGES["cpu"]). Before #2126 that runner carried
#   FALLBACK_VULKAN_IMAGE — the GPU toolbox — so a correctly derived
#   `device = "cpu"` slot launched a GPU llama-server build and died with
#   SIGILL a second into model load, crash-looping forever while `hal0 slot
#   list` reported `warming`. This image is the one that actually runs there.
#
#   GGML_NATIVE=OFF below is load-bearing for that role, not just a CI
#   convenience: it keeps the binary portable across x86_64 instead of tuned
#   to whatever CPU the builder drew, which is exactly the property whose
#   absence produces a SIGILL on someone else's box.
#
#   GPU boxes are unaffected — they resolve `rocmfpx`
#   (DEFAULT_ROCMFPX_IMAGE), a different lineage entirely.
#
# Provider contract:
#   - ENTRYPOINT MUST be llama-server itself. The quadlet renderer emits the
#     slot's argv as `Exec=` AFTER the image (see _render_quadlet_from_plan in
#     src/hal0/providers/container.py), i.e. ARGS ONLY — never prepend
#     "llama-server" to command[0] or the binary sees its own name as a flag.
#     This is the ONE thing the host side depends on.
#   - binary path:      /opt/llama-vulkan/llama-server
#   - lib path:         /opt/llama-vulkan/lib
#     Both are INTERNAL to this image and no longer referenced anywhere under
#     src/ (checked 2026-08-31 — nothing outside this file mentions
#     /opt/llama-vulkan). The prefix is historical: it was chosen for path
#     parity with a vulkan.Dockerfile that no longer exists in this tree, back
#     when the provider hardcoded a binary path per backend. Renaming it is
#     therefore safe but pointless churn; it is kept so the built image and
#     its ENTRYPOINT/PATH/LD_LIBRARY_PATH lines stay self-consistent.
#   - runtime devices:  none (--device flags from ContainerSpec are
#                       harmless: podman accepts them, the CPU build
#                       simply ignores GPU hardware).
#   - NOT included: the #2037 fail-fast entrypoint
#     (packaging/runner/rocmfpx/entrypoint.sh), which supervises llama-server
#     and translates a died-during-load into exit 64. A slot on this image
#     therefore relies on the HOST-side half — the quadlet's
#     RestartPreventExitStatus=64 132 (#2126) — to fail fast on SIGILL.
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

# llama.cpp git ref.
#
# UNPINNED, and knowingly so as of #2126: toolbox.yml passes no build-args, so
# a :v1 push captures whatever llama.cpp master was at build time. That was
# acceptable while this was a CI smoke image and is a known gap now that it is
# the `cpu` runner's production image. What holds the shipped surface still is
# the DIGEST pin in manifest.json (toolbox_images.cpu) — installs resolve the
# exact image that was validated, not "whatever :v1 points at today" — so an
# unreviewed upstream never reaches a box without a manifest change. Pinning a
# ref here is the follow-up that makes the BUILD reproducible too.
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

# Stage the install layout.  /opt/llama-vulkan/ (not /opt/llama-cpu/) is
# historical — see the "Provider contract" note in the header: the provider no
# longer resolves a binary path per backend, so nothing outside this image
# depends on the prefix, and it is kept only so ENTRYPOINT / PATH /
# LD_LIBRARY_PATH below stay self-consistent.
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

# Baked into the image, so the binary finds its libs with no host cooperation.
ENV LD_LIBRARY_PATH=/opt/llama-vulkan/lib \
    PATH=/opt/llama-vulkan:${PATH}

# Non-root user matching the systemd unit (User=hal0, Group=hal0).
# 1000:1000 lines up with the host hal0 user when bind-mounted model paths
# are owned by 1000:1000.
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

# Image-level healthcheck. Informational only for hal0 slots — the quadlet
# renders its own Health* directives from the launch plan.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${HAL0_PORT:-8081}/v1/models" || exit 1

# ENTRYPOINT contract: Provider's ContainerSpec.command[] is ARGS only.
ENTRYPOINT ["/opt/llama-vulkan/llama-server"]

# Default CMD: print help. Real invocations override this from the
# Provider's ContainerSpec.command[].
CMD ["--help"]
