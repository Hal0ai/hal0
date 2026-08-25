#!/bin/sh
# hal0 runner preflight — a device-less GPU start must be a readable diagnostic, not a SIGSEGV
# (#1936). Gates ONLY on an explicit GPU request: llama.cpp defaults to CPU when neither -ngl
# nor -dev is passed, so absence of those flags must never be treated as a GPU request.
#
# Test seams (unset in the shipped image, harmless there):
#   HAL0_RUNNER_SERVER   — server binary (default /opt/rocmfpx/bin/llama-server)
#   HAL0_RUNNER_DEV_ROOT — prefix for the /dev probes, so the no-GPU branch is testable
#                          on a dev box that has real devices
SERVER="${HAL0_RUNNER_SERVER:-/opt/rocmfpx/bin/llama-server}"
DEV_ROOT="${HAL0_RUNNER_DEV_ROOT:-}"

wants_gpu=0
port=8080
prev=""
for a in "$@"; do
  case "$prev" in
    -ngl|--n-gpu-layers|--gpu-layers)
      [ "$a" != "0" ] && wants_gpu=1
      ;;
    -dev|--device)
      case "$a" in
        ROCm*|rocm*|Vulkan*|vulkan*|CUDA*|cuda*|SYCL*|sycl*) wants_gpu=1 ;;
      esac
      ;;
    --port)
      port="$a"
      ;;
  esac
  # --port=NNNN (single-token form) — the template renders the two-token
  # form, but operator-edited profile flags may not.
  case "$a" in
    --port=*) port="${a#--port=}" ;;
  esac
  prev="$a"
done

if [ "$wants_gpu" = "1" ]; then
  has_dev=0
  [ -e "${DEV_ROOT}/dev/kfd" ] && has_dev=1
  for r in "${DEV_ROOT}"/dev/dri/renderD*; do [ -e "$r" ] && has_dev=1; done
  if [ "$has_dev" = "0" ]; then
    echo "hal0-runner: GPU offload was requested but no GPU devices are visible in this container." >&2
    echo "hal0-runner: expected /dev/kfd (ROCm lane) and/or /dev/dri/renderD* (Vulkan lane)." >&2
    echo "hal0-runner: pass the devices through, or run a CPU slot (omit -ngl, or -ngl 0)." >&2
    echo "hal0-runner: refusing to start rather than crashing at model load (see hal0 issue #1936)." >&2
    exit 78
  fi
fi

# Load-phase exit translation (#2037): llama-server exits 1 for every failure
# class, so systemd cannot fail-fast a doomed model. Supervise the child and
# watch /health — "loaded" means /health answered 200 at least once (during
# load llama-server either isn't listening yet or answers 503, so a bare TCP
# accept is not enough). A normal (non-signal) death before that point becomes
# exit 64 for RestartPreventExitStatus=64 to act on; signal deaths (OOM kill,
# GPU reset — possibly transient) and anything after load keep their own code
# and the restart backoff runway.
"$SERVER" "$@" &
child=$!
trap 'kill -TERM "$child" 2>/dev/null' TERM INT

ready=0
while kill -0 "$child" 2>/dev/null; do
  if curl -sf -o /dev/null "http://127.0.0.1:${port}/health"; then
    ready=1
    break
  fi
  sleep 0.5
done

# A trap interrupting wait returns 128+sig with the child unreaped — wait again
# until the child's own status comes back.
while :; do
  wait "$child"
  rc=$?
  kill -0 "$child" 2>/dev/null || break
done

if [ "$ready" = "0" ] && [ "$rc" -ge 1 ] && [ "$rc" -le 127 ]; then
  echo "hal0-runner: llama-server exited (rc=$rc) before /health ever answered — died during model load." >&2
  echo "hal0-runner: translating to exit 64 so systemd can fail-fast instead of burning the restart ramp (hal0 issue #2037)." >&2
  exit 64
fi
exit "$rc"
