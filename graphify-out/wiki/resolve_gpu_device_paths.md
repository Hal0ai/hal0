# resolve_gpu_device_paths

> 10 nodes

## Key Concepts

- **resolve_gpu_device_paths()** (9 connections) — `src/hal0/providers/_gpu.py`
- **TestResolveGpuDevicePaths** (4 connections) — `tests/providers/test_gpu.py`
- **.test_enumerates_explicit_dri_nodes_not_the_directory()** (3 connections) — `tests/providers/test_gpu.py`
- **.test_falls_back_to_legacy_dirs_on_non_gpu_host()** (3 connections) — `tests/providers/test_gpu.py`
- **test_gpu.py** (2 connections) — `tests/providers/test_gpu.py`
- **.test_kfd_included_only_when_present()** (2 connections) — `tests/providers/test_gpu.py`
- **Return explicit GPU device-node paths to pass via ``--device=``.      Docker rec** (1 connections) — `src/hal0/providers/_gpu.py`
- **Tests for GPU device-path resolution (podman/docker passthrough).  Podman cannot** (1 connections) — `tests/providers/test_gpu.py`
- **Char-device nodes under /dev/dri are listed explicitly; the bare         directo** (1 connections) — `tests/providers/test_gpu.py`
- **When neither /dev/kfd nor /dev/dri exist (CI / no-GPU dev box),         return t** (1 connections) — `tests/providers/test_gpu.py`

## Relationships

- [resolve_gpu_group_ids](resolve_gpu_group_ids.md) (1 shared connections)
- [ComfyUIProvider](ComfyUIProvider.md) (1 shared connections)
- [_resolve_llama_scalars](_resolve_llama_scalars.md) (1 shared connections)
- [Qwen3TTSProvider](Qwen3TTSProvider.md) (1 shared connections)
- [Mount](Mount.md) (1 shared connections)

## Source Files

- `src/hal0/providers/_gpu.py`
- `tests/providers/test_gpu.py`

## Audit Trail

- EXTRACTED: 17 (63%)
- INFERRED: 10 (37%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*