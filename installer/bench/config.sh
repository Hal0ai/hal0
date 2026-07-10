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
GPU_LABEL="${GPU_LABEL:-Radeon 8060S (gfx1151)}"

# --- Container runtime ------------------------------------------------------
# podman, NOT docker: `docker run` is AppArmor-blocked on hal0. Containers run
# rootful (the container is the sandbox boundary, per hal0's hardened model).
RUNTIME="${RUNTIME:-podman}"

# Device + security flags, lifted verbatim from the working production unit
# /etc/systemd/system/hal0-slot@agent.service (render gid 993, video gid 44).
COMMON_RUN_FLAGS=(
  --rm
  --device=/dev/kfd
  --device=/dev/dri/amdgpu
  --device=/dev/dri/renderD128
  --group-add=993                       # render
  --group-add=44                        # video
  --security-opt apparmor=unconfined
  --security-opt seccomp=unconfined
  --volume="${MODEL_DIR}:${MODEL_DIR}:ro,z"
)

# --- Backends ---------------------------------------------------------------
# key -> "image | bench_bin | ubatch | extra_env (space-separated KEY=VAL) | dev_args"
#
# Both lanes use the SAME unified runner image the production slots run
# (hal0-rocmfpx:vulkan-minicpm5): it ships ROCm + Vulkan backends AND loads
# every quant the slots can serve — including the custom ROCmFPX/FPX families
# that the stock toolboxes reject with "failed to load model" (verified
# on-box 2026-07-10 with Hal0-BRAINTRAIN-1B-FPX6-AGENT). Anything servable
# is now benchable. The image exposes BOTH devices, so each lane pins its
# device via dev_args (`llama-bench --list-devices`: ROCm0, Vulkan0).
declare -A BACKENDS=(
  [rocm]="ghcr.io/hal0ai/hal0-rocmfpx:vulkan-minicpm5|/usr/local/bin/llama-bench|2048|GGML_HIP_ENABLE_UNIFIED_MEMORY=1|-dev ROCm0"
  [vulkan_radv]="ghcr.io/hal0ai/hal0-rocmfpx:vulkan-minicpm5|/usr/local/bin/llama-bench|512||-dev Vulkan0"
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
