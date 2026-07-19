# test_models_add_from_path.py

> 19 nodes · cohesion 0.20

## Key Concepts

- **test_models_add_from_path.py** (10 connections) — `tests/api/test_models_add_from_path.py`
- **Path** (9 connections)
- **TestClient** (9 connections)
- **add_path_client()** (5 connections) — `tests/api/test_models_add_from_path.py`
- **test_add_from_path_409_on_duplicate_id()** (4 connections) — `tests/api/test_models_add_from_path.py`
- **test_add_from_path_honours_explicit_id_and_labels()** (4 connections) — `tests/api/test_models_add_from_path.py`
- **test_add_from_path_overwrites_when_requested()** (4 connections) — `tests/api/test_models_add_from_path.py`
- **test_add_from_path_registers_gguf()** (4 connections) — `tests/api/test_models_add_from_path.py`
- **test_add_from_path_rejects_unsupported_extension()** (4 connections) — `tests/api/test_models_add_from_path.py`
- **test_add_from_path_rejects_bad_body()** (3 connections) — `tests/api/test_models_add_from_path.py`
- **test_add_from_path_rejects_missing_file()** (3 connections) — `tests/api/test_models_add_from_path.py`
- **test_add_from_path_rejects_relative_path()** (3 connections) — `tests/api/test_models_add_from_path.py`
- **Tests for POST /api/models/add-from-path — single-file registry add.  The endpoi** (1 connections) — `tests/api/test_models_add_from_path.py`
- **A file whose extension isn't in file_extensions must 400.** (1 connections) — `tests/api/test_models_add_from_path.py`
- **Re-adding the same path without overwrite=true is a 409.** (1 connections) — `tests/api/test_models_add_from_path.py`
- **overwrite=true replaces the existing entry in place.** (1 connections) — `tests/api/test_models_add_from_path.py`
- **Empty-config app + a tmp dir to drop fixture files into.      The dir is NOT reg** (1 connections) — `tests/api/test_models_add_from_path.py`
- **Pointing at a real .gguf file lands in the registry as installed=True.** (1 connections) — `tests/api/test_models_add_from_path.py`
- **Caller-supplied id + labels must win over the detector's defaults.** (1 connections) — `tests/api/test_models_add_from_path.py`

## Relationships

- [create_app](create_app.md) (1 shared connections)

## Source Files

- `tests/api/test_models_add_from_path.py`

## Audit Trail

- EXTRACTED: 68 (99%)
- INFERRED: 1 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*