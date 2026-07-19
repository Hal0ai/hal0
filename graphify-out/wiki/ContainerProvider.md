# ContainerProvider

> 111 nodes · cohesion 0.03

## Key Concepts

- **ContainerProvider** (113 connections) — `src/hal0/providers/container.py`
- **TestLoadSync** (13 connections) — `tests/providers/test_container.py`
- **._render_quadlet_text()** (12 connections) — `src/hal0/providers/container.py`
- **test_container_health_gating.py** (9 connections) — `tests/providers/test_container_health_gating.py`
- **._unit_path()** (8 connections) — `src/hal0/providers/container.py`
- **.load_sync()** (7 connections) — `src/hal0/providers/container.py`
- **._run()** (7 connections) — `src/hal0/providers/container.py`
- **.unload_sync()** (7 connections) — `src/hal0/providers/container.py`
- **slot_instance_token()** (7 connections) — `src/hal0/slots/naming.py`
- **_mock_client()** (7 connections) — `tests/providers/test_container_health_gating.py`
- **TestLoadSyncNpuBranch** (7 connections) — `tests/providers/test_container_npu.py`
- **_container_runtime()** (6 connections) — `src/hal0/providers/container.py`
- **.health()** (6 connections) — `src/hal0/providers/container.py`
- **.rerender_unit_sync()** (6 connections) — `src/hal0/providers/container.py`
- **._unit_name()** (6 connections) — `src/hal0/providers/container.py`
- **._write_and_start_unit()** (6 connections) — `src/hal0/providers/container.py`
- **.__init__()** (6 connections) — `src/hal0/slots/migrate_id_keying.py`
- **slot_container_name()** (6 connections) — `src/hal0/slots/naming.py`
- **_json_resp()** (6 connections) — `tests/providers/test_container_health_gating.py`
- **test_health_404_v1_models_fallback_unchanged()** (6 connections) — `tests/providers/test_container_health_gating.py`
- **.test_install_and_update_render_byte_identical_units()** (6 connections) — `tests/providers/test_container.py`
- **naming.py** (5 connections) — `src/hal0/slots/naming.py`
- **test_container_dropin_cleanup.py** (5 connections) — `tests/providers/test_container_dropin_cleanup.py`
- **test_health_200_json_without_model_loaded_stays_ok()** (5 connections) — `tests/providers/test_container_health_gating.py`
- **test_health_200_model_loaded_ok()** (5 connections) — `tests/providers/test_container_health_gating.py`
- *... and 86 more nodes in this community*

## Relationships

- [_resolve_llama_scalars](_resolve_llama_scalars.md) (17 shared connections)
- [ConfigParseError](ConfigParseError.md) (11 shared connections)
- [ProfileConfig](ProfileConfig.md) (11 shared connections)
- [_render_from_spec](_render_from_spec.md) (10 shared connections)
- [_spec_provider_for](_spec_provider_for.md) (8 shared connections)
- [Mount](Mount.md) (7 shared connections)
- [_llama_launch_plan](_llama_launch_plan.md) (6 shared connections)
- [model_store_root](model_store_root.md) (5 shared connections)
- [TestContainerSpec](TestContainerSpec.md) (5 shared connections)
- [test_slots_image_pull.py](test_slots_image_pull.py.md) (4 shared connections)
- [test_container_mmproj.py](test_container_mmproj.py.md) (4 shared connections)
- [FLMProvider](FLMProvider.md) (3 shared connections)

## Source Files

- `src/hal0/providers/container.py`
- `src/hal0/slots/migrate_id_keying.py`
- `src/hal0/slots/naming.py`
- `tests/providers/test_container.py`
- `tests/providers/test_container_dropin_cleanup.py`
- `tests/providers/test_container_health_gating.py`
- `tests/providers/test_container_npu.py`

## Audit Trail

- EXTRACTED: 339 (74%)
- INFERRED: 119 (26%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*