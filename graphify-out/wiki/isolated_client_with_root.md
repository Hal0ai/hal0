# isolated_client_with_root

> 9 nodes

## Key Concepts

- **isolated_client_with_root()** (6 connections) — `tests/api/test_models_scan.py`
- **test_models_scan.py** (4 connections) — `tests/api/test_models_scan.py`
- **TestClient** (3 connections)
- **Path** (3 connections)
- **test_scan_registers_new_gguf()** (3 connections) — `tests/api/test_models_scan.py`
- **test_scan_idempotent()** (3 connections) — `tests/api/test_models_scan.py`
- **TempPathFactory** (1 connections)
- **Tests for POST /api/models/scan — discovery + auto-register.** (1 connections) — `tests/api/test_models_scan.py`
- **App + a tmp root containing one .gguf, with the root pre-configured.      The li** (1 connections) — `tests/api/test_models_scan.py`

## Relationships

- [create_app](create_app.md) (1 shared connections)

## Source Files

- `tests/api/test_models_scan.py`

## Audit Trail

- EXTRACTED: 24 (96%)
- INFERRED: 1 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*