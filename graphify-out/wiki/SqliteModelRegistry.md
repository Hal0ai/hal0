# SqliteModelRegistry

> 94 nodes

## Key Concepts

- **SqliteModelRegistry** (74 connections) — `src/hal0/registry/sqlite_store.py`
- **test_store_golden.py** (17 connections) — `tests/registry/test_store_golden.py`
- **_pull_fileset()** (17 connections) — `tests/registry/test_store_golden.py`
- **.update()** (12 connections) — `src/hal0/registry/sqlite_store.py`
- **test_duplicate_refcount.py** (11 connections) — `tests/registry/test_duplicate_refcount.py`
- **._ensure_migrated()** (10 connections) — `src/hal0/registry/sqlite_store.py`
- **_body()** (10 connections) — `tests/registry/test_store_golden.py`
- **._connect()** (8 connections) — `src/hal0/registry/sqlite_store.py`
- **.get()** (8 connections) — `src/hal0/registry/sqlite_store.py`
- **.add()** (8 connections) — `src/hal0/registry/sqlite_store.py`
- **Path** (8 connections)
- **.test_single_file_pull_writes_row_blob_and_pointer()** (8 connections) — `tests/registry/test_store_golden.py`
- **._row_to_model()** (7 connections) — `src/hal0/registry/sqlite_store.py`
- **_seed_pulled_model()** (7 connections) — `tests/registry/test_duplicate_refcount.py`
- **_blob()** (7 connections) — `tests/registry/test_store_golden.py`
- **.test_delete_one_of_two_keeps_bytes_delete_last_removes_bytes()** (7 connections) — `tests/registry/test_store_golden.py`
- **.test_orphan_pruned_live_retained_missing_bytes_tolerated()** (7 connections) — `tests/registry/test_store_golden.py`
- **.list()** (6 connections) — `src/hal0/registry/sqlite_store.py`
- **.remove()** (6 connections) — `src/hal0/registry/sqlite_store.py`
- **_model_files()** (6 connections) — `tests/registry/test_store_golden.py`
- **.test_all_shards_and_mmproj_recorded_with_roles()** (6 connections) — `tests/registry/test_store_golden.py`
- **.test_identical_blob_across_two_models_is_one_inode_refcount_two()** (6 connections) — `tests/registry/test_store_golden.py`
- **TestGcReconcilesDbVsFilesystem** (6 connections) — `tests/registry/test_store_golden.py`
- **.test_written_dest_is_under_the_read_resolver_root()** (6 connections) — `tests/registry/test_store_golden.py`
- **TestNfsRelabelOmission** (6 connections) — `tests/registry/test_store_golden.py`
- *... and 69 more nodes in this community*

## Relationships

- [Model](Model.md) (21 shared connections)
- [connect](connect.md) (16 shared connections)
- [import_toml_to_sqlite](import_toml_to_sqlite.md) (11 shared connections)
- [plan_fileset](plan_fileset.md) (11 shared connections)
- [TomlModelRegistry](TomlModelRegistry.md) (2 shared connections)
- [run_pull](run_pull.md) (2 shared connections)
- [test_registry_import.py](test_registry_import.py.md) (1 shared connections)
- [scan_and_register](scan_and_register.md) (1 shared connections)
- [pull.py](pull.py.md) (1 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)
- [BadRequest](BadRequest.md) (1 shared connections)

## Source Files

- `src/hal0/registry/sqlite_store.py`
- `tests/registry/test_duplicate_refcount.py`
- `tests/registry/test_store_golden.py`

## Audit Trail

- EXTRACTED: 363 (82%)
- INFERRED: 82 (18%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*