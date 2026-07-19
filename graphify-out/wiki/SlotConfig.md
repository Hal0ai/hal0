# SlotConfig

> 80 nodes

## Key Concepts

- **SlotConfig** (75 connections) — `src/hal0/config/schema.py`
- **TestSlotConfig** (17 connections) — `tests/config/test_schema.py`
- **test_schema_seeds_d1.py** (11 connections) — `tests/config/test_schema_seeds_d1.py`
- **test_schema_npu.py** (10 connections) — `tests/config/test_schema_npu.py`
- **ImageGenConfig** (8 connections) — `src/hal0/config/schema.py`
- **TestLegacyRuntimeRejected** (8 connections) — `tests/config/test_schema_migration.py`
- **Any** (7 connections)
- **NpuConfig** (6 connections) — `src/hal0/config/schema.py`
- **TestSlotConfigDevice** (6 connections) — `tests/config/test_schema_migration.py`
- **._promote_backend_to_device()** (4 connections) — `src/hal0/config/schema.py`
- **._tuck_server_into_extra()** (4 connections) — `src/hal0/config/schema.py`
- **TestSlotConfigVisionField** (4 connections) — `tests/providers/test_container_vision_toggle.py`
- **._hoist_server_from_extra()** (3 connections) — `src/hal0/config/schema.py`
- **._hoist_npu_from_extra()** (3 connections) — `src/hal0/config/schema.py`
- **._hoist_image_from_extra()** (3 connections) — `src/hal0/config/schema.py`
- **.test_minimum_valid()** (3 connections) — `tests/config/test_schema.py`
- **.test_invalid_device_raises_with_field_path()** (3 connections) — `tests/config/test_schema.py`
- **.test_extra_allow_keeps_unknown_keys()** (3 connections) — `tests/config/test_schema.py`
- **test_npu_chat_default_on()** (3 connections) — `tests/config/test_schema_npu.py`
- **test_seed_flm_toml_validates()** (3 connections) — `tests/config/test_schema_npu.py`
- **test_string_image_override_still_validates_and_round_trips()** (3 connections) — `tests/config/test_schema_seeds_d1.py`
- **.rerank_fields_nonempty()** (2 connections) — `src/hal0/config/schema.py`
- **._name_grammar()** (2 connections) — `src/hal0/config/schema.py`
- **.test_invalid_provider_raises()** (2 connections) — `tests/config/test_schema.py`
- **.test_port_below_range_raises()** (2 connections) — `tests/config/test_schema.py`
- *... and 55 more nodes in this community*

## Relationships

- [ConfigParseError](ConfigParseError.md) (14 shared connections)
- [SlotState](SlotState.md) (9 shared connections)
- [test_schema_seeds_c5.py](test_schema_seeds_c5.py.md) (4 shared connections)
- [BaseModel](BaseModel.md) (3 shared connections)
- [schema.py](schema.py.md) (3 shared connections)
- [unknown_slot_config_keys](unknown_slot_config_keys.md) (3 shared connections)
- [TestUpstreamEntry](TestUpstreamEntry.md) (3 shared connections)
- [CapabilitySelection](CapabilitySelection.md) (2 shared connections)
- [resolve_servable_model](resolve_servable_model.md) (1 shared connections)
- [recommend_primary_slot](recommend_primary_slot.md) (1 shared connections)
- [map_backend_to_device](map_backend_to_device.md) (1 shared connections)
- [HonchoConfig](HonchoConfig.md) (1 shared connections)

## Source Files

- `src/hal0/config/schema.py`
- `tests/config/test_schema.py`
- `tests/config/test_schema_migration.py`
- `tests/config/test_schema_npu.py`
- `tests/config/test_schema_seeds_d1.py`
- `tests/providers/test_container_vision_toggle.py`

## Audit Trail

- EXTRACTED: 203 (71%)
- INFERRED: 81 (29%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*