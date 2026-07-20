# tx

> 35 nodes · cohesion 0.10

## Key Concepts

- **tx()** (38 connections) — `src/hal0/db/connection.py`
- **Path** (13 connections)
- **TestCollectAndPruneOrphans** (7 connections) — `tests/registry/test_gc.py`
- **._write_batch()** (6 connections) — `src/hal0/metrics/writer.py`
- **prune_orphans()** (6 connections) — `src/hal0/registry/gc.py`
- **_maybe_hardlink_from_blob()** (6 connections) — `src/hal0/registry/pull.py`
- **_copy_model_files_refcounted()** (6 connections) — `src/hal0/services/models_service.py`
- **test_gc.py** (6 connections) — `tests/registry/test_gc.py`
- **_seed_model()** (6 connections) — `tests/registry/test_gc.py`
- **.test_delete_decrements_shared_blob_refcount_without_deleting_it()** (6 connections) — `tests/registry/test_gc.py`
- **.test_delete_repoints_canonical_blob_path_to_surviving_referent()** (6 connections) — `tests/registry/test_gc.py`
- **.test_prune_orphans_escape_guard_before_unlink()** (5 connections) — `tests/registry/test_gc.py`
- **.test_prune_orphans_refcount_guard_never_deletes_referenced()** (5 connections) — `tests/registry/test_gc.py`
- **.test_delete_unlinks_dest_and_drops_blob_at_zero_refcount()** (5 connections) — `tests/registry/test_gc.py`
- **TestReconcileStoreTree** (5 connections) — `tests/registry/test_gc.py`
- **.test_reaps_bare_bytes_retains_tracked_skips_partial()** (5 connections) — `tests/registry/test_gc.py`
- **.test_collect_orphans_finds_zero_refcount_blobs()** (4 connections) — `tests/registry/test_gc.py`
- **.test_prune_orphans_dry_run_does_not_delete()** (4 connections) — `tests/registry/test_gc.py`
- **.test_prune_orphans_real_deletes_blob_and_row()** (4 connections) — `tests/registry/test_gc.py`
- **.test_referenced_blob_is_not_an_orphan()** (4 connections) — `tests/registry/test_gc.py`
- **TestDeleteModelFiles** (4 connections) — `tests/registry/test_gc.py`
- **.test_dry_run_reports_without_unlinking()** (3 connections) — `tests/registry/test_gc.py`
- **.test_max_files_bounds_the_walk()** (3 connections) — `tests/registry/test_gc.py`
- **db_path()** (2 connections) — `tests/registry/test_gc.py`
- **_QueueItem** (1 connections)
- *... and 10 more nodes in this community*

## Relationships

- [connect](connect.md) (22 shared connections)
- [Model](Model.md) (5 shared connections)
- [SqliteModelRegistry](SqliteModelRegistry.md) (3 shared connections)
- [MetricsWriter](MetricsWriter.md) (3 shared connections)
- [store.py](store.py.md) (3 shared connections)
- [models_service.py](models_service.py.md) (3 shared connections)
- [applied_versions](applied_versions.md) (2 shared connections)
- [BoardStore](BoardStore.md) (1 shared connections)
- [aggregate_hour](aggregate_hour.md) (1 shared connections)
- [MetricsRetention](MetricsRetention.md) (1 shared connections)
- [PortAuthority](PortAuthority.md) (1 shared connections)
- [register_candidate](register_candidate.md) (1 shared connections)

## Source Files

- `src/hal0/db/connection.py`
- `src/hal0/metrics/writer.py`
- `src/hal0/registry/gc.py`
- `src/hal0/registry/pull.py`
- `src/hal0/services/models_service.py`
- `tests/registry/test_gc.py`

## Audit Trail

- EXTRACTED: 102 (60%)
- INFERRED: 68 (40%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*