# import_toml_to_sqlite

> 30 nodes · cohesion 0.13

## Key Concepts

- **import_toml_to_sqlite()** (16 connections) — `src/hal0/registry/import_toml.py`
- **Path** (10 connections)
- **TestImportIdempotency** (8 connections) — `tests/registry/test_import_export_roundtrip.py`
- **TestLosslessImport** (8 connections) — `tests/registry/test_import_export_roundtrip.py`
- **_write_registry_toml()** (8 connections) — `tests/registry/test_import_export_roundtrip.py`
- **_import_into()** (6 connections) — `src/hal0/registry/import_toml.py`
- **.test_rerunning_import_never_clobbers_a_post_import_edit()** (6 connections) — `tests/registry/test_import_export_roundtrip.py`
- **.test_minimal_entry_round_trips_with_defaults()** (6 connections) — `tests/registry/test_import_export_roundtrip.py`
- **import_toml.py** (5 connections) — `src/hal0/registry/import_toml.py`
- **ImportReport** (5 connections) — `src/hal0/registry/import_toml.py`
- **_load_toml_models()** (5 connections) — `src/hal0/registry/import_toml.py`
- **test_import_export_roundtrip.py** (5 connections) — `tests/registry/test_import_export_roundtrip.py`
- **TestExport** (5 connections) — `tests/registry/test_import_export_roundtrip.py`
- **.test_export_reproduces_toml_shape()** (5 connections) — `tests/registry/test_import_export_roundtrip.py`
- **.test_first_boot_import_fires_automatically_on_empty_db()** (5 connections) — `tests/registry/test_import_export_roundtrip.py`
- **.test_every_field_round_trips()** (5 connections) — `tests/registry/test_import_export_roundtrip.py`
- **.test_first_boot_import_does_not_reimport_on_second_instance()** (4 connections) — `tests/registry/test_import_export_roundtrip.py`
- **.test_rerunning_import_does_not_reimport()** (4 connections) — `tests/registry/test_import_export_roundtrip.py`
- **.test_malformed_entry_is_skipped_not_raised()** (4 connections) — `tests/registry/test_import_export_roundtrip.py`
- **.test_missing_registry_file_is_a_noop()** (3 connections) — `tests/registry/test_import_export_roundtrip.py`
- **Connection** (2 connections)
- **Path** (2 connections)
- **One-shot, idempotent import: ``registry.toml`` → the SQLite ``model`` table.  Tw** (1 connections) — `src/hal0/registry/import_toml.py`
- **Idempotently copy every valid ``registry.toml`` entry into SQLite.      Args:** (1 connections) — `src/hal0/registry/import_toml.py`
- **Outcome counters for one :func:`import_toml_to_sqlite` run.** (1 connections) — `src/hal0/registry/import_toml.py`
- *... and 5 more nodes in this community*

## Relationships

- [SqliteModelRegistry](SqliteModelRegistry.md) (14 shared connections)
- [Model](Model.md) (4 shared connections)
- [ModelDefaults](ModelDefaults.md) (4 shared connections)
- [tx](tx.md) (1 shared connections)
- [test_registry_import.py](test_registry_import.py.md) (1 shared connections)
- [connect](connect.md) (1 shared connections)

## Source Files

- `src/hal0/registry/import_toml.py`
- `tests/registry/test_import_export_roundtrip.py`

## Audit Trail

- EXTRACTED: 100 (74%)
- INFERRED: 35 (26%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*