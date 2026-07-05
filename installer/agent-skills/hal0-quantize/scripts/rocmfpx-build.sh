#!/usr/bin/env bash
# Clone (or update) Hal0ai/Hal0_ROCmFPX and build the llama.cpp tree for one arch.
#
# Tracks the repo's default branch (latest) — no pinned commit. Idempotent:
# re-running fetches + fast-forwards the checkout and rebuilds in place.
#
# NOTE: the published ghcr.io/hal0ai/hal0-rocmfpx:server image only bundles
# llama-server/bench/cli (not llama-quantize), so quantization must build from
# source here — that is exactly what this script does.
#
# Usage:
#   ARCH=strix JOBS=16 scripts/rocmfpx-build.sh
#   ARCH=rdna3 scripts/rocmfpx-build.sh
#
# Key env (see rocmfpx-env.sh for the full contract + defaults):
#   ARCH=strix               strix|rdna2|rdna3|rdna4|gfx906
#   JOBS=$(nproc)            parallel build jobs
#   ROCMFPX_HOME=$HOME/ROCmFPX   checkout + build location
#   ROCMFPX_REPO / ROCMFPX_BRANCH  override the upstream source
#   FORCE_CLEAN=1            wipe the arch build dir before configuring

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/rocmfpx-env.sh"

rocmfpx_resolve_build

build_script="$(rocmfpx_build_script "$ARCH")"
if [[ -z "$build_script" ]]; then
    rocmfpx_err "unknown ARCH='$ARCH' (want: strix|rdna2|rdna3|rdna4|gfx906)"
    exit 2
fi

# --- 1. clone or update ------------------------------------------------------
if [[ -d "$ROCMFPX_HOME/.git" ]]; then
    rocmfpx_log "Updating existing checkout at $ROCMFPX_HOME ($ROCMFPX_BRANCH)"
    git -C "$ROCMFPX_HOME" fetch --depth 1 origin "$ROCMFPX_BRANCH"
    git -C "$ROCMFPX_HOME" checkout "$ROCMFPX_BRANCH"
    git -C "$ROCMFPX_HOME" reset --hard "origin/$ROCMFPX_BRANCH"
else
    rocmfpx_log "Cloning $ROCMFPX_REPO -> $ROCMFPX_HOME ($ROCMFPX_BRANCH)"
    git clone --depth 1 --branch "$ROCMFPX_BRANCH" "$ROCMFPX_REPO" "$ROCMFPX_HOME"
fi

rocmfpx_log "HEAD: $(git -C "$ROCMFPX_HOME" rev-parse --short HEAD)"

# --- 2. optional pre-flight --------------------------------------------------
# The repo ships a requirements checker; run it if present (best-effort).
if [[ -x "$ROCMFPX_HOME/scripts/check-requirements.sh" && "${SKIP_REQ_CHECK:-0}" != "1" ]]; then
    rocmfpx_log "check-requirements.sh available (set SKIP_REQ_CHECK=0 to run; skipping heavy venv check by default)"
fi

# --- 3. build ----------------------------------------------------------------
if [[ "${FORCE_CLEAN:-0}" == "1" && -d "$BUILD_DIR" ]]; then
    rocmfpx_log "FORCE_CLEAN=1 -> removing $BUILD_DIR"
    rm -rf "$BUILD_DIR"
fi

rocmfpx_runtime_env
rocmfpx_log "Building $ARCH via scripts/$build_script (JOBS=$JOBS, BUILD_DIR=$BUILD_DIR)"
env JOBS="$JOBS" BUILD_DIR="$BUILD_DIR" bash "$ROCMFPX_HOME/scripts/$build_script"

# --- 4. verify key binaries --------------------------------------------------
missing=0
for bin in llama-quantize llama-cli llama-server llama-bench; do
    if [[ -x "$BUILD_DIR/bin/$bin" ]]; then
        rocmfpx_log "ok: $BUILD_DIR/bin/$bin"
    else
        rocmfpx_err "missing expected binary: $BUILD_DIR/bin/$bin"
        missing=1
    fi
done
[[ "$missing" == "0" ]] || exit 1

rocmfpx_log "Build complete. QUANTIZE_BIN=$QUANTIZE_BIN"
