# ModelRegistry

> 68 nodes · cohesion 0.06

## Key Concepts

- **ModelRegistry** (38 connections)
- **_model()** (28 connections) — `tests/registry/test_sqlite_store.py`
- **RegistryError** (14 connections) — `src/hal0/registry/store.py`
- **test_sqlite_store.py** (14 connections) — `tests/registry/test_sqlite_store.py`
- **ModelAlreadyExists** (12 connections) — `src/hal0/registry/store.py`
- **TestUpdate** (11 connections) — `tests/registry/test_sqlite_store.py`
- **TestAdd** (9 connections) — `tests/registry/test_sqlite_store.py`
- **TestEmptyRegistry** (9 connections) — `tests/registry/test_sqlite_store.py`
- **TestModelDefaultsRoundTrip** (8 connections) — `tests/registry/test_sqlite_store.py`
- **TestReloadAndOnChange** (8 connections) — `tests/registry/test_sqlite_store.py`
- **TestRouteFor** (8 connections) — `tests/registry/test_sqlite_store.py`
- **TestDefaultRegistryDir** (7 connections) — `tests/registry/test_sqlite_store.py`
- **TestRemove** (7 connections) — `tests/registry/test_sqlite_store.py`
- **Path** (6 connections)
- **TestConcurrency** (6 connections) — `tests/registry/test_sqlite_store.py`
- **.test_update_preserves_created_at()** (6 connections) — `tests/registry/test_sqlite_store.py`
- **TestList** (5 connections) — `tests/registry/test_sqlite_store.py`
- **.test_empty_defaults_collapses_to_none()** (5 connections) — `tests/registry/test_sqlite_store.py`
- **.test_falsy_but_set_n_gpu_layers_is_not_dropped()** (5 connections) — `tests/registry/test_sqlite_store.py`
- **reg()** (4 connections) — `tests/registry/test_sqlite_store.py`
- **.test_add_duplicate_leaves_original_row_intact()** (4 connections) — `tests/registry/test_sqlite_store.py`
- **.test_add_duplicate_raises()** (4 connections) — `tests/registry/test_sqlite_store.py`
- **.test_add_persists_across_instances()** (4 connections) — `tests/registry/test_sqlite_store.py`
- **.test_two_instances_same_dir_no_lost_update()** (4 connections) — `tests/registry/test_sqlite_store.py`
- **.test_full_defaults_round_trip()** (4 connections) — `tests/registry/test_sqlite_store.py`
- *... and 43 more nodes in this community*

## Relationships

- [SqliteModelRegistry](SqliteModelRegistry.md) (25 shared connections)
- [ModelDefaults](ModelDefaults.md) (13 shared connections)
- [Model](Model.md) (12 shared connections)
- [ModelRegistry](ModelRegistry.md) (3 shared connections)
- [get_curated](get_curated.md) (1 shared connections)
- [test_duplicate_refcount.py](test_duplicate_refcount.py.md) (1 shared connections)
- [connect](connect.md) (1 shared connections)

## Source Files

- `src/hal0/registry/store.py`
- `tests/registry/test_sqlite_store.py`

## Audit Trail

- EXTRACTED: 276 (83%)
- INFERRED: 58 (17%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*