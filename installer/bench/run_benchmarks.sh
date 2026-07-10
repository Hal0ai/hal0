#!/usr/bin/env bash
# Strix Halo benchmark sweep — runs llama-bench across backends (ROCm / Vulkan),
# models, and context configs via podman, writing one JSON result per cell.
#
# Ported from kyuz0/amd-strix-halo-toolboxes/benchmark/run_benchmarks.sh, with
# `toolbox run -c <name> -- <bin>` replaced by `podman run --entrypoint <bin>`
# against the images already present on hal0. Resumable: existing results are
# skipped. See config.sh for the backend/model/context matrix.
#
# Tuning: --extra "<verbatim llama-bench args>" + --tag <name> appends arbitrary
# flags (incl. comma-separated value sweeps, e.g. -ub 512,1024,2048) and labels
# the output, so the hal0-tune skill can drive sweeps through this same script.
set -uo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.sh
source "$HARNESS_DIR/config.sh"

# --- defaults ---------------------------------------------------------------
SEL_BACKENDS=("${BACKEND_ORDER[@]}")
SEL_CONTEXTS=(default)
EXCLUSIVE=0
FORCE=0
DRYRUN=0
ALL_MODELS=0
REPS_OVERRIDE=""
MODELS_ARG=""
EXTRA=""
TAG=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

  --models a,b,c     Comma-separated model paths relative to MODEL_DIR
                     (default: curated DEFAULT_MODELS in config.sh)
  --all-models       Sweep every *.gguf under MODEL_DIR (first shard only)
  --backends a,b     Backends to run (default: ${BACKEND_ORDER[*]})
  --contexts a,b     Context configs, or 'all' (default: default)
                     available: ${CTX_ORDER[*]}
  --reps N           Override repetitions for every cell
  --extra "ARGS"     Extra verbatim llama-bench args, appended to every cell.
                     Supports llama-bench value sweeps, e.g. --extra "-ub 512,1024,2048"
  --tag NAME         Label appended to result filenames (use with --extra to keep
                     tuning runs separate from baseline results)
  --exclusive        Stop active GPU inference slots for the run, then restart
                     them on exit (Tier-2: only use when no one is serving)
  --force            Run even if GPU slots are active (numbers WILL be skewed)
  --dry-run          Print the podman commands without running them
  -h, --help         This help

Results: \$RESULT_DIR/runs/<model>__<backend>[__ctx][__tag].json (+ .meta.json)
Then aggregate with: ./generate_results_json.py
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --models)      MODELS_ARG="$2"; shift 2;;
    --all-models)  ALL_MODELS=1; shift;;
    --backends)    IFS=',' read -ra SEL_BACKENDS <<<"$2"; shift 2;;
    --contexts)
      if [[ "$2" == "all" ]]; then SEL_CONTEXTS=("${CTX_ORDER[@]}");
      else IFS=',' read -ra SEL_CONTEXTS <<<"$2"; fi; shift 2;;
    --reps)        REPS_OVERRIDE="$2"; shift 2;;
    --extra)       EXTRA="$2"; shift 2;;
    --tag)         TAG="$2"; shift 2;;
    --exclusive)   EXCLUSIVE=1; shift;;
    --force)       FORCE=1; shift;;
    --dry-run)     DRYRUN=1; shift;;
    -h|--help)     usage; exit 0;;
    *) echo "unknown arg: $1" >&2; usage; exit 2;;
  esac
done

mkdir -p "$RUNS_DIR" "$LOG_DIR"
tagsuffix=""; [[ -n "$TAG" ]] && tagsuffix="__$(printf '%s' "$TAG" | tr -c 'A-Za-z0-9._-' '_')"

# --- GPU-idle preflight -----------------------------------------------------
# The production hal0-slot@agent inference slot shares this iGPU. Running
# llama-bench against a busy GPU produces meaningless (contended) numbers, so
# by default we refuse. --exclusive stops/restarts the GPU slots; --force runs
# anyway. The NPU slot (hal0-slot@npu) does not touch the GPU and is ignored.
gpu_slots_active() {
  systemctl list-units 'hal0-slot@*' --no-legend --state=active 2>/dev/null \
    | awk '{print $1}' | grep -v '^hal0-slot@npu' || true
}

STOPPED_SLOTS=()
restore_slots() {
  local s
  for s in "${STOPPED_SLOTS[@]:-}"; do
    [[ -n "$s" ]] || continue
    echo "[exclusive] restarting $s" >&2
    systemctl start "$s" || echo "[exclusive] WARN: failed to restart $s" >&2
  done
}

active="$(gpu_slots_active)"
if [[ -n "$active" ]]; then
  if [[ $EXCLUSIVE -eq 1 ]]; then
    trap restore_slots EXIT INT TERM
    while read -r s; do
      [[ -n "$s" ]] || continue
      echo "[exclusive] stopping GPU slot: $s"
      systemctl stop "$s" || { echo "could not stop $s, aborting" >&2; exit 1; }
      STOPPED_SLOTS+=("$s")
    done <<<"$active"
    sleep 3
  elif [[ $FORCE -eq 1 ]]; then
    echo "[warn] GPU slots active; --force set — results will be contended:" >&2
    echo "$active" >&2
  else
    echo "[abort] GPU inference slots are active; results would be skewed:" >&2
    echo "$active" | sed 's/^/    /' >&2
    echo "Re-run with --exclusive (stop+restart them) or --force (accept contention)." >&2
    exit 1
  fi
fi

# --- resolve model list -----------------------------------------------------
declare -a MODELS=()
if [[ -n "$MODELS_ARG" ]]; then
  IFS=',' read -ra MODELS <<<"$MODELS_ARG"
elif [[ $ALL_MODELS -eq 1 ]]; then
  while IFS= read -r f; do MODELS+=("${f#"$MODEL_DIR"/}"); done < <(
    find "$MODEL_DIR" -type f -name '*.gguf' \
      \( -name '*-00001-of-*.gguf' -o -not -name '*-0*-of-*.gguf' \) | sort)
else
  MODELS=("${DEFAULT_MODELS[@]}")
fi
[[ ${#MODELS[@]} -gt 0 ]] || { echo "no models to benchmark" >&2; exit 1; }

echo "Backends : ${SEL_BACKENDS[*]}"
echo "Contexts : ${SEL_CONTEXTS[*]}"
echo "Models   : ${#MODELS[@]}"
[[ -n "$EXTRA" ]] && echo "Extra    : $EXTRA${TAG:+  (tag=$TAG)}"
echo "Results  : $RESULT_DIR"
echo

# --- sweep ------------------------------------------------------------------
fail_count=0 run_count=0 skip_count=0
for backend in "${SEL_BACKENDS[@]}"; do
  spec="${BACKENDS[$backend]:-}"
  [[ -n "$spec" ]] || { echo "[skip] unknown backend: $backend" >&2; continue; }
  IFS='|' read -r image bench_bin ubatch envstr devargs <<<"$spec"
  env_flags=()
  for kv in $envstr; do env_flags+=(-e "$kv"); done

  for rel in "${MODELS[@]}"; do
    model_path="$MODEL_DIR/$rel"
    if [[ ! -f "$model_path" ]]; then
      echo "[skip] missing model: $model_path" >&2; continue
    fi
    mstem="$(basename "$rel" .gguf)"
    msan="$(printf '%s' "$mstem" | tr -c 'A-Za-z0-9._-' '_')"

    for ctx in "${SEL_CONTEXTS[@]}"; do
      cspec="${CTX_CONFIGS[$ctx]:-}"
      [[ -n "$cspec" ]] || { echo "[skip] unknown ctx: $ctx" >&2; continue; }
      IFS='|' read -r ctxargs reps <<<"$cspec"
      [[ -n "$REPS_OVERRIDE" ]] && reps="$REPS_OVERRIDE"
      ctxargs="${ctxargs//%UB%/$ubatch}"
      ctxsuffix=""; [[ "$ctx" != "default" ]] && ctxsuffix="__$ctx"
      base="${msan}__${backend}${ctxsuffix}${tagsuffix}"
      out="$RUNS_DIR/${base}.json"
      meta="$RUNS_DIR/${base}.meta.json"
      log="$LOG_DIR/${base}.log"

      if [[ -s "$out" ]]; then
        echo "[skip exists] $(basename "$out")"; ((skip_count++)); continue
      fi

      # shellcheck disable=SC2206  # intentional word-split of ctxargs / EXTRA / devargs
      cmd=("$RUNTIME" run "${COMMON_RUN_FLAGS[@]}" "${env_flags[@]}"
           --entrypoint "$bench_bin" "$image"
           -m "$model_path" "${COMMON_BENCH_ARGS[@]}" $devargs $ctxargs $EXTRA -r "$reps" -o json)

      echo "[run] $backend / $mstem / $ctx${TAG:+ / $TAG} (reps=$reps)"
      if [[ $DRYRUN -eq 1 ]]; then printf '   '; printf '%q ' "${cmd[@]}"; echo; continue; fi

      ts="$(date -Iseconds)"
      # The rocmfpx runner's llama-bench has a flaky init-time crash (SIGSEGV/
      # GPF in libllama BEFORE any measurement — ~2/3 of launches, observed
      # on-box 2026-07-10; llama-server in the same image is unaffected). A
      # signal exit (rc>=128) therefore retries without biasing numbers — no
      # rep ever ran. Real errors (rc<128, e.g. failed-to-load) never retry.
      rc=1
      for attempt in 1 2 3 4 5 6; do
        "${cmd[@]}" >"$out" 2>"$log"; rc=$?
        if (( rc == 0 )) && [[ -s "$out" ]]; then break; fi
        # rc 0 with EMPTY output is the same crash: the container died to the
        # SIGSEGV but `podman run --rm` raced its own cleanup and lost the real
        # exit code (observed on-box 2026-07-10: kernel logged the segfault,
        # podman returned 0, the JSON was 0 bytes). Normalize to a signal exit.
        (( rc == 0 )) && rc=139
        (( rc >= 128 )) || break
        echo "   [retry $attempt/6] llama-bench crashed before emitting results (rc=$rc) — relaunching" >&2
        sleep 1
      done
      if [[ $rc -eq 0 ]]; then
        extra_json="$(printf '%s' "$EXTRA" | sed 's/\\/\\\\/g; s/"/\\"/g')"
        cat >"$meta" <<META
{"backend":"$backend","image":"$image","context":"$ctx","tag":"$TAG","extra":"$extra_json","reps":$reps,"ubatch":$ubatch,"model_rel":"$rel","model_path":"$model_path","host":"$HOST_LABEL","gpu":"$GPU_LABEL","timestamp":"$ts"}
META
        echo "   -> $(basename "$out")"; ((run_count++))
      else
        echo "   [FAIL] see $(basename "$log")" >&2
        mv "$out" "${out}.failed" 2>/dev/null || true
        ((fail_count++))
      fi
    done
  done
done

echo
echo "Done. ran=$run_count skipped=$skip_count failed=$fail_count"
echo "Aggregate: $HARNESS_DIR/generate_results_json.py"
[[ $fail_count -eq 0 ]]
