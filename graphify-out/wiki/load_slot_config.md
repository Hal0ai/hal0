# load_slot_config

> 56 nodes · cohesion 0.05

## Key Concepts

- **load_slot_config()** (29 connections) — `src/hal0/config/loader.py`
- **TestSlotConfigRoundTrip** (19 connections) — `tests/config/test_loader.py`
- **save_slot_config()** (17 connections) — `src/hal0/config/loader.py`
- **test_schema_seeds_d1.py** (11 connections) — `tests/config/test_schema_seeds_d1.py`
- **ImageGenConfig** (8 connections) — `src/hal0/config/schema.py`
- **.test_server_extra_args_round_trips()** (5 connections) — `tests/config/test_loader.py`
- **test_load_slot_config_string_image_survives_in_extra()** (5 connections) — `tests/config/test_schema_seeds_d1.py`
- **test_installer_slot_load_save_load_is_stable()** (5 connections) — `tests/config/test_seeds_roundtrip.py`
- **test_installer_slot_loads_unchanged()** (5 connections) — `tests/config/test_seeds_roundtrip.py`
- **.test_load_preserves_extra_sections()** (4 connections) — `tests/config/test_loader.py`
- **.test_load_with_invalid_device_raises_with_field_path()** (4 connections) — `tests/config/test_loader.py`
- **.test_save_then_load()** (4 connections) — `tests/config/test_loader.py`
- **.test_server_env_loads_and_round_trips()** (4 connections) — `tests/config/test_loader.py`
- **.test_slot_toml_on_disk_has_nested_sections()** (4 connections) — `tests/config/test_loader.py`
- **.test_unset_server_extra_args_does_not_write_empty_table()** (4 connections) — `tests/config/test_loader.py`
- **test_save_load_round_trip_preserves_image_gen()** (4 connections) — `tests/config/test_schema_seeds_d1.py`
- **test_seeds_roundtrip.py** (4 connections) — `tests/config/test_seeds_roundtrip.py`
- **.test_list_slots_returns_stems_sorted()** (3 connections) — `tests/config/test_loader.py`
- **.test_load_flat_slot_toml_hoists_sibling_tables()** (3 connections) — `tests/config/test_loader.py`
- **.test_load_flat_slot_toml_infers_name_from_filename()** (3 connections) — `tests/config/test_loader.py`
- **.test_load_flat_top_level_slot_toml()** (3 connections) — `tests/config/test_loader.py`
- **.test_load_with_legacy_backend_only_promotes_to_device()** (3 connections) — `tests/config/test_loader.py`
- **.test_server_extra_args_loads_from_toml()** (3 connections) — `tests/config/test_loader.py`
- **.test_server_extra_args_missing_defaults_to_none()** (3 connections) — `tests/config/test_loader.py`
- **Path** (3 connections)
- *... and 31 more nodes in this community*

## Relationships

- [SlotConfig](SlotConfig.md) (12 shared connections)
- [load_hal0_config](load_hal0_config.md) (11 shared connections)
- [ConfigParseError](ConfigParseError.md) (3 shared connections)
- [load_manifest](load_manifest.md) (3 shared connections)
- [schema.py](schema.py.md) (2 shared connections)
- [CapabilitySelection](CapabilitySelection.md) (1 shared connections)
- [_write_diagnostics_section](_write_diagnostics_section.md) (1 shared connections)
- [test_doctor_profiles.py](test_doctor_profiles.py.md) (1 shared connections)
- [ProfileCatalog](ProfileCatalog.md) (1 shared connections)
- [heal_missing_llm_type](heal_missing_llm_type.md) (1 shared connections)
- [unknown_slot_config_keys](unknown_slot_config_keys.md) (1 shared connections)

## Source Files

- `src/hal0/config/loader.py`
- `src/hal0/config/schema.py`
- `tests/config/test_loader.py`
- `tests/config/test_schema_seeds_d1.py`
- `tests/config/test_seeds_roundtrip.py`

## Audit Trail

- EXTRACTED: 125 (62%)
- INFERRED: 76 (38%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*