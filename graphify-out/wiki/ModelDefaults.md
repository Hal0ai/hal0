# ModelDefaults

> 44 nodes · cohesion 0.08

## Key Concepts

- **ModelDefaults** (35 connections) — `src/hal0/registry/model.py`
- **ModelRegistry** (12 connections)
- **TestNewFieldsRoundTrip** (10 connections) — `tests/registry/test_schema_migration.py`
- **ModelCapabilities** (9 connections) — `src/hal0/registry/model.py`
- **test_model_capabilities.py** (9 connections) — `tests/registry/test_model_capabilities.py`
- **_model()** (9 connections) — `tests/registry/test_schema_migration.py`
- **model.py** (6 connections) — `src/hal0/registry/model.py`
- **test_schema_migration.py** (6 connections) — `tests/registry/test_schema_migration.py`
- **TestLegacyMigration** (6 connections) — `tests/registry/test_schema_migration.py`
- **Path** (5 connections)
- **.test_new_fields_survive_fresh_registry_instance()** (5 connections) — `tests/registry/test_schema_migration.py`
- **.test_defaults_partial_round_trip()** (5 connections) — `tests/registry/test_schema_migration.py`
- **.test_empty_defaults_collapses()** (5 connections) — `tests/registry/test_schema_migration.py`
- **TestFreshInstanceReread** (4 connections) — `tests/registry/test_schema_migration.py`
- **.test_legacy_entry_loads_with_defaults()** (4 connections) — `tests/registry/test_schema_migration.py`
- **.test_legacy_update_preserves_then_adds_new_fields()** (4 connections) — `tests/registry/test_schema_migration.py`
- **.test_defaults_full_round_trip()** (4 connections) — `tests/registry/test_schema_migration.py`
- **.test_defaults_none_not_written()** (4 connections) — `tests/registry/test_schema_migration.py`
- **.test_metadata_none_value_stripped()** (4 connections) — `tests/registry/test_schema_migration.py`
- **BaseModel** (3 connections)
- **reg()** (3 connections) — `tests/registry/test_schema_migration.py`
- **.test_mixed_legacy_and_new_entries_coexist()** (3 connections) — `tests/registry/test_schema_migration.py`
- **.test_backends_round_trip()** (3 connections) — `tests/registry/test_schema_migration.py`
- **.test_metadata_context_length_round_trip()** (3 connections) — `tests/registry/test_schema_migration.py`
- **test_model_capabilities_defaults_all_none()** (2 connections) — `tests/registry/test_model_capabilities.py`
- *... and 19 more nodes in this community*

## Relationships

- [ModelRegistry](ModelRegistry.md) (13 shared connections)
- [Model](Model.md) (5 shared connections)
- [import_toml_to_sqlite](import_toml_to_sqlite.md) (4 shared connections)
- [repository.py](repository.py.md) (3 shared connections)
- [test_modality.py](test_modality.py.md) (2 shared connections)
- [models_service.py](models_service.py.md) (2 shared connections)
- [_derive_ns](_derive_ns.md) (1 shared connections)
- [get_curated](get_curated.md) (1 shared connections)
- [ReaperHost](ReaperHost.md) (1 shared connections)
- [test_mtp_defuse.py](test_mtp_defuse.py.md) (1 shared connections)

## Source Files

- `src/hal0/registry/model.py`
- `tests/registry/test_model_capabilities.py`
- `tests/registry/test_schema_migration.py`

## Audit Trail

- EXTRACTED: 134 (72%)
- INFERRED: 51 (28%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*