#!/usr/bin/env bash
# Strix Halo benchmark harness — central configuration (hal0 / CT105).
#
# Sourced by run_benchmarks.sh. This is the single place that encodes the
# hal0-specific facts: which container images provide each backend, where the
# llama-bench binary lives inside them, the podman device/security flags, and
# the model/context sweep matrix. Edit HERE to add a backend, model, or context.
#
# Ownership model (D hardened-perms): this harness is installed root-owned and
# is invoked as root only via the /usr/lib/hal0/bin/hal0-benchctl seam. Results
# go to a hal0-owned dir so the agent + UI can read them.

# --- Host / paths -----------------------------------------------------------
MODEL_DIR="${MODEL_DIR:-/mnt/ai-models}"
HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULT_DIR="${RESULT_DIR:-/var/lib/hal0/benchmarks}"   # hal0:hal0, agent-readable
RUNS_DIR="$RESULT_DIR/runs"      # one llama-bench JSON (+ .meta.json) per cell
LOG_DIR="$RESULT_DIR/logs"       # stderr / errors per cell
HOST_LABEL="${HOST_LABEL:-hal0}"

# --- Container runtime ------------------------------------------------------
# podman, NOT docker: `docker run` is AppArmor-blocked on hal0. Containers run
# rootful (the container is the sandbox boundary, per hal0's hardened model).
RUNTIME="${RUNTIME:-podman}"

# --- GPU passthrough (issue #1303) ------------------------------------------
# NOTHING about the GPU is hardcoded here any more. This file used to carry
#   --device=/dev/kfd --device=/dev/dri/amdgpu --device=/dev/dri/renderD128
#   --group-add=993 --group-add=44
# lifted from one host's hal0-slot@agent.service. `/dev/dri/amdgpu` is not a
# kernel-conventional node name — a stock LXC exposes card1 + renderD128 and
# every queued cell died with `stat /dev/dri/amdgpu: no such file or
# directory`, while the production slots on the same box ran fine.
#
# The nodes + render/video GIDs now come from the SAME resolver the production
# slot containers use: hal0.bench.devices -> hal0.providers._gpu
# (resolve_gpu_device_paths / resolve_gpu_group_ids) + the `hal0 probe`
# hardware.json snapshot. One source of truth, four hardware tiers:
#
#   AMD    -> /dev/kfd + the real /dev/dri char devices + real GIDs
#   NVIDIA -> the CDI name nvidia.com/gpu=all (no paths, no --group-add)
#   CPU    -> NO device flags at all; a CPU-tier run must not need a DRI node
#   custom -> HAL0_BENCH_{GPU_DEVICES,KFD,CARD,RENDER}_DEVICE / _GPU_GROUPS
#             overrides for unusual passthrough layouts and recovery
#
# The resolver emits a KEY=VALUE block that is PARSED (never `eval`ed) and
# every flag is re-validated below against the allowed device-node shapes and
# confirmed to be a character device — this file is sourced by the root-run
# hal0-benchctl seam, so it must not widen that seam.
BENCH_TIER=""
BENCH_DEVICE_SOURCE=""
BENCH_CARD_NODE=""
BENCH_RENDER_NODE=""
BENCH_DEVICE_FLAGS=()
_BENCH_GPU_LABEL=""

_bench_python() {
  local candidate
  for candidate in \
      "${HAL0_BENCH_PYTHON:-}" \
      "${HAL0_FHS_ROOT:-/usr/lib/hal0}/venv/bin/python3" \
      "/usr/lib/hal0/venv/bin/python3" \
      "$(command -v python3 2>/dev/null || true)"; do
    [[ -n "$candidate" && -x "$candidate" ]] && { printf '%s' "$candidate"; return 0; }
  done
  return 1
}

# Permitted device roots — the mirror of hal0.bench.devices._node_allowed.
# Defaults are the real /dev roots; the two seams relocate them in lock-step
# with the resolver (tests, unusual passthrough layouts) so an override is
# still CHECKED rather than waved through.
_BENCH_KFD_ROOT="${HAL0_BENCH_KFD_PATH:-/dev/kfd}"
_BENCH_DRI_ROOT="${HAL0_BENCH_DRI_DIR:-/dev/dri}"

# Reject anything that is not an allowed device node / numeric group id, and
# re-confirm S_ISCHR on THIS host. This file is sourced by the root-run
# hal0-benchctl seam, so the resolver's output must not be able to widen it.
_bench_valid_flag() {
  local flag="$1" node name
  [[ "$flag" != *..* ]] || return 1
  case "$flag" in
    --group-add=*) [[ "${flag#--group-add=}" =~ ^[0-9]+$ ]]; return $? ;;
    --device=nvidia.com/gpu=*)
      [[ "${flag#--device=nvidia.com/gpu=}" =~ ^([0-9]+|all)$ ]]; return $? ;;
    --device=*) node="${flag#--device=}" ;;
    *) return 1 ;;
  esac
  if [[ "$node" != "$_BENCH_KFD_ROOT" ]]; then
    case "$node" in
      "${_BENCH_DRI_ROOT}"/*) name="${node#"${_BENCH_DRI_ROOT}"/}" ;;
      /dev/accel/*)           name="${node#/dev/accel/}" ;;
      *) return 1 ;;
    esac
    [[ "$name" =~ ^[A-Za-z0-9_.:+-]+$ ]] || return 1
  fi
  [[ -c "$node" ]]
}

_bench_resolve_devices() {
  local py out rc line flag
  if ! py="$(_bench_python)"; then
    echo "bench/config.sh: no python interpreter found to resolve GPU devices" >&2
    echo "  set HAL0_BENCH_PYTHON=/path/to/python3 (hal0 venv)" >&2
    return 1
  fi
  out="$("$py" -m hal0.bench.devices --format env 2>&1)"; rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "bench/config.sh: GPU device resolution failed (rc=$rc):" >&2
    printf '  %s\n' "$out" >&2
    return 1
  fi
  while IFS= read -r line; do
    case "$line" in
      BENCH_TIER=*)          BENCH_TIER="${line#*=}" ;;
      BENCH_DEVICE_SOURCE=*) BENCH_DEVICE_SOURCE="${line#*=}" ;;
      BENCH_GPU_LABEL=*)     _BENCH_GPU_LABEL="${line#*=}" ;;
      BENCH_CARD_NODE=*)     BENCH_CARD_NODE="${line#*=}" ;;
      BENCH_RENDER_NODE=*)   BENCH_RENDER_NODE="${line#*=}" ;;
      BENCH_RUN_FLAG=*)
        flag="${line#*=}"
        if ! _bench_valid_flag "$flag"; then
          echo "bench/config.sh: refusing unusable device flag from resolver: $flag" >&2
          return 1
        fi
        BENCH_DEVICE_FLAGS+=("$flag")
        ;;
      "") ;;
    esac
  done <<<"$out"
  return 0
}

# Hard preflight: a benchmark queued against unusable device settings must
# fail HERE (before any container starts), with the checked paths named --
# never by silently substituting a node that does not exist. `exit` (not
# `return`) because this file is sourced: the sweep must not proceed.
if ! _bench_resolve_devices; then
  echo "bench/config.sh: cannot resolve GPU device nodes for benchmark containers — aborting" >&2
  exit 1
fi

# GPU label stamped into every result's .meta.json. Probed, so a run on the
# NVIDIA or CPU tier is never mislabelled as the Strix Halo iGPU (which would
# silently corrupt the published per-tier baselines).
if [[ -z "${GPU_LABEL:-}" ]]; then
  if [[ -n "$_BENCH_GPU_LABEL" ]]; then
    GPU_LABEL="$_BENCH_GPU_LABEL"
  elif [[ "$BENCH_TIER" == "cpu" ]]; then
    GPU_LABEL="CPU (no GPU passthrough)"
  else
    GPU_LABEL="unknown GPU"
  fi
fi

COMMON_RUN_FLAGS=(
  --rm
  ${BENCH_DEVICE_FLAGS[@]+"${BENCH_DEVICE_FLAGS[@]}"}
  --security-opt apparmor=unconfined
  --security-opt seccomp=unconfined
  --volume="${MODEL_DIR}:${MODEL_DIR}:ro,z"
)

# --- Backends ---------------------------------------------------------------
# key -> "image | bench_bin | ubatch | extra_env (space-separated KEY=VAL) | dev_args"
#
# Both lanes use the SAME unified runner image the production slots run —
# DEFAULT_ROCMFPX_IMAGE in src/hal0/config/schema.py; keep the two in sync
# when bumping. It ships ROCm + Vulkan backends AND loads every quant the
# slots can serve, including the ROCmFPX/FPX families the stock toolboxes
# reject (verified on-box 2026-07-10). Anything servable is benchable. The
# image exposes BOTH devices, so each lane pins its device via dev_args
# (`llama-bench --list-devices`: ROCm0, Vulkan0).
#
# bench_bin is /opt/rocmfpx/bin/llama-bench — the binary matched to the
# ROCmFPX libllama. (Pre-c077206 images also carried the base toolbox's
# stale /usr/local/bin/llama-bench, which LD_LIBRARY_PATH pointed at the
# new libs — an ABI mismatch that segfaulted ~2/3 of launches. c077206
# removes the stale install; the /opt path works on every image vintage.)
declare -A BACKENDS=(
  [rocm]="ghcr.io/hal0ai/hal0-rocmfpx:c077206|/opt/rocmfpx/bin/llama-bench|2048|GGML_HIP_ENABLE_UNIFIED_MEMORY=1|-dev ROCm0"
  [vulkan_radv]="ghcr.io/hal0ai/hal0-rocmfpx:c077206|/opt/rocmfpx/bin/llama-bench|512||-dev Vulkan0"
)
# Order backends are swept in.
BACKEND_ORDER=(rocm vulkan_radv)

# --- Context configurations -------------------------------------------------
# key -> "extra llama-bench args (%UB% = per-backend ubatch) | repetitions"
# default = stock pp512/tg128; long contexts mirror kyuz0's 32K/65K sweeps.
declare -A CTX_CONFIGS=(
  [default]="|5"
  [ctx32k]="-p 2048 -n 32 -d 32768 -ub %UB%|3"
  [ctx65k]="-p 2048 -n 32 -d 65536 -ub %UB%|3"
)
CTX_ORDER=(default ctx32k ctx65k)

# --- Common llama-bench args (applied to every cell) ------------------------
#  -ngl 99  : offload all layers to GPU
#  -fa 1    : flash attention on
#  -mmp 0   : no mmap (matches production serving + kyuz0 harness)
COMMON_BENCH_ARGS=(-ngl 99 -fa 1 -mmp 0)

# --- Default curated model set (paths relative to MODEL_DIR) ----------------
# Deliberately small (one per size class) so a default run is quick. Use
# --all-models to sweep every GGUF, or --models a,b,c to pick specific ones.
DEFAULT_MODELS=(
  "qwen3.5-0.8b/Qwen3.5-0.8B-UD-Q4_K_XL.gguf"
  "qwopus3.5-4b-coder-mtp/Qwopus3.5-4B-Coder-MTP-Q6_K.gguf"
  "gemma-4-12B-agentic-fable5/gemma4-v2-Q4_K_M.gguf"
  "qwen3.6-27b/Qwen3.6-27B-UD-Q5_K_XL.gguf"
)
