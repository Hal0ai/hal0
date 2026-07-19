# _FakeSM

> 10 nodes

## Key Concepts

- **_FakeSM** (6 connections) — `tests/api/test_health_degraded.py`
- **test_health_degraded.py** (5 connections) — `tests/api/test_health_degraded.py`
- **_slot()** (5 connections) — `tests/api/test_health_degraded.py`
- **test_health_system_ok_when_no_errored_slots()** (4 connections) — `tests/api/test_health_degraded.py`
- **test_health_system_degraded_when_slot_errored()** (4 connections) — `tests/api/test_health_degraded.py`
- **Slot** (3 connections)
- **.__init__()** (2 connections) — `tests/api/test_health_degraded.py`
- **.list()** (2 connections) — `tests/api/test_health_degraded.py`
- **TestClient** (2 connections)
- **B2: /api/health/system must report degraded when a slot is in ERROR.  Previously** (1 connections) — `tests/api/test_health_degraded.py`

## Relationships

- [SlotState](SlotState.md) (2 shared connections)

## Source Files

- `tests/api/test_health_degraded.py`

## Audit Trail

- EXTRACTED: 33 (97%)
- INFERRED: 1 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*