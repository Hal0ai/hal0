# resolve_gpu_device_paths

> 20 nodes · cohesion 0.11

## Key Concepts

- **resolve_gpu_device_paths()** (9 connections) — `src/hal0/providers/_gpu.py`
- **_gpu.py** (7 connections) — `src/hal0/providers/_gpu.py`
- **TestResolveGpuDevicePaths** (4 connections) — `tests/providers/test_gpu.py`
- **gpu_visibility_env()** (3 connections) — `src/hal0/providers/_gpu.py`
- **is_nvidia_gpu_device()** (3 connections) — `src/hal0/providers/_gpu.py`
- **nvidia_cdi_devices()** (3 connections) — `src/hal0/providers/_gpu.py`
- **_probed_gpu_group_gids()** (3 connections) — `src/hal0/providers/_gpu.py`
- **.test_enumerates_explicit_dri_nodes_not_the_directory()** (3 connections) — `tests/providers/test_gpu.py`
- **.test_falls_back_to_legacy_dirs_on_non_gpu_host()** (3 connections) — `tests/providers/test_gpu.py`
- **test_gpu.py** (2 connections) — `tests/providers/test_gpu.py`
- **.test_kfd_included_only_when_present()** (2 connections) — `tests/providers/test_gpu.py`
- **Shared helpers for GPU device + group exposure to provider containers.  Lives he** (1 connections) — `src/hal0/providers/_gpu.py`
- **True when a slot's declared device/profile selects the NVIDIA path.      Decided** (1 connections) — `src/hal0/providers/_gpu.py`
- **CDI device names for NVIDIA GPU passthrough.      ``--device nvidia.com/gpu=all`** (1 connections) — `src/hal0/providers/_gpu.py`
- **Visibility env a pinned slot needs, keyed by device family.      Returns ``{}``** (1 connections) — `src/hal0/providers/_gpu.py`
- **Return explicit GPU device-node paths to pass via ``--device=``.      Docker rec** (1 connections) — `src/hal0/providers/_gpu.py`
- **GIDs `hal0 probe` recorded in hardware.json (``gpu_group_gids``).      Raw-JSON** (1 connections) — `src/hal0/providers/_gpu.py`
- **Tests for GPU device-path resolution (podman/docker passthrough).  Podman cannot** (1 connections) — `tests/providers/test_gpu.py`
- **Char-device nodes under /dev/dri are listed explicitly; the bare         directo** (1 connections) — `tests/providers/test_gpu.py`
- **When neither /dev/kfd nor /dev/dri exist (CI / no-GPU dev box),         return t** (1 connections) — `tests/providers/test_gpu.py`

## Relationships

- [_resolve_llama_scalars](_resolve_llama_scalars.md) (4 shared connections)
- [ProfileConfig](ProfileConfig.md) (2 shared connections)
- [ComfyUIProvider](ComfyUIProvider.md) (1 shared connections)
- [Qwen3TTSProvider](Qwen3TTSProvider.md) (1 shared connections)
- [Mount](Mount.md) (1 shared connections)

## Source Files

- `src/hal0/providers/_gpu.py`
- `tests/providers/test_gpu.py`

## Audit Trail

- EXTRACTED: 38 (75%)
- INFERRED: 13 (25%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*