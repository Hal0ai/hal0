# SqliteModelRegistry

> 103 nodes · cohesion 0.04

## Key Concepts

- **SqliteModelRegistry** (74 connections) — `src/hal0/registry/sqlite_store.py`
- **model** (27 connections) — `src/hal0/db/migrations/001_registry.sql`
- **ModelNotFound** (24 connections) — `src/hal0/registry/store.py`
- **TomlModelRegistry** (22 connections) — `src/hal0/registry/store.py`
- **store.py** (14 connections) — `src/hal0/registry/store.py`
- **.update()** (12 connections) — `src/hal0/registry/sqlite_store.py`
- **.update()** (12 connections) — `src/hal0/registry/store.py`
- **._ensure_fresh()** (11 connections) — `src/hal0/registry/store.py`
- **._ensure_migrated()** (10 connections) — `src/hal0/registry/sqlite_store.py`
- **.add()** (9 connections) — `src/hal0/registry/store.py`
- **._atomic_write()** (9 connections) — `src/hal0/registry/store.py`
- **.add()** (8 connections) — `src/hal0/registry/sqlite_store.py`
- **._connect()** (8 connections) — `src/hal0/registry/sqlite_store.py`
- **.get()** (8 connections) — `src/hal0/registry/sqlite_store.py`
- **model_to_toml_dict()** (8 connections) — `src/hal0/registry/store.py`
- **._row_to_model()** (7 connections) — `src/hal0/registry/sqlite_store.py`
- **merge_update()** (7 connections) — `src/hal0/registry/store.py`
- **.get()** (7 connections) — `src/hal0/registry/store.py`
- **._process_lock()** (7 connections) — `src/hal0/registry/store.py`
- **._read_locked()** (7 connections) — `src/hal0/registry/store.py`
- **.remove()** (7 connections) — `src/hal0/registry/store.py`
- **.list()** (6 connections) — `src/hal0/registry/sqlite_store.py`
- **.remove()** (6 connections) — `src/hal0/registry/sqlite_store.py`
- **_model_to_toml()** (6 connections) — `src/hal0/registry/store.py`
- **Path** (6 connections)
- *... and 78 more nodes in this community*

## Relationships

- [ModelRegistry](ModelRegistry.md) (29 shared connections)
- [test_store_golden.py](test_store_golden.py.md) (20 shared connections)
- [import_toml_to_sqlite](import_toml_to_sqlite.md) (14 shared connections)
- [test_duplicate_refcount.py](test_duplicate_refcount.py.md) (8 shared connections)
- [Model](Model.md) (5 shared connections)
- [migrate-haloai.py](migrate-haloai.py.md) (3 shared connections)
- [register_candidate](register_candidate.md) (3 shared connections)
- [test_registry_import.py](test_registry_import.py.md) (3 shared connections)
- [tx](tx.md) (3 shared connections)
- [repository.py](repository.py.md) (2 shared connections)
- [models.py](models.py.md) (2 shared connections)
- [slots.py](slots.py.md) (2 shared connections)

## Source Files

- `src/hal0/db/migrations/001_registry.sql`
- `src/hal0/registry/sqlite_store.py`
- `src/hal0/registry/store.py`
- `tests/registry/test_duplicate_refcount.py`

## Audit Trail

- EXTRACTED: 398 (84%)
- INFERRED: 77 (16%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*