# gc.py

> 22 nodes

## Key Concepts

- **gc.py** (11 connections) — `src/hal0/registry/gc.py`
- **reconcile_store_tree()** (7 connections) — `src/hal0/registry/gc.py`
- **prune_orphans()** (6 connections) — `src/hal0/registry/gc.py`
- **_norm_path()** (5 connections) — `src/hal0/registry/gc.py`
- **_tracked_store_paths()** (5 connections) — `src/hal0/registry/gc.py`
- **GCReport** (4 connections) — `src/hal0/registry/gc.py`
- **delete_model_files()** (4 connections) — `src/hal0/registry/gc.py`
- **_repoint_shared_blob()** (4 connections) — `src/hal0/registry/gc.py`
- **_sqlite_files()** (4 connections) — `src/hal0/registry/gc.py`
- **_same_path()** (3 connections) — `src/hal0/registry/gc.py`
- **collect_orphans()** (2 connections) — `src/hal0/registry/gc.py`
- **Real GC for the model store — orphan blob prune + guarded delete (ML-3).  Histor** (1 connections) — `src/hal0/registry/gc.py`
- **Summary of one GC pass.** (1 connections) — `src/hal0/registry/gc.py`
- **Return ``blob_path`` values for every ``store_blob`` row with     ``refcount <=** (1 connections) — `src/hal0/registry/gc.py`
- **Delete (or, if ``dry_run``, just report) every orphaned blob.      ``dry_run=Tru** (1 connections) — `src/hal0/registry/gc.py`
- **Decrement/GC every ``model_file`` row's blob ref, then unlink its     ``dest`` h** (1 connections) — `src/hal0/registry/gc.py`
- **Best-effort equality of two on-disk paths (resolve, fall back to raw).** (1 connections) — `src/hal0/registry/gc.py`
- **Re-point a still-referenced blob's canonical ``blob_path`` off a     just-to-be-** (1 connections) — `src/hal0/registry/gc.py`
- **Normalise a stored path for set-membership comparison against the walk.** (1 connections) — `src/hal0/registry/gc.py`
- **The active SQLite database's own on-disk files (main db + ``-wal`` /     ``-shm`** (1 connections) — `src/hal0/registry/gc.py`
- **Every on-disk path the DB knows about: ``store_blob.blob_path`` (LFS     dedup b** (1 connections) — `src/hal0/registry/gc.py`
- **Reap *bare bytes* — files under the store root tracked by NEITHER a     ``store_** (1 connections) — `src/hal0/registry/gc.py`

## Relationships

- [store.py](store.py.md) (3 shared connections)
- [connect](connect.md) (3 shared connections)

## Source Files

- `src/hal0/registry/gc.py`

## Audit Trail

- EXTRACTED: 60 (91%)
- INFERRED: 6 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*