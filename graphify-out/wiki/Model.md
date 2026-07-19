# Model

> 80 nodes

## Key Concepts

- **Model** (63 connections) — `src/hal0/registry/model.py`
- **ModelRegistry** (38 connections)
- **ModelDefaults** (35 connections) — `src/hal0/registry/model.py`
- **_model()** (28 connections) — `tests/registry/test_sqlite_store.py`
- **ModelNotFound** (24 connections) — `src/hal0/registry/store.py`
- **RegistryError** (14 connections) — `src/hal0/registry/store.py`
- **test_sqlite_store.py** (14 connections) — `tests/registry/test_sqlite_store.py`
- **ModelAlreadyExists** (12 connections) — `src/hal0/registry/store.py`
- **TestUpdate** (11 connections) — `tests/registry/test_sqlite_store.py`
- **TestEmptyRegistry** (9 connections) — `tests/registry/test_sqlite_store.py`
- **TestAdd** (9 connections) — `tests/registry/test_sqlite_store.py`
- **TestRouteFor** (8 connections) — `tests/registry/test_sqlite_store.py`
- **TestReloadAndOnChange** (8 connections) — `tests/registry/test_sqlite_store.py`
- **TestModelDefaultsRoundTrip** (8 connections) — `tests/registry/test_sqlite_store.py`
- **TestRemove** (7 connections) — `tests/registry/test_sqlite_store.py`
- **TestDefaultRegistryDir** (7 connections) — `tests/registry/test_sqlite_store.py`
- **model.py** (6 connections) — `src/hal0/registry/model.py`
- **seed_registry_from_body()** (6 connections) — `src/hal0/registry/pull_jobs.py`
- **Path** (6 connections)
- **.test_update_preserves_created_at()** (6 connections) — `tests/registry/test_sqlite_store.py`
- **TestConcurrency** (6 connections) — `tests/registry/test_sqlite_store.py`
- **TestList** (5 connections) — `tests/registry/test_sqlite_store.py`
- **.test_empty_defaults_collapses_to_none()** (5 connections) — `tests/registry/test_sqlite_store.py`
- **.test_falsy_but_set_n_gpu_layers_is_not_dropped()** (5 connections) — `tests/registry/test_sqlite_store.py`
- **reg()** (4 connections) — `tests/registry/test_sqlite_store.py`
- *... and 55 more nodes in this community*

## Relationships

- [ModelRegistry](ModelRegistry.md) (32 shared connections)
- [SqliteModelRegistry](SqliteModelRegistry.md) (21 shared connections)
- [pull.py](pull.py.md) (11 shared connections)
- [TomlModelRegistry](TomlModelRegistry.md) (9 shared connections)
- [import_toml_to_sqlite](import_toml_to_sqlite.md) (8 shared connections)
- [repository.py](repository.py.md) (3 shared connections)
- [test_modality.py](test_modality.py.md) (3 shared connections)
- [migrate-haloai.py](migrate-haloai.py.md) (3 shared connections)
- [_derive_ns](_derive_ns.md) (2 shared connections)
- [BaseModel](BaseModel.md) (2 shared connections)
- [models_service.py](models_service.py.md) (2 shared connections)
- [embed_references](embed_references.md) (2 shared connections)

## Source Files

- `src/hal0/registry/model.py`
- `src/hal0/registry/pull_jobs.py`
- `src/hal0/registry/store.py`
- `tests/registry/test_sqlite_store.py`

## Audit Trail

- EXTRACTED: 308 (65%)
- INFERRED: 167 (35%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*