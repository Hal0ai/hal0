#!/bin/sh
# hal0 runner preflight — a device-less GPU start must be a readable diagnostic, not a SIGSEGV
# (#1936). Gates ONLY on an explicit GPU request: llama.cpp defaults to CPU when neither -ngl
# nor -dev is passed, so absence of those flags must never be treated as a GPU request.
wants_gpu=0
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
  esac
  prev="$a"
done

if [ "$wants_gpu" = "1" ]; then
  has_dev=0
  [ -e /dev/kfd ] && has_dev=1
  for r in /dev/dri/renderD*; do [ -e "$r" ] && has_dev=1; done
  if [ "$has_dev" = "0" ]; then
    echo "hal0-runner: GPU offload was requested but no GPU devices are visible in this container." >&2
    echo "hal0-runner: expected /dev/kfd (ROCm lane) and/or /dev/dri/renderD* (Vulkan lane)." >&2
    echo "hal0-runner: pass the devices through, or run a CPU slot (omit -ngl, or -ngl 0)." >&2
    echo "hal0-runner: refusing to start rather than crashing at model load (see hal0 issue #1936)." >&2
    exit 78
  fi
fi
exec /opt/rocmfpx/bin/llama-server "$@"
