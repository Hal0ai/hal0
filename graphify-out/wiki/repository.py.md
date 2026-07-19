# repository.py

> 43 nodes

## Key Concepts

- **repository.py** (14 connections) — `src/hal0/db/repository.py`
- **Connection** (9 connections)
- **ModelCapabilities** (9 connections) — `src/hal0/registry/model.py`
- **test_model_capabilities.py** (9 connections) — `tests/registry/test_model_capabilities.py`
- **model_to_row()** (6 connections) — `src/hal0/db/repository.py`
- **row_to_model()** (6 connections) — `src/hal0/db/repository.py`
- **get_blob()** (5 connections) — `src/hal0/db/repository.py`
- **now_iso()** (4 connections) — `src/hal0/db/repository.py`
- **list_model_files()** (4 connections) — `src/hal0/db/repository.py`
- **insert_blob()** (4 connections) — `src/hal0/db/repository.py`
- **drop_blob_ref()** (4 connections) — `src/hal0/db/repository.py`
- **blob_referents()** (4 connections) — `src/hal0/db/repository.py`
- **Any** (3 connections)
- **insert_model_file()** (3 connections) — `src/hal0/db/repository.py`
- **upsert_model_file()** (3 connections) — `src/hal0/db/repository.py`
- **bump_blob_ref()** (3 connections) — `src/hal0/db/repository.py`
- **set_blob_path()** (3 connections) — `src/hal0/db/repository.py`
- **Model** (2 connections)
- **Row** (2 connections)
- **test_model_capabilities_defaults_all_none()** (2 connections) — `tests/registry/test_model_capabilities.py`
- **test_model_capabilities_tri_state()** (2 connections) — `tests/registry/test_model_capabilities.py`
- **test_model_capabilities_forbids_extra_fields()** (2 connections) — `tests/registry/test_model_capabilities.py`
- **test_model_capability_flags_round_trips_through_model_dump()** (2 connections) — `tests/registry/test_model_capabilities.py`
- **``model`` row ⇄ :class:`hal0.registry.model.Model` mapping — the pydantic seam.** (1 connections) — `src/hal0/db/repository.py`
- **ISO-8601 UTC timestamp — matches the ``activity``/``bench`` convention.** (1 connections) — `src/hal0/db/repository.py`
- *... and 18 more nodes in this community*

## Relationships

- [Model](Model.md) (3 shared connections)
- [BaseModel](BaseModel.md) (1 shared connections)
- [test_modality.py](test_modality.py.md) (1 shared connections)

## Source Files

- `src/hal0/db/repository.py`
- `src/hal0/registry/model.py`
- `tests/registry/test_model_capabilities.py`

## Audit Trail

- EXTRACTED: 112 (90%)
- INFERRED: 13 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*