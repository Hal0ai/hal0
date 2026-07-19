# store.py

> 32 nodes

## Key Concepts

- **store.py** (17 connections) — `src/hal0/config/store.py`
- **Path** (12 connections)
- **assert_under_store()** (9 connections) — `src/hal0/config/store.py`
- **store_root()** (8 connections) — `src/hal0/config/store.py`
- **model_dir()** (7 connections) — `src/hal0/config/store.py`
- **entry_pointer()** (7 connections) — `src/hal0/config/store.py`
- **StorePathEscape** (5 connections) — `src/hal0/config/store.py`
- **_sanitise()** (5 connections) — `src/hal0/config/store.py`
- **file_dest()** (5 connections) — `src/hal0/config/store.py`
- **is_nfs_path()** (5 connections) — `src/hal0/config/store.py`
- **mount_for()** (5 connections) — `src/hal0/config/store.py`
- **repo_dirname()** (4 connections) — `src/hal0/config/store.py`
- **by_id_dir()** (4 connections) — `src/hal0/config/store.py`
- **set_entry_pointer()** (4 connections) — `src/hal0/config/store.py`
- **resolve_entry_pointer()** (4 connections) — `src/hal0/config/store.py`
- **_fstype_from_proc_mounts()** (4 connections) — `src/hal0/config/store.py`
- **finalize_perms()** (3 connections) — `src/hal0/config/store.py`
- **Unified model-store resolver — read == write, one precedence, one default.  Fixe** (1 connections) — `src/hal0/config/store.py`
- **A derived store path resolved outside the configured store root.      Raised (fa** (1 connections) — `src/hal0/config/store.py`
- **Resolve the single model-store root — identical for reads and writes.      See m** (1 connections) — `src/hal0/config/store.py`
- **Require ``p`` to resolve inside :func:`store_root`.      ``severity="fail"`` (th** (1 connections) — `src/hal0/config/store.py`
- **Strip path-unsafe characters — shared shape with the historic pull     engine's** (1 connections) — `src/hal0/config/store.py`
- **``org/repo`` → ``models--org--repo`` (HF local-cache shape).      :func:`hal0.re** (1 connections) — `src/hal0/config/store.py`
- **``<store_root>/models--<org>--<repo>/snapshots/<revision>``.** (1 connections) — `src/hal0/config/store.py`
- **Final on-disk destination for one file of a repo/revision fileset.      Always p** (1 connections) — `src/hal0/config/store.py`
- *... and 7 more nodes in this community*

## Relationships

- [gc.py](gc.py.md) (3 shared connections)
- [Mount](Mount.md) (2 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [Hal0Error](Hal0Error.md) (1 shared connections)
- [load_hal0_config](load_hal0_config.md) (1 shared connections)
- [model_store_root](model_store_root.md) (1 shared connections)

## Source Files

- `src/hal0/config/store.py`

## Audit Trail

- EXTRACTED: 117 (95%)
- INFERRED: 6 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*