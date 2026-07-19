# connect

> 108 nodes

## Key Concepts

- **connect()** (114 connections) — `src/hal0/db/connection.py`
- **tx()** (38 connections) — `src/hal0/db/connection.py`
- **Path** (13 connections)
- **Path** (11 connections)
- **test_connection.py** (9 connections) — `tests/db/test_connection.py`
- **test_migrate_board.py** (8 connections) — `tests/db/test_migrate_board.py`
- **_db()** (8 connections) — `tests/db/test_migrate_board.py`
- **TestPackagedSlotsPortsMigration** (8 connections) — `tests/db/test_migrate_slots_ports.py`
- **Path** (8 connections)
- **Path** (7 connections)
- **Path** (7 connections)
- **TestCollectAndPruneOrphans** (7 connections) — `tests/registry/test_gc.py`
- **._write_batch()** (6 connections) — `src/hal0/metrics/writer.py`
- **_maybe_hardlink_from_blob()** (6 connections) — `src/hal0/registry/pull.py`
- **_register_blob_after_install()** (6 connections) — `src/hal0/registry/pull.py`
- **_copy_model_files_refcounted()** (6 connections) — `src/hal0/services/models_service.py`
- **.test_cascade_does_not_fire_with_foreign_keys_off()** (6 connections) — `tests/db/test_connection.py`
- **test_read.py** (6 connections) — `tests/metrics/test_read.py`
- **test_gc.py** (6 connections) — `tests/registry/test_gc.py`
- **_seed_model()** (6 connections) — `tests/registry/test_gc.py`
- **.test_delete_decrements_shared_blob_refcount_without_deleting_it()** (6 connections) — `tests/registry/test_gc.py`
- **.test_delete_repoints_canonical_blob_path_to_surviving_referent()** (6 connections) — `tests/registry/test_gc.py`
- **connection.py** (5 connections) — `src/hal0/db/connection.py`
- **TestCascadeDelete** (5 connections) — `tests/db/test_connection.py`
- **.test_cascade_fires_with_foreign_keys_on()** (5 connections) — `tests/db/test_connection.py`
- *... and 83 more nodes in this community*

## Relationships

- [Path](Path.md) (20 shared connections)
- [SqliteModelRegistry](SqliteModelRegistry.md) (16 shared connections)
- [MetricsWriter](MetricsWriter.md) (7 shared connections)
- [pull.py](pull.py.md) (6 shared connections)
- [aggregate_hour](aggregate_hour.md) (5 shared connections)
- [_iso](_iso.md) (5 shared connections)
- [BoardStore](BoardStore.md) (4 shared connections)
- [PortAuthority](PortAuthority.md) (4 shared connections)
- [SlotIdentityStore](SlotIdentityStore.md) (4 shared connections)
- [models_health](models_health.md) (3 shared connections)
- [gc.py](gc.py.md) (3 shared connections)
- [models_service.py](models_service.py.md) (3 shared connections)

## Source Files

- `src/hal0/board/store.py`
- `src/hal0/db/connection.py`
- `src/hal0/metrics/writer.py`
- `src/hal0/registry/pull.py`
- `src/hal0/services/models_service.py`
- `tests/db/test_connection.py`
- `tests/db/test_migrate_board.py`
- `tests/db/test_migrate_slots_ports.py`
- `tests/metrics/test_read.py`
- `tests/registry/test_gc.py`

## Audit Trail

- EXTRACTED: 323 (60%)
- INFERRED: 213 (40%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*