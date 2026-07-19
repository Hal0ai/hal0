# layout_store.py

> 14 nodes

## Key Concepts

- **layout_store.py** (6 connections) — `src/hal0/dashboard/layout_store.py`
- **_layout_path()** (5 connections) — `src/hal0/dashboard/layout_store.py`
- **_write_json_atomic()** (5 connections) — `src/hal0/dashboard/layout_store.py`
- **save()** (5 connections) — `src/hal0/dashboard/layout_store.py`
- **Any** (4 connections)
- **load()** (4 connections) — `src/hal0/dashboard/layout_store.py`
- **reconcile()** (3 connections) — `src/hal0/dashboard/layout_store.py`
- **Path** (2 connections)
- **Persistent file-backed store for the operator's dashboard layout.  Single-operat** (1 connections) — `src/hal0/dashboard/layout_store.py`
- **Return the on-disk path for the layout JSON file.      Resolves through ``paths.** (1 connections) — `src/hal0/dashboard/layout_store.py`
- **Write *data* as JSON to *path* atomically (tempfile + fsync + os.replace).** (1 connections) — `src/hal0/dashboard/layout_store.py`
- **Load the saved dashboard layout from disk.      Returns an empty dict when no la** (1 connections) — `src/hal0/dashboard/layout_store.py`
- **Write *layout* to disk atomically.      Callers should call :func:`reconcile` be** (1 connections) — `src/hal0/dashboard/layout_store.py`
- **Return a defensively-normalised copy of *layout*.      Rules applied (pure — nev** (1 connections) — `src/hal0/dashboard/layout_store.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `src/hal0/dashboard/layout_store.py`

## Audit Trail

- EXTRACTED: 40 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*