#!/usr/bin/env bash
# Shared config + helpers for the hal0 ROCmFPX quantize pipeline.
#
# Source this from the other scripts:
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   source "$SCRIPT_DIR/rocmfpx-env.sh"
#
# It defines the repo/arch contract used by rocmfpx-build.sh, rocmfpx-quantize.sh
# and rocmfpx-pipeline.sh, and resolves the absolute BUILD_DIR / QUANTIZE_BIN so
# the build step and the quantize step always agree on where the binaries live.

set -o pipefail

# --- Upstream repo / variant -------------------------------------------------
# ROCMFPX_VARIANT selects which tree to build. The two variants use different
# repos, branches, and (crucially) separate checkout+build dirs, so an
# experimental build never clobbers the stable one.
#
#   stable        Hal0ai/Hal0_ROCmFPX @ main  <- DEFAULT. The hal0-branded fork
#                 (of charlie12345/ROCmFPX); its main HEAD is the same commit the
#                 published ghcr.io/hal0ai/hal0-rocmfpx:server image is built from.
#   experimental  charlie12345/ROCmFPX @ experimental-rocmfpx-branch. The
#                 experimental branch lives on upstream, not mirrored into the
#                 Hal0ai fork; kept in $HOME/ROCmFPX-experimental.
#
# Any of ROCMFPX_REPO / ROCMFPX_BRANCH / ROCMFPX_HOME still override per-variant
# defaults if set explicitly.
ROCMFPX_VARIANT="${ROCMFPX_VARIANT:-stable}"
case "$ROCMFPX_VARIANT" in
    stable)
        _rocmfpx_def_repo="https://github.com/Hal0ai/Hal0_ROCmFPX.git"
        _rocmfpx_def_branch="main"
        _rocmfpx_def_home="$HOME/ROCmFPX"
        ;;
    experimental)
        _rocmfpx_def_repo="https://github.com/charlie12345/ROCmFPX.git"
        _rocmfpx_def_branch="experimental-rocmfpx-branch"
        _rocmfpx_def_home="$HOME/ROCmFPX-experimental"
        ;;
    *)
        echo "ERROR: unknown ROCMFPX_VARIANT='$ROCMFPX_VARIANT' (want: stable|experimental)" >&2
        # sourced -> return aborts the caller (set -e); executed -> exit.
        # shellcheck disable=SC2317
        return 2 2>/dev/null || exit 2
        ;;
esac

ROCMFPX_REPO="${ROCMFPX_REPO:-$_rocmfpx_def_repo}"
ROCMFPX_BRANCH="${ROCMFPX_BRANCH:-$_rocmfpx_def_branch}"

# Where the tree is cloned + built (variant-specific so stable/experimental
# builds coexist). Override to reuse an existing checkout.
ROCMFPX_HOME="${ROCMFPX_HOME:-$_rocmfpx_def_home}"

# Target GPU arch. Drives which build-*.sh runs and which build dir is used.
#   strix   Strix Halo / RDNA3.5 (gfx1151)  <- this box's default
#   rdna2   RX 6000 (gfx1030 class)
#   rdna3   RX 7000 (gfx1100 class)
#   rdna4   RX 9000 (gfx1200 class)
#   gfx906  Vega 7nm / MI50 class
ARCH="${ARCH:-strix}"

# Parallel build jobs (README uses JOBS=16; default to all cores).
JOBS="${JOBS:-$(nproc)}"

# Map ARCH -> upstream build script under scripts/.
rocmfpx_build_script() {
    case "$1" in
        strix)  echo "build-strix-rocmfp4-mtp.sh" ;;
        rdna2)  echo "build-rdna2.sh" ;;
        rdna3)  echo "build-rdna3.sh" ;;
        rdna4)  echo "build-rdna4.sh" ;;
        gfx906) echo "build-gfx906.sh" ;;
        *)      echo "" ;;
    esac
}

# Map ARCH -> build output dir name (matches each build script's BUILD_DIR).
rocmfpx_build_dirname() {
    case "$1" in
        strix)  echo "build-strix-rocmfp4" ;;
        rdna2)  echo "build-rdna2" ;;
        rdna3)  echo "build-rdna3" ;;
        rdna4)  echo "build-rdna4" ;;
        gfx906) echo "build-gfx906" ;;
        *)      echo "" ;;
    esac
}

# Resolve absolute BUILD_DIR + QUANTIZE_BIN for the selected ARCH.
# Exports BUILD_DIR and QUANTIZE_BIN for downstream scripts + the upstream wrapper.
rocmfpx_resolve_build() {
    local dirname
    dirname="$(rocmfpx_build_dirname "$ARCH")"
    if [[ -z "$dirname" ]]; then
        echo "ERROR: unknown ARCH='$ARCH' (want: strix|rdna2|rdna3|rdna4|gfx906)" >&2
        return 2
    fi
    export BUILD_DIR="${BUILD_DIR:-$ROCMFPX_HOME/$dirname}"
    export QUANTIZE_BIN="${QUANTIZE_BIN:-$BUILD_DIR/bin/llama-quantize}"
}

# Runtime env for GPU-touching binaries. Quantize itself is CPU-side, but
# exporting these keeps llama-cli / llama-server smoke checks correct on Strix.
rocmfpx_runtime_env() {
    if [[ "$ARCH" == "strix" ]]; then
        export HSA_OVERRIDE_GFX_VERSION="${HSA_OVERRIDE_GFX_VERSION:-11.5.1}"
        export GGML_HIP_ENABLE_UNIFIED_MEMORY="${GGML_HIP_ENABLE_UNIFIED_MEMORY:-1}"
    fi
}

rocmfpx_log() { printf '\033[1;36m[rocmfpx]\033[0m %s\n' "$*" >&2; }
rocmfpx_err() { printf '\033[1;31m[rocmfpx] ERROR:\033[0m %s\n' "$*" >&2; }
