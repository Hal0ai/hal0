# map_backend_to_device

> 23 nodes

## Key Concepts

- **map_backend_to_device()** (14 connections) — `src/hal0/model_meta/__init__.py`
- **migrate_capabilities_v1_to_v2()** (13 connections) — `src/hal0/capabilities/config.py`
- **TestMigrateCapabilitiesV1ToV2** (9 connections) — `tests/config/test_schema_migration.py`
- **TestMapBackendToDevice** (5 connections) — `tests/config/test_schema_migration.py`
- **._promote_backend_to_device()** (4 connections) — `src/hal0/capabilities/config.py`
- **Any** (4 connections)
- **.test_unknown_value_maps_to_cpu_with_warning()** (3 connections) — `tests/config/test_schema_migration.py`
- **.test_already_v2_input_is_noop()** (3 connections) — `tests/config/test_schema_migration.py`
- **.test_empty_input_returns_default()** (2 connections) — `tests/config/test_schema_migration.py`
- **.test_known_legacy_values()** (2 connections) — `tests/config/test_schema_migration.py`
- **.test_new_namespace_values_are_idempotent()** (2 connections) — `tests/config/test_schema_migration.py`
- **.test_renames_backend_to_device()** (2 connections) — `tests/config/test_schema_migration.py`
- **.test_mapping_table()** (2 connections) — `tests/config/test_schema_migration.py`
- **.test_empty_selections_stamps_version()** (2 connections) — `tests/config/test_schema_migration.py`
- **.test_no_selections_key()** (2 connections) — `tests/config/test_schema_migration.py`
- **.test_unknown_backend_falls_back_to_cpu()** (2 connections) — `tests/config/test_schema_migration.py`
- **.test_empty_selection_passes_through()** (2 connections) — `tests/config/test_schema_migration.py`
- **.test_caller_dict_not_mutated()** (2 connections) — `tests/config/test_schema_migration.py`
- **Read-only promotion shim: derive ``device`` from a legacy         on-disk ``back** (1 connections) — `src/hal0/capabilities/config.py`
- **Pure-dict v1 → v2 migration. Idempotent on v2 inputs.      Transforms applied:** (1 connections) — `src/hal0/capabilities/config.py`
- **Map a legacy ``backend`` value to the v0.2 ``device`` enum.      Unknown values** (1 connections) — `src/hal0/model_meta/__init__.py`
- **LogCaptureFixture** (1 connections)
- **Idempotence: a v2 file should round-trip unchanged in shape.** (1 connections) — `tests/config/test_schema_migration.py`

## Relationships

- [CapabilitySelection](CapabilitySelection.md) (7 shared connections)
- [test_model_meta.py](test_model_meta.py.md) (2 shared connections)
- [SlotConfig](SlotConfig.md) (1 shared connections)
- [recommend_primary_slot](recommend_primary_slot.md) (1 shared connections)
- [.apply](apply.md) (1 shared connections)
- [SlotState](SlotState.md) (1 shared connections)
- [_reconcile_device_profile](_reconcile_device_profile.md) (1 shared connections)

## Source Files

- `src/hal0/capabilities/config.py`
- `src/hal0/model_meta/__init__.py`
- `tests/config/test_schema_migration.py`

## Audit Trail

- EXTRACTED: 47 (59%)
- INFERRED: 33 (41%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*