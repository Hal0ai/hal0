#!/usr/bin/env bash
# profile-matrix.sh — the seed-profile re-tune benchmark matrix (handoff
# llamacpp-strix-halo-profile-consolidation-2026-07-04.md §6.3), scripted.
#
# Runs the llama-bench half (Tier A) of the matrix through the hal0-benchctl
# seam — so it works from the unprivileged hal0/agent user — and prints the
# server-level half (Tier B) as ready-to-run server_ab.py commands, since
# llama-bench cannot measure speculative decode, prompt-cache reuse, or the
# embed/rerank endpoints.
#
# Cells (Tier A, via `hal0-benchctl sweep`, whitelist-constrained):
#   moe-batch    : -b x -ub grid on the MoE flagship (rocm)   → rocm-moe -b/-ub
#   dense-batch  : -b x -ub grid on the dense 27B (rocm)      → rocm-dense -b/-ub
#   vulkan-ub    : -ub sweep on the dense model (vulkan_radv) → vulkan -ub
#   kv-rocm      : symmetric q8_0 vs f16 KV at 32k depth (rocm)
#   kv-vulkan    : same on vulkan_radv (verifies the "symmetric q8 is fine now"
#                  research claim on THIS build before shipping it as a doc'd
#                  operator override)
#   threads      : -t 8 vs 16 sanity (expected ~noise at full offload)
#
# The seam tags every sweep row "sweep"; cells are distinguished by their flag
# values in /var/lib/hal0/benchmarks/index.json (the meta JSON records the
# extra args per cell). Run `aggregate` at the end and read SUMMARY.md.
#
# Usage:
#   ./profile-matrix.sh [--cell name[,name...]] [--dry-run] [--no-exclusive]
#                       [--moe REL.gguf] [--dense REL.gguf]
#
# Models default to the current fleet's profile-class representatives; override
# per box. Paths are RELATIVE to /mnt/ai-models (seam requirement).
set -uo pipefail

SEAM="/usr/lib/hal0/bin/hal0-benchctl"

# Profile-class representative models (rel to /mnt/ai-models). Overridable.
MOE_MODEL="${MOE_MODEL:-qwen3.6-35b-a3b-crown-halo-mtp-dynamic/Qwen3.6-CROWN-35B-A3B-MTP-v7.gguf}"
DENSE_MODEL="${DENSE_MODEL:-qwen3.6-27b/Qwen3.6-27B-UD-Q5_K_XL.gguf}"

EXCLUSIVE="--exclusive"
DRYRUN=0
CELLS="moe-batch,dense-batch,vulkan-ub,kv-rocm,kv-vulkan,threads"

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
  echo
  echo "Cells: ${CELLS}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cell)         CELLS="$2"; shift 2;;
    --moe)          MOE_MODEL="$2"; shift 2;;
    --dense)        DENSE_MODEL="$2"; shift 2;;
    --dry-run)      DRYRUN=1; shift;;
    --no-exclusive) EXCLUSIVE=""; shift;;
    -h|--help)      usage; exit 0;;
    *) echo "unknown arg: $1" >&2; usage; exit 2;;
  esac
done

run_sweep() {  # <model> <backend> <flags...>
  local model="$1" backend="$2"; shift 2
  local cmd=(sudo -n "$SEAM" sweep "$model" "$backend")
  [[ -n "$EXCLUSIVE" ]] && cmd+=("$EXCLUSIVE")
  cmd+=("$@")
  echo "▶ ${cmd[*]}"
  [[ $DRYRUN -eq 1 ]] && return 0
  "${cmd[@]}"
}

has_cell() { [[ ",$CELLS," == *",$1,"* ]]; }

echo "== profile-matrix: Tier A (llama-bench via seam) =="
echo "   moe=$MOE_MODEL"
echo "   dense=$DENSE_MODEL"
echo

# rocm-moe batch shape: is -ub 4096 the MoE pp booster the community reports,
# or does 2048 hold on this build? (-b bounded by -ub grid; -p 2048 -n 64
# matches the hal0-tune house sweep shape.)
has_cell moe-batch && run_sweep "$MOE_MODEL" rocm \
  -b 2048,4096,8192 -ub 1024,2048,4096 -p 2048 -n 64

# rocm-dense batch shape (dense is less batch-sensitive; confirm 8192/2048).
has_cell dense-batch && run_sweep "$DENSE_MODEL" rocm \
  -b 2048,4096,8192 -ub 1024,2048,4096 -p 2048 -n 64

# vulkan profile -ub: RADV sweet spot is reported ~1024 (vs seeded 512).
has_cell vulkan-ub && run_sweep "$DENSE_MODEL" vulkan_radv \
  -ub 256,512,1024,2048 -p 2048 -n 64

# KV quant at depth: symmetric q8_0 vs f16 (asymmetric deliberately NOT swept —
# known-poison on both backends). -d 32768 puts the cache under real pressure.
has_cell kv-rocm && run_sweep "$DENSE_MODEL" rocm \
  -ctk q8_0,f16 -ctv q8_0,f16 -p 2048 -n 32 -d 32768
has_cell kv-vulkan && run_sweep "$DENSE_MODEL" vulkan_radv \
  -ctk q8_0,f16 -ctv q8_0,f16 -p 2048 -n 32 -d 32768

# Threads: expected within noise at full offload (#23659 swept 1..8 at ~1%);
# confirms dropping --threads-batch 32 (SMT) loses nothing.
has_cell threads && run_sweep "$DENSE_MODEL" rocm \
  -t 8,16 -p 2048 -n 64

if [[ $DRYRUN -eq 0 ]]; then
  echo "▶ sudo -n $SEAM aggregate"
  sudo -n "$SEAM" aggregate
fi

cat <<'EOF'

== profile-matrix: Tier B (server-level — run these next) ==
llama-bench cannot measure speculative decode, prompt-cache reuse, or the
embed/rerank endpoints. Use server_ab.py (same dir) against live slots:

  # MTP draft depth: upstream sweet spot is n-max 2-3; the seeded bundle uses 4.
  ./server_ab.py --mode ab --slot agent \
      --variant "n-max-2:--spec-draft-n-max 2" \
      --variant "n-max-4:--spec-draft-n-max 4"

  # Prompt-cache reuse for agentic serving (TTFT on a shared-prefix trace).
  ./server_ab.py --mode reuse --slot agent

  # Busy-wait poll tuning currently baked into the MTP profiles.
  ./server_ab.py --mode ab --slot agent \
      --variant "poll:--poll 100 --poll-batch 1" \
      --variant "no-poll:"

  # Embed / rerank serving sanity + latency (new seed profiles).
  ./server_ab.py --mode embed  --slot embed
  ./server_ab.py --mode rerank --slot rerank

Results: /var/lib/hal0/benchmarks/server-ab/*.json
Tier A results: /var/lib/hal0/benchmarks/SUMMARY.md + index.json (rows tagged "sweep")
EOF
