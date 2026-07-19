# SlotConfig

> 64 nodes · cohesion 0.05

## Key Concepts

- **SlotConfig** (75 connections) — `src/hal0/config/schema.py`
- **TestSlotConfig** (17 connections) — `tests/config/test_schema.py`
- **test_schema_npu.py** (10 connections) — `tests/config/test_schema_npu.py`
- **TestLegacyRuntimeRejected** (8 connections) — `tests/config/test_schema_migration.py`
- **Any** (7 connections)
- **TestSlotConfigDevice** (6 connections) — `tests/config/test_schema_migration.py`
- **._promote_backend_to_device()** (4 connections) — `src/hal0/config/schema.py`
- **._tuck_server_into_extra()** (4 connections) — `src/hal0/config/schema.py`
- **TestSlotConfigVisionField** (4 connections) — `tests/providers/test_container_vision_toggle.py`
- **._hoist_image_from_extra()** (3 connections) — `src/hal0/config/schema.py`
- **._hoist_npu_from_extra()** (3 connections) — `src/hal0/config/schema.py`
- **._hoist_server_from_extra()** (3 connections) — `src/hal0/config/schema.py`
- **test_seed_flm_toml_validates()** (3 connections) — `tests/config/test_schema_npu.py`
- **.test_extra_allow_keeps_unknown_keys()** (3 connections) — `tests/config/test_schema.py`
- **.test_invalid_device_raises_with_field_path()** (3 connections) — `tests/config/test_schema.py`
- **.test_minimum_valid()** (3 connections) — `tests/config/test_schema.py`
- **._name_grammar()** (2 connections) — `src/hal0/config/schema.py`
- **.rerank_fields_nonempty()** (2 connections) — `src/hal0/config/schema.py`
- **.test_container_slot_loads_clean()** (2 connections) — `tests/config/test_schema_migration.py`
- **.test_legacy_provider_rejected()** (2 connections) — `tests/config/test_schema_migration.py`
- **.test_legacy_runtime_rejected()** (2 connections) — `tests/config/test_schema_migration.py`
- **.test_provider_default_is_llama_server()** (2 connections) — `tests/config/test_schema_migration.py`
- **.test_runtime_default_is_container()** (2 connections) — `tests/config/test_schema_migration.py`
- **.test_runtime_rejects_unknown_values()** (2 connections) — `tests/config/test_schema_migration.py`
- **.test_default_device_is_gpu_rocm()** (2 connections) — `tests/config/test_schema_migration.py`
- *... and 39 more nodes in this community*

## Relationships

- [load_slot_config](load_slot_config.md) (12 shared connections)
- [SlotConfigError](SlotConfigError.md) (9 shared connections)
- [schema.py](schema.py.md) (5 shared connections)
- [test_schema_seeds_c5.py](test_schema_seeds_c5.py.md) (4 shared connections)
- [BrainChatConfig](BrainChatConfig.md) (2 shared connections)
- [CapabilitySelection](CapabilitySelection.md) (2 shared connections)
- [HonchoConfig](HonchoConfig.md) (1 shared connections)
- [load_manifest](load_manifest.md) (1 shared connections)
- [test_model_fallback.py](test_model_fallback.py.md) (1 shared connections)
- [unknown_slot_config_keys](unknown_slot_config_keys.md) (1 shared connections)
- [load_hal0_config](load_hal0_config.md) (1 shared connections)
- [recommend_primary_slot](recommend_primary_slot.md) (1 shared connections)

## Source Files

- `src/hal0/config/schema.py`
- `tests/config/test_schema.py`
- `tests/config/test_schema_migration.py`
- `tests/config/test_schema_npu.py`
- `tests/providers/test_container_vision_toggle.py`

## Audit Trail

- EXTRACTED: 168 (71%)
- INFERRED: 69 (29%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*