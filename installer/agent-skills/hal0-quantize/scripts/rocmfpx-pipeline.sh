#!/usr/bin/env bash
# End-to-end ROCmFPX pipeline: ensure the arch build exists (clone+build if
# needed), then quantize one BF16/F16 GGUF to a ROCmFPX family quant.
#
# One command, BF16 GGUF in -> ROCmFPX GGUF out:
#   SRC=/models/foo-BF16.gguf OUT=/models/foo-Q4_0_ROCMFP4_AGENT.gguf \
#     FORMAT=rocmfp4 PROFILE=agent scripts/rocmfpx-pipeline.sh
#
# Env: everything rocmfpx-build.sh + rocmfpx-quantize.sh accept.
#   REBUILD=1   force a fresh build even if binaries already exist
#
# Note: builds are native (no container / no read-only bind mount), so the
# output write always happens on a writable host path — the old hal0-quant-fc
# RO-mount `basic_ios::clear` failure does not apply to this pipeline.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/rocmfpx-env.sh"

rocmfpx_resolve_build

if [[ "${REBUILD:-0}" == "1" || ! -x "$QUANTIZE_BIN" ]]; then
    rocmfpx_log "Build required (REBUILD=${REBUILD:-0}, have QUANTIZE_BIN=$([[ -x "$QUANTIZE_BIN" ]] && echo yes || echo no))"
    bash "$SCRIPT_DIR/rocmfpx-build.sh"
else
    rocmfpx_log "Reusing existing build: $QUANTIZE_BIN"
fi

exec bash "$SCRIPT_DIR/rocmfpx-quantize.sh"
