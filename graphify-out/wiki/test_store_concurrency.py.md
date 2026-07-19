# test_store_concurrency.py

> 19 nodes · cohesion 0.19

## Key Concepts

- **test_store_concurrency.py** (8 connections) — `tests/registry/test_store_concurrency.py`
- **test_multiprocessing_no_lost_update()** (8 connections) — `tests/registry/test_store_concurrency.py`
- **test_registry_import_does_not_drop_concurrent_add()** (7 connections) — `tests/registry/test_store_concurrency.py`
- **test_two_instances_no_lost_update()** (7 connections) — `tests/registry/test_store_concurrency.py`
- **_read_ids_from_disk()** (6 connections) — `tests/registry/test_store_concurrency.py`
- **_slow_atomic_write_wrapper()** (5 connections) — `tests/registry/test_store_concurrency.py`
- **_child_add()** (4 connections) — `tests/registry/test_store_concurrency.py`
- **_model()** (4 connections) — `tests/registry/test_store_concurrency.py`
- **ModelRegistry** (4 connections)
- **Path** (4 connections)
- **Any** (1 connections)
- **MonkeyPatch** (1 connections)
- **Cross-process write serialization for ModelRegistry (MR-5 regression).  These te** (1 connections) — `tests/registry/test_store_concurrency.py`
- **Child process: construct a ModelRegistry on HAL0_HOME and add a row.** (1 connections) — `tests/registry/test_store_concurrency.py`
- **Two child processes adding distinct rows: both must survive.      This proves tr** (1 connections) — `tests/registry/test_store_concurrency.py`
- **`registry import --force` (_atomic_copy) serializes with a store add.      Timel** (1 connections) — `tests/registry/test_store_concurrency.py`
- **Re-read registry.toml straight off disk (no cache) and return ids.** (1 connections) — `tests/registry/test_store_concurrency.py`
- **Patch reg._atomic_write to sleep AFTER read, before os.replace.      Widening th** (1 connections) — `tests/registry/test_store_concurrency.py`
- **Two ModelRegistry instances on the same dir must not drop rows.      Each instan** (1 connections) — `tests/registry/test_store_concurrency.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/registry/test_store_concurrency.py`

## Audit Trail

- EXTRACTED: 64 (97%)
- INFERRED: 2 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*