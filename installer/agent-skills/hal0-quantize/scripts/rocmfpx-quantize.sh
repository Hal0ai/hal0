#!/usr/bin/env bash
# Quantize one BF16/F16 GGUF to a ROCmFPX family quant, using the arch build
# produced by rocmfpx-build.sh. Thin wrapper over the upstream
# scripts/quantize-rocmfpx-agent.sh — it does NOT reimplement quant logic;
# it just points the wrapper at the right BUILD_DIR and sets the runtime env.
#
# Usage:
#   SRC=/models/foo-BF16.gguf OUT=/models/foo-Q4_0_ROCMFP4_AGENT.gguf \
#     FORMAT=rocmfp4 PROFILE=agent scripts/rocmfpx-quantize.sh
#
# Required env:
#   SRC       BF16/F16/F32 GGUF input (split inputs kept split by default)
#   OUT       output GGUF path (or split-output prefix)
#
# Common env (passed straight through to the upstream wrapper):
#   FORMAT=rocmfp4    rocmfp3 | rocmfp4 | rocmfp6 | rocmfp8   (default rocmfp8)
#   PROFILE=agent     agent | straight                        (default agent)
#   KEEP_SPLIT=1      preserve input shard count when source is split
#   DRY_RUN=1         ask llama-quantize for the estimated output size only
#   NTHREADS=N        llama-quantize threads
#   IMATRIX=path      importance matrix (recommended for low-bit MoE)
#
# Arch/build env (see rocmfpx-env.sh): ARCH, ROCMFPX_HOME, BUILD_DIR, QUANTIZE_BIN
#
# Preset mapping (FORMAT x PROFILE -> llama-quantize preset):
#   rocmfp3 straight Q3_0_ROCMFPX     | rocmfp3 agent Q3_0_ROCMFPX_AGENT
#   rocmfp4 straight Q4_0_ROCMFP4     | rocmfp4 agent Q4_0_ROCMFP4_COHERENT
#   rocmfp6 straight Q6_0_ROCMFPX     | rocmfp6 agent Q6_0_ROCMFPX_AGENT
#   rocmfp8 straight Q8_0_ROCMFPX     | rocmfp8 agent Q8_0_ROCMFPX_AGENT

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/rocmfpx-env.sh"

SRC="${SRC:-}"
OUT="${OUT:-}"
FORMAT="${FORMAT:-rocmfp8}"
PROFILE="${PROFILE:-agent}"

if [[ -z "$SRC" || -z "$OUT" ]]; then
    rocmfpx_err "SRC and OUT are required. Example:"
    echo "  SRC=in-BF16.gguf OUT=out.gguf FORMAT=rocmfp4 PROFILE=agent $(basename "$0")" >&2
    exit 2
fi
case "$FORMAT" in rocmfp3|rocmfp4|rocmfp6|rocmfp8) ;; *)
    rocmfpx_err "FORMAT must be rocmfp3|rocmfp4|rocmfp6|rocmfp8 (got '$FORMAT')"; exit 2 ;;
esac
case "$PROFILE" in agent|straight) ;; *)
    rocmfpx_err "PROFILE must be agent|straight (got '$PROFILE')"; exit 2 ;;
esac
if [[ ! -f "$SRC" ]]; then
    rocmfpx_err "SRC not found: $SRC"; exit 2
fi

rocmfpx_resolve_build
rocmfpx_runtime_env

upstream="$ROCMFPX_HOME/scripts/quantize-rocmfpx-agent.sh"
if [[ ! -x "$upstream" ]]; then
    rocmfpx_err "upstream wrapper not found: $upstream"
    rocmfpx_err "run rocmfpx-build.sh first (or set ROCMFPX_HOME to an existing checkout)"
    exit 1
fi
if [[ ! -x "$QUANTIZE_BIN" ]]; then
    rocmfpx_err "llama-quantize not found: $QUANTIZE_BIN"
    rocmfpx_err "build the '$ARCH' tree first: ARCH=$ARCH scripts/rocmfpx-build.sh"
    exit 1
fi

mkdir -p "$(dirname "$OUT")"

rocmfpx_log "Quantizing ($ARCH): FORMAT=$FORMAT PROFILE=$PROFILE"
rocmfpx_log "  SRC=$SRC"
rocmfpx_log "  OUT=$OUT"
[[ -n "${IMATRIX:-}" ]] && rocmfpx_log "  IMATRIX=$IMATRIX"

exec env \
    ROOT="$ROCMFPX_HOME" \
    BUILD_DIR="$BUILD_DIR" \
    QUANTIZE_BIN="$QUANTIZE_BIN" \
    SRC="$SRC" OUT="$OUT" FORMAT="$FORMAT" PROFILE="$PROFILE" \
    KEEP_SPLIT="${KEEP_SPLIT:-1}" DRY_RUN="${DRY_RUN:-0}" \
    ${NTHREADS:+NTHREADS="$NTHREADS"} ${IMATRIX:+IMATRIX="$IMATRIX"} \
    bash "$upstream"
