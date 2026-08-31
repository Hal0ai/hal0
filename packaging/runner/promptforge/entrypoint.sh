#!/bin/sh
# hal0 runner preflight — a device-less GPU start must be a readable diagnostic, not a SIGSEGV
# (#1936). Gates ONLY on an explicit GPU request: llama.cpp defaults to CPU when neither -ngl
# nor -dev is passed, so absence of those flags must never be treated as a GPU request.
#
# Test seams (unset in the shipped image, harmless there):
#   HAL0_RUNNER_SERVER   — server binary (default /opt/promptforge/bin/llama-server)
#   HAL0_RUNNER_DEV_ROOT — prefix for the /dev probes, so the no-GPU branch is testable
#                          on a dev box that has real devices
SERVER="${HAL0_RUNNER_SERVER:-/opt/promptforge/bin/llama-server}"
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
# exit 64 for RestartPreventExitStatus= to act on; anything after load keeps
# its own code and the restart backoff runway.
#
# #2126 extends that to the DETERMINISTIC-FAULT signals — SIGILL (132),
# SIGABRT (134), SIGSEGV (139) — but only before /health ever answered, which
# is what makes them deterministic here: the binary never got a request, so
# the same load will reproduce the same fault every restart. A SIGILL is an
# ISA/image mismatch (#2126: a device=cpu slot launched from a GPU toolbox);
# a load-phase SIGSEGV is the shape #1790 saw when stock llama.cpp met a
# ROCmFPX quant's custom tensor type ids. Neither survives a retry.
#
# SIGKILL (137) is deliberately NOT in that set and still propagates: it is
# the OOM-killer (or an operator/podman teardown), which is genuinely
# transient — the whole point of keeping the backoff runway.
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

# Deterministic-fault signal deaths during load (#2126). Same translation,
# different evidence: the signal itself names the fault, so say which one and
# what it usually means before remapping.
if [ "$ready" = "0" ]; then
  case "$rc" in
    132) fault="SIGILL (illegal instruction)"
         hint="this image's llama-server contains CPU instructions this host cannot execute — an image/hardware mismatch, not a model problem (e.g. a device=cpu slot launched from a GPU toolbox)" ;;
    134) fault="SIGABRT"
         hint="the runtime aborted during model load — usually an unsupported model/quant or a failed allocation" ;;
    139) fault="SIGSEGV"
         hint="the runtime segfaulted during model load — usually a model file this build cannot parse (e.g. a ROCmFPX quant on a stock llama.cpp, hal0 issue #1790)" ;;
    *)   fault="" ;;
  esac
  if [ -n "$fault" ]; then
    echo "hal0-runner: llama-server was killed by ${fault} (rc=$rc) before /health ever answered." >&2
    echo "hal0-runner: ${hint}." >&2
    echo "hal0-runner: translating to exit 64 — restarting reproduces this fault exactly (hal0 issue #2126)." >&2
    exit 64
  fi
fi
exit "$rc"
