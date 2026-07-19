# test_duplicate_refcount.py

> 13 nodes · cohesion 0.26

## Key Concepts

- **test_duplicate_refcount.py** (11 connections) — `tests/registry/test_duplicate_refcount.py`
- **Path** (8 connections)
- **_seed_pulled_model()** (7 connections) — `tests/registry/test_duplicate_refcount.py`
- **test_duplicate_replicates_model_files_and_bumps_refcount()** (5 connections) — `tests/registry/test_duplicate_refcount.py`
- **test_duplicate_conflicting_new_id_raises()** (4 connections) — `tests/registry/test_duplicate_refcount.py`
- **test_duplicate_hand_registered_model_has_no_files_to_refcount()** (4 connections) — `tests/registry/test_duplicate_refcount.py`
- **test_duplicate_same_id_raises()** (4 connections) — `tests/registry/test_duplicate_refcount.py`
- **test_duplicate_with_profile_stamps_flags()** (4 connections) — `tests/registry/test_duplicate_refcount.py`
- **registry()** (3 connections) — `tests/registry/test_duplicate_refcount.py`
- **db_path()** (2 connections) — `tests/registry/test_duplicate_refcount.py`
- **services.models_service.duplicate_model — refcount-reusing row duplication.  Del** (1 connections) — `tests/registry/test_duplicate_refcount.py`
- **Register a model with one LFS ``model_file`` row + its ``store_blob``.** (1 connections) — `tests/registry/test_duplicate_refcount.py`
- **A hand-registered single-file model carries no ``model_file`` rows — the     dup** (1 connections) — `tests/registry/test_duplicate_refcount.py`

## Relationships

- [SqliteModelRegistry](SqliteModelRegistry.md) (8 shared connections)
- [connect](connect.md) (2 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [tx](tx.md) (1 shared connections)
- [ModelRegistry](ModelRegistry.md) (1 shared connections)
- [BadRequest](BadRequest.md) (1 shared connections)
- [ProfileCatalog](ProfileCatalog.md) (1 shared connections)

## Source Files

- `tests/registry/test_duplicate_refcount.py`

## Audit Trail

- EXTRACTED: 49 (89%)
- INFERRED: 6 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*