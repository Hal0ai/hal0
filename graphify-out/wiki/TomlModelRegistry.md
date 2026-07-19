# TomlModelRegistry

> 57 nodes

## Key Concepts

- **TomlModelRegistry** (22 connections) — `src/hal0/registry/store.py`
- **store.py** (14 connections) — `src/hal0/registry/store.py`
- **.update()** (12 connections) — `src/hal0/registry/store.py`
- **._ensure_fresh()** (11 connections) — `src/hal0/registry/store.py`
- **Model** (10 connections)
- **._atomic_write()** (9 connections) — `src/hal0/registry/store.py`
- **.add()** (9 connections) — `src/hal0/registry/store.py`
- **model_to_toml_dict()** (8 connections) — `src/hal0/registry/store.py`
- **._read_locked()** (7 connections) — `src/hal0/registry/store.py`
- **._process_lock()** (7 connections) — `src/hal0/registry/store.py`
- **.get()** (7 connections) — `src/hal0/registry/store.py`
- **.remove()** (7 connections) — `src/hal0/registry/store.py`
- **merge_update()** (7 connections) — `src/hal0/registry/store.py`
- **Path** (6 connections)
- **._invalidate()** (6 connections) — `src/hal0/registry/store.py`
- **_model_to_toml()** (6 connections) — `src/hal0/registry/store.py`
- **registry_write_lock()** (5 connections) — `src/hal0/registry/store.py`
- **_fsync_dir()** (5 connections) — `src/hal0/registry/store.py`
- **.list()** (5 connections) — `src/hal0/registry/store.py`
- **._notify_change()** (5 connections) — `src/hal0/registry/store.py`
- **._stat_mtime()** (4 connections) — `src/hal0/registry/store.py`
- **Any** (4 connections)
- **.__init__()** (3 connections) — `src/hal0/registry/store.py`
- **.registry_dir()** (3 connections) — `src/hal0/registry/store.py`
- **.registry_file()** (3 connections) — `src/hal0/registry/store.py`
- *... and 32 more nodes in this community*

## Relationships

- [Model](Model.md) (9 shared connections)
- [test_registry_import.py](test_registry_import.py.md) (2 shared connections)
- [SqliteModelRegistry](SqliteModelRegistry.md) (2 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [pull.py](pull.py.md) (1 shared connections)
- [migrate-haloai.py](migrate-haloai.py.md) (1 shared connections)
- [import_toml_to_sqlite](import_toml_to_sqlite.md) (1 shared connections)

## Source Files

- `src/hal0/registry/store.py`

## Audit Trail

- EXTRACTED: 213 (96%)
- INFERRED: 10 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*