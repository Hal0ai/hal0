# ComfyUIProvider

> 80 nodes · cohesion 0.04

## Key Concepts

- **ComfyUIProvider** (40 connections) — `src/hal0/providers/comfyui.py`
- **test_comfyui.py** (20 connections) — `tests/providers/test_comfyui.py`
- **test_comfyui_container_spec.py** (15 connections) — `tests/providers/test_comfyui_container_spec.py`
- **.container_spec()** (11 connections) — `src/hal0/providers/comfyui.py`
- **ComfyUIInferError** (10 connections) — `src/hal0/providers/comfyui.py`
- **_img_cfg()** (10 connections) — `tests/providers/test_comfyui_container_spec.py`
- **Any** (9 connections)
- **.infer()** (8 connections) — `src/hal0/providers/comfyui.py`
- **Any** (7 connections)
- **comfyui.py** (6 connections) — `src/hal0/providers/comfyui.py`
- **._await_history()** (6 connections) — `src/hal0/providers/comfyui.py`
- **ComfyUIHealthError** (5 connections) — `src/hal0/providers/comfyui.py`
- **._fetch_view()** (5 connections) — `src/hal0/providers/comfyui.py`
- **._profile_flags()** (5 connections) — `src/hal0/providers/comfyui.py`
- **.health()** (4 connections) — `src/hal0/providers/comfyui.py`
- **AsyncClient** (4 connections)
- **_render_from_spec()** (4 connections) — `tests/providers/test_comfyui_container_spec.py`
- **test_comfyui_data_root_env_override()** (4 connections) — `tests/providers/test_comfyui_container_spec.py`
- **test_comfyui_extra_model_paths_mount_is_read_only()** (4 connections) — `tests/providers/test_comfyui_container_spec.py`
- **test_comfyui_profile_flags_fallback_without_profile()** (4 connections) — `tests/providers/test_comfyui_container_spec.py`
- **test_renderer_host_network_skips_publish_and_keeps_shm()** (4 connections) — `tests/providers/test_comfyui_container_spec.py`
- **test_container_spec_passes_gpu_device_nodes()** (4 connections) — `tests/providers/test_comfyui.py`
- **test_infer_surfaces_workflow_error()** (4 connections) — `tests/providers/test_comfyui.py`
- **.build_env()** (3 connections) — `src/hal0/providers/comfyui.py`
- **_gpu_nodes()** (3 connections) — `tests/providers/test_comfyui_container_spec.py`
- *... and 55 more nodes in this community*

## Relationships

- [Mount](Mount.md) (5 shared connections)
- [_spec_provider_for](_spec_provider_for.md) (4 shared connections)
- [Provider](Provider.md) (3 shared connections)
- [get_runner](get_runner.md) (3 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [model_store_root](model_store_root.md) (1 shared connections)
- [resolve_gpu_device_paths](resolve_gpu_device_paths.md) (1 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)
- [build_workflow](build_workflow.md) (1 shared connections)
- [ProfileCatalog](ProfileCatalog.md) (1 shared connections)

## Source Files

- `src/hal0/providers/comfyui.py`
- `tests/providers/test_comfyui.py`
- `tests/providers/test_comfyui_container_spec.py`

## Audit Trail

- EXTRACTED: 258 (87%)
- INFERRED: 39 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*