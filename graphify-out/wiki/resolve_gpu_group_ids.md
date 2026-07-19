# resolve_gpu_group_ids

> 12 nodes

## Key Concepts

- **resolve_gpu_group_ids()** (9 connections) — `src/hal0/providers/_gpu.py`
- **_gpu.py** (7 connections) — `src/hal0/providers/_gpu.py`
- **_probed_gpu_group_gids()** (3 connections) — `src/hal0/providers/_gpu.py`
- **is_nvidia_gpu_device()** (3 connections) — `src/hal0/providers/_gpu.py`
- **nvidia_cdi_devices()** (3 connections) — `src/hal0/providers/_gpu.py`
- **gpu_visibility_env()** (3 connections) — `src/hal0/providers/_gpu.py`
- **Shared helpers for GPU device + group exposure to provider containers.  Lives he** (1 connections) — `src/hal0/providers/_gpu.py`
- **GIDs `hal0 probe` recorded in hardware.json (``gpu_group_gids``).      Raw-JSON** (1 connections) — `src/hal0/providers/_gpu.py`
- **Return numeric GIDs for the host's GPU access groups (render, video).      Fallb** (1 connections) — `src/hal0/providers/_gpu.py`
- **True when a slot's declared device/profile selects the NVIDIA path.      Decided** (1 connections) — `src/hal0/providers/_gpu.py`
- **CDI device names for NVIDIA GPU passthrough.      ``--device nvidia.com/gpu=all`** (1 connections) — `src/hal0/providers/_gpu.py`
- **Visibility env a pinned slot needs, keyed by device family.      Returns ``{}``** (1 connections) — `src/hal0/providers/_gpu.py`

## Relationships

- [_resolve_llama_scalars](_resolve_llama_scalars.md) (4 shared connections)
- [resolve_profile_flags](resolve_profile_flags.md) (2 shared connections)
- [resolve_gpu_device_paths](resolve_gpu_device_paths.md) (1 shared connections)
- [ComfyUIProvider](ComfyUIProvider.md) (1 shared connections)
- [Qwen3TTSProvider](Qwen3TTSProvider.md) (1 shared connections)
- [Mount](Mount.md) (1 shared connections)

## Source Files

- `src/hal0/providers/_gpu.py`

## Audit Trail

- EXTRACTED: 25 (74%)
- INFERRED: 9 (26%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*