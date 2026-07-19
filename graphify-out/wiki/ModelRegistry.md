# ModelRegistry

> 26 nodes

## Key Concepts

- **ModelRegistry** (12 connections)
- **TestNewFieldsRoundTrip** (10 connections) — `tests/registry/test_schema_migration.py`
- **_model()** (9 connections) — `tests/registry/test_schema_migration.py`
- **test_schema_migration.py** (6 connections) — `tests/registry/test_schema_migration.py`
- **TestLegacyMigration** (6 connections) — `tests/registry/test_schema_migration.py`
- **Path** (5 connections)
- **.test_defaults_partial_round_trip()** (5 connections) — `tests/registry/test_schema_migration.py`
- **.test_empty_defaults_collapses()** (5 connections) — `tests/registry/test_schema_migration.py`
- **.test_new_fields_survive_fresh_registry_instance()** (5 connections) — `tests/registry/test_schema_migration.py`
- **.test_defaults_full_round_trip()** (4 connections) — `tests/registry/test_schema_migration.py`
- **.test_defaults_none_not_written()** (4 connections) — `tests/registry/test_schema_migration.py`
- **.test_metadata_none_value_stripped()** (4 connections) — `tests/registry/test_schema_migration.py`
- **.test_legacy_entry_loads_with_defaults()** (4 connections) — `tests/registry/test_schema_migration.py`
- **.test_legacy_update_preserves_then_adds_new_fields()** (4 connections) — `tests/registry/test_schema_migration.py`
- **TestFreshInstanceReread** (4 connections) — `tests/registry/test_schema_migration.py`
- **reg()** (3 connections) — `tests/registry/test_schema_migration.py`
- **.test_backends_round_trip()** (3 connections) — `tests/registry/test_schema_migration.py`
- **.test_metadata_context_length_round_trip()** (3 connections) — `tests/registry/test_schema_migration.py`
- **.test_mixed_legacy_and_new_entries_coexist()** (3 connections) — `tests/registry/test_schema_migration.py`
- **Schema-migration tests for the Phase-1 Model additions.  Covers:   * New optiona** (1 connections) — `tests/registry/test_schema_migration.py`
- **Only some ModelDefaults fields set — others stay None.** (1 connections) — `tests/registry/test_schema_migration.py`
- **defaults=None must not appear in the on-disk TOML at all.** (1 connections) — `tests/registry/test_schema_migration.py`
- **All-None ModelDefaults() collapses to no on-disk section.** (1 connections) — `tests/registry/test_schema_migration.py`
- **metadata values that are None get dropped on write (TOML has no null).** (1 connections) — `tests/registry/test_schema_migration.py`
- **An entry with no backends/defaults keys parses fine.** (1 connections) — `tests/registry/test_schema_migration.py`
- *... and 1 more nodes in this community*

## Relationships

- [Model](Model.md) (10 shared connections)

## Source Files

- `tests/registry/test_schema_migration.py`

## Audit Trail

- EXTRACTED: 96 (91%)
- INFERRED: 10 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*