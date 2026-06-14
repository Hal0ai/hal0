# hal0-toolbox-flm — FastFlowLM (FLM) on AMD Strix Halo XDNA2 NPU.
#
# Target image:    ghcr.io/<owner>/hal0-toolbox-flm:v1
# Local dev tag:   hal0-toolbox-flm:dev
#
# Provider contract (src/hal0/providers/flm.py):
#   - binary path:      /usr/local/bin/flm
#   - subcommand:       `flm serve <model_tag> --port N --host 0.0.0.0 --ctx-len C`
#   - endpoints:        /v1/models, /v1/chat/completions, /v1/embeddings,
#                       /v1/audio/transcriptions (when --asr is enabled)
#   - runtime devices:  /dev/accel (XDNA2 NPU), /dev/dri (iGPU helpers)
#   - runtime ulimits:  --ulimit memlock=-1:-1 (NPU buffer pinning)
#   - model cache:      /root/.config/flm (bind-mount via slot_cfg._paths.flm_cache)
#
# Upstream: https://github.com/FastFlowLM/FastFlowLM — Apache 2.0
# Driver:   https://github.com/amd/xdna-driver (XRT + xdna NPU plugin)
# Reference: https://github.com/hpenedones/fastflowlm-docker (3-stage build,
#            ~440MB final, ~15-25 min build time on a fast box).
#
# HOST REQUIREMENTS (cannot be satisfied inside the image):
#   - Linux kernel ≥ 6.11 with `amdxdna` loaded
#   - NPU firmware ≥ 1.1.0.0
#   - /dev/accel/accel0 visible on the host
#
# Build:
#   docker build -t hal0-toolbox-flm:dev -f packaging/toolbox/flm.Dockerfile .
#
# Smoke (on a Strix Halo host with NPU passthrough):
#   docker run --rm --device=/dev/accel --device=/dev/dri \
#       --ulimit memlock=-1:-1 --group-add video --group-add render \
#       hal0-toolbox-flm:dev flm validate
#
# NOTE: This Dockerfile builds FLM + XRT from source. Build hosts WITHOUT
# the xdna driver kernel headers will still compile cleanly — the
# xdna-driver userspace bits are headers + libraries, not kernel modules.
# Only RUNTIME requires the host kernel + /dev/accel.

# ─── Stage 1 — XRT + xdna-driver userspace builder ────────────────────────────
FROM ubuntu:24.04 AS xrt-builder

ARG DEBIAN_FRONTEND=noninteractive
# Pinned to the Ryzen AI 1.7.1 stable pairing (xdna-driver 2.21.75 ↔ XRT
# 2.21.75), matching the CT105 host's libxrt. Was `main` (unreproducible,
# drifted to XRT 2.23.0). Tag verified to exist upstream.
ARG XDNA_REF=2.21.75

# Minimal bootstrap deps — just enough to clone the repo and run XRT's
# own dependency installer. After three rounds of whack-a-mole with
# missing find_package() targets (OpenCL → Boost components → Curses →
# Protobuf → …), it's faster to defer to XRT's canonical dep script
# which knows every package the configure step touches on each
# Ubuntu release.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        sudo \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
# NOTE: clone WITHOUT --recurse-submodules, then init submodules AFTER the
# tag checkout. When pinning XDNA_REF to a release tag, the bundled XRT
# submodule points at a specific commit; a recursive clone would first
# populate xrt/ at the default-branch commit, and the later
# `submodule update` then fails to switch it ("Unable to checkout <sha> in
# submodule path 'xrt'"). Initialising submodules post-checkout fetches the
# tag's pinned XRT commit cleanly.
RUN git clone https://github.com/amd/xdna-driver.git . \
    && git checkout "${XDNA_REF}" \
    && git submodule update --init --recursive

# XRT ships its own apt installer for every Ubuntu release it supports.
# `-docker` skips kernel-module-build deps (we're never going to insmod
# inside the image). It's idempotent and covers GTest, Protobuf, ncurses,
# Boost (full), OpenCL, systemd, pybind11, rapidjson, uuid, ffmpeg-libs,
# etc. — i.e. the same packages we were enumerating by hand.
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && ./xrt/src/runtime_src/tools/scripts/xrtdeps.sh -docker \
    && rm -rf /var/lib/apt/lists/*

# xdna-driver bundles upstream XRT as a submodule under xrt/.
# Build XRT first, then the xdna plugin against it.
#
# -noert: skip ERT MicroBlaze firmware build. ERT requires the Xilinx
#   Vitis toolchain (XILINX_VITIS env) which we don't ship in CI — and
#   the XDNA2 NPU on Strix Halo doesn't use the MicroBlaze firmware path
#   anyway (it has its own AIE compiler output). Without this flag the
#   build aborts with "XILINX_VITIS is undefined" before any compilation
#   happens. Confirmed by upstream xrt build.sh: "To treat as a warning
#   use -noert option."
#
# `./build.sh -opt` already runs `cmake --install` into the staging dir
# `build/Release/opt/xilinx/xrt`. The companion `./build.sh -install`
# step is the .deb-packaging path which (a) expects ERT firmware to be
# present and (b) prints help + exits 1 when called twice in a row.
# So: drop the second invocation and copy the staging tree directly
# into /opt/xilinx/xrt — that's exactly what the .deb would put there.
RUN cd xrt/build && ./build.sh -noert -opt \
    && mkdir -p /opt/xilinx \
    && cp -a Release/opt/xilinx/xrt /opt/xilinx/xrt

# Build the xdna NPU plugin against the XRT we just installed.
RUN mkdir -p build && cd build \
    && cmake -DCMAKE_INSTALL_PREFIX=/opt/xilinx/xrt .. \
    && make -j"$(nproc)" \
    && make install

# ─── Stage 2 — FLM builder ────────────────────────────────────────────────────
FROM ubuntu:24.04 AS flm-builder

ARG DEBIAN_FRONTEND=noninteractive
# Pinned to v0.9.43 (latest, 2026-05-26) — matches the host model cache and
# clears the "0.9.43 > 0.9.42" incompatibility warning the old image emitted.
ARG FLM_REF=v0.9.43

# Pull the freshly-built XRT in so FLM can link against it.
COPY --from=xrt-builder /opt/xilinx/xrt /opt/xilinx/xrt

# Dep list mirrors upstream FastFlowLM/Dockerfile (main): adds rust
# toolchain (FLM ships Rust components since 2026-04), nasm + patchelf
# (xclbin packaging), and the full ffmpeg dev libs (libavutil,
# libswresample) — the prior hand-picked subset compiled against an
# older release that predated the Rust rewrite of FLM's tokenizer.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        ninja-build \
        git \
        curl \
        ca-certificates \
        libboost-program-options-dev \
        libcurl4-openssl-dev \
        libfftw3-dev \
        libavformat-dev \
        libavcodec-dev \
        libavutil-dev \
        libswscale-dev \
        libswresample-dev \
        libreadline-dev \
        libdrm-dev \
        nasm \
        patchelf \
        pkg-config \
        uuid-dev \
    && rm -rf /var/lib/apt/lists/*

# Install a recent Rust via rustup. Ubuntu 24.04 ships rustc 1.75 in
# apt which is too old. FLM's tokenizers-cpp pulls a dependency graph
# that requires rustc >= 1.85 — the floor moved twice while debugging:
# `monostate v0.1.18` needs 1.79, and `unicode-segmentation v1.13.2`
# (pulled transitively) needs 1.85. Pinning to 1.85.0 stable keeps
# the build reproducible.
ARG RUST_VERSION=1.85.0
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:${PATH}
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --default-toolchain ${RUST_VERSION} --profile minimal --no-modify-path \
    && rustc --version && cargo --version

WORKDIR /src
RUN git clone --recurse-submodules \
        https://github.com/FastFlowLM/FastFlowLM.git . \
    && git checkout "${FLM_REF}" \
    && git submodule update --init --recursive

# Upstream layout note: FastFlowLM's CMakeLists.txt + CMakePresets.json
# live under src/ (not the repo root). The previous Dockerfile config
# step `cmake --preset linux-default` ran from /src and bailed with
# "Could not read presets from /src" — the presets file is actually
# at /src/src/CMakePresets.json. cd into the source dir first.
WORKDIR /src/src
RUN cmake --preset linux-default \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/fastflowlm \
        -DXRT_INCLUDE_DIR=/opt/xilinx/xrt/include \
        -DXRT_LIB_DIR=/opt/xilinx/xrt/lib \
    && cmake --build build -j"$(nproc)" \
    && cmake --install build

# ─── Stage 3 — runtime ────────────────────────────────────────────────────────
FROM ubuntu:24.04 AS runtime

ARG DEBIAN_FRONTEND=noninteractive

# Runtime deps only — no compilers, headers, or source.
# Boost-program-options, FFTW, ffmpeg-libs, readline are FLM's runtime
# link-time deps; libdrm/libelf/libudev are XRT's; curl/ca-certificates
# for FLM's model pull from HF.
# NOTE: XRT links BOTH boost_program_options AND boost_filesystem (see XRT
# boostUtil.cmake) but XRT does not declare the filesystem dep (Xilinx/XRT
# #6295). Without libboost-filesystem, `xrt-smi` dies with
# "error while loading shared libraries: libboost_filesystem.so.1.83.0",
# which breaks NPU telemetry (aie-partitions). Keep both boost runtime libs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libboost-program-options1.83.0 \
        libboost-filesystem1.83.0 \
        libboost-system1.83.0 \
        libcurl4 \
        libfftw3-single3 \
        libavformat60 \
        libavcodec60 \
        libswscale7 \
        libreadline8t64 \
        libdrm2 \
        libelf1t64 \
        libudev1 \
        libgomp1 \
        ca-certificates \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Copy XRT runtime libs + xdna plugin.
COPY --from=xrt-builder /opt/xilinx/xrt/lib /opt/xilinx/xrt/lib
COPY --from=xrt-builder /opt/xilinx/xrt/bin /opt/xilinx/xrt/bin
COPY --from=xrt-builder /opt/xilinx/xrt/share /opt/xilinx/xrt/share

# Copy FLM tree (binary + xclbins + share assets).
COPY --from=flm-builder /opt/fastflowlm /opt/fastflowlm

# Symlink the binary so the Provider's start_cmd default (/usr/local/bin/flm)
# resolves. The Provider also accepts HAL0_FLM_BINARY override.
RUN ln -sf /opt/fastflowlm/bin/flm /usr/local/bin/flm

ENV LD_LIBRARY_PATH=/opt/xilinx/xrt/lib:/opt/fastflowlm/lib \
    PATH=/opt/xilinx/xrt/bin:/opt/fastflowlm/bin:${PATH} \
    XILINX_XRT=/opt/xilinx/xrt \
    FLM_XCLBIN_PATH=/opt/fastflowlm/share/flm/xclbins

# Build-time smoke: xrt-smi must load its shared libs (catches a missing
# boost regression at build, not at runtime on the NPU box). The `unwrapped`
# binary is used because the wrapped `xrt-smi` is a python shim that expects
# `source setup.sh`; the unwrapped binary runs directly off LD_LIBRARY_PATH.
RUN /opt/xilinx/xrt/bin/unwrapped/xrt-smi --version

# UID/GID match host hal0; bind-mounted model cache reads cleanly.
# NOTE: FLM defaults its model cache to ~/.config/flm; the slot's
# ContainerSpec bind-mounts host flm_cache into the same in-container
# path (see flm.py:container_spec).
#
# Pre-create render/video groups so docker --group-add (FLM ContainerSpec
# passes ["video", "render"]) resolves inside the container.
RUN userdel -r ubuntu 2>/dev/null || true \
    && groupadd --system --gid 44  video  2>/dev/null || true \
    && groupadd --system --gid 993 render 2>/dev/null || true \
    && groupadd --system --gid 1000 hal0 \
    && useradd  --system --uid 1000 --gid 1000 --shell /usr/sbin/nologin \
                --home-dir /var/lib/hal0 --create-home hal0 \
    && usermod -aG video,render hal0 \
    && mkdir -p /var/lib/hal0/.config/flm /var/lib/hal0/models \
    && chown -R hal0:hal0 /var/lib/hal0

USER hal0
WORKDIR /var/lib/hal0

EXPOSE 8086

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${HAL0_PORT:-8086}/v1/models" || exit 1

# tini → flm. ContainerSpec.command[] supplies ["serve", <tag>, "--port", ...].
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/flm"]
CMD ["--help"]
