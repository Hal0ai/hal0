# repository.py

> 30 nodes · cohesion 0.10

## Key Concepts

- **repository.py** (14 connections) — `src/hal0/db/repository.py`
- **Connection** (9 connections)
- **model_to_row()** (6 connections) — `src/hal0/db/repository.py`
- **row_to_model()** (6 connections) — `src/hal0/db/repository.py`
- **get_blob()** (5 connections) — `src/hal0/db/repository.py`
- **blob_referents()** (4 connections) — `src/hal0/db/repository.py`
- **drop_blob_ref()** (4 connections) — `src/hal0/db/repository.py`
- **insert_blob()** (4 connections) — `src/hal0/db/repository.py`
- **list_model_files()** (4 connections) — `src/hal0/db/repository.py`
- **now_iso()** (4 connections) — `src/hal0/db/repository.py`
- **bump_blob_ref()** (3 connections) — `src/hal0/db/repository.py`
- **insert_model_file()** (3 connections) — `src/hal0/db/repository.py`
- **Any** (3 connections)
- **set_blob_path()** (3 connections) — `src/hal0/db/repository.py`
- **upsert_model_file()** (3 connections) — `src/hal0/db/repository.py`
- **Row** (2 connections)
- **``model`` row ⇄ :class:`hal0.registry.model.Model` mapping — the pydantic seam.** (1 connections) — `src/hal0/db/repository.py`
- **ISO-8601 UTC timestamp — matches the ``activity``/``bench`` convention.** (1 connections) — `src/hal0/db/repository.py`
- **Serialise a ``Model`` into a flat dict keyed by the `model` table columns.** (1 connections) — `src/hal0/db/repository.py`
- **Insert one ``model_file`` row (idempotent — first-writer semantics).** (1 connections) — `src/hal0/db/repository.py`
- **Insert-or-replace one ``model_file`` row.      Unlike :func:`insert_model_file`** (1 connections) — `src/hal0/db/repository.py`
- **Return every ``model_file`` row for ``model_id``, shard-ordered.      Entry poin** (1 connections) — `src/hal0/db/repository.py`
- **Return the ``store_blob`` row for ``sha256``, or ``None``.** (1 connections) — `src/hal0/db/repository.py`
- **Register a freshly-installed file as the canonical blob for its sha256.** (1 connections) — `src/hal0/db/repository.py`
- **Increment ``store_blob.refcount`` — a new hardlink now shares this blob.** (1 connections) — `src/hal0/db/repository.py`
- *... and 5 more nodes in this community*

## Relationships

- [ModelDefaults](ModelDefaults.md) (3 shared connections)
- [SqliteModelRegistry](SqliteModelRegistry.md) (2 shared connections)

## Source Files

- `src/hal0/db/repository.py`

## Audit Trail

- EXTRACTED: 88 (97%)
- INFERRED: 3 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*