# ContainerProvider

> 102 nodes

## Key Concepts

- **ContainerProvider** (113 connections) — `src/hal0/providers/container.py`
- **TestLoadSync** (13 connections) — `tests/providers/test_container.py`
- **_render_from_spec()** (13 connections) — `tests/providers/test_container_npu.py`
- **test_container_npu.py** (12 connections) — `tests/providers/test_container_npu.py`
- **_flm_spec()** (12 connections) — `tests/providers/test_container_npu.py`
- **TestRenderUnitFromSpec** (12 connections) — `tests/providers/test_container_npu.py`
- **test_container_health_gating.py** (9 connections) — `tests/providers/test_container_health_gating.py`
- **._run()** (7 connections) — `src/hal0/providers/container.py`
- **_mock_client()** (7 connections) — `tests/providers/test_container_health_gating.py`
- **TestLoadSyncNpuBranch** (7 connections) — `tests/providers/test_container_npu.py`
- **.health()** (6 connections) — `src/hal0/providers/container.py`
- **.test_install_and_update_render_byte_identical_units()** (6 connections) — `tests/providers/test_container.py`
- **_json_resp()** (6 connections) — `tests/providers/test_container_health_gating.py`
- **test_health_404_v1_models_fallback_unchanged()** (6 connections) — `tests/providers/test_container_health_gating.py`
- **Path** (5 connections)
- **.test_load_sync_threads_ctx_size_and_extra_args()** (5 connections) — `tests/providers/test_container.py`
- **.test_load_sync_advertises_model_id_alias()** (5 connections) — `tests/providers/test_container.py`
- **test_container_dropin_cleanup.py** (5 connections) — `tests/providers/test_container_dropin_cleanup.py`
- **test_health_200_model_loading_not_ok()** (5 connections) — `tests/providers/test_container_health_gating.py`
- **test_health_200_model_loaded_ok()** (5 connections) — `tests/providers/test_container_health_gating.py`
- **test_health_200_non_json_body_stays_ok()** (5 connections) — `tests/providers/test_container_health_gating.py`
- **test_health_200_json_without_model_loaded_stays_ok()** (5 connections) — `tests/providers/test_container_health_gating.py`
- **_exec()** (5 connections) — `tests/providers/test_container_npu.py`
- **.test_gpu_slot_unaffected_by_npu_branch()** (5 connections) — `tests/providers/test_container_npu.py`
- **.is_active()** (4 connections) — `src/hal0/providers/container.py`
- *... and 77 more nodes in this community*

## Relationships

- [_resolve_llama_scalars](_resolve_llama_scalars.md) (18 shared connections)
- [resolve_profile_flags](resolve_profile_flags.md) (14 shared connections)
- [updater.py](updater.py.md) (11 shared connections)
- [Mount](Mount.md) (6 shared connections)
- [_llama_launch_plan](_llama_launch_plan.md) (6 shared connections)
- [FLMProvider](FLMProvider.md) (5 shared connections)
- [model_store_root](model_store_root.md) (5 shared connections)
- [_spec_provider_for](_spec_provider_for.md) (5 shared connections)
- [_container_runtime](_container_runtime.md) (4 shared connections)
- [test_slots_image_pull.py](test_slots_image_pull.py.md) (4 shared connections)
- [test_container_mmproj.py](test_container_mmproj.py.md) (4 shared connections)
- [_build_spec](_build_spec.md) (2 shared connections)

## Source Files

- `src/hal0/providers/container.py`
- `tests/providers/test_container.py`
- `tests/providers/test_container_dropin_cleanup.py`
- `tests/providers/test_container_health_gating.py`
- `tests/providers/test_container_npu.py`

## Audit Trail

- EXTRACTED: 336 (77%)
- INFERRED: 103 (23%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*