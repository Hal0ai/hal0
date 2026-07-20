# MonkeyPatch

> 5 nodes

## Key Concepts

- **test_is_hal0_service_user_false_when_hal0_user_absent()** (3 connections) — `tests/system/test_seam.py`
- **MonkeyPatch** (3 connections)
- **test_is_hal0_service_user_true_when_euid_matches()** (2 connections) — `tests/system/test_seam.py`
- **test_is_hal0_service_user_false_when_euid_differs()** (2 connections) — `tests/system/test_seam.py`
- **No 'hal0' system user on this box (dev/CI/unit tests) -> never seam,     regardl** (1 connections) — `tests/system/test_seam.py`

## Relationships

- [SystemCtlSeam](SystemCtlSeam.md) (3 shared connections)

## Source Files

- `tests/system/test_seam.py`

## Audit Trail

- EXTRACTED: 11 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*