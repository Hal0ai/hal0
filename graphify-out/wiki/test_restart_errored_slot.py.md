# test_restart_errored_slot.py

> 14 nodes

## Key Concepts

- **test_restart_errored_slot.py** (7 connections) — `tests/slots/test_restart_errored_slot.py`
- **_drive_into_error()** (7 connections) — `tests/slots/test_restart_errored_slot.py`
- **test_restart_from_error_survives_a_hanging_terminate()** (7 connections) — `tests/slots/test_restart_errored_slot.py`
- **test_restart_from_error_reaches_ready()** (6 connections) — `tests/slots/test_restart_errored_slot.py`
- **test_restart_from_error_does_not_short_circuit()** (6 connections) — `tests/slots/test_restart_errored_slot.py`
- **SlotManager** (4 connections)
- **FakeContainerProvider** (4 connections)
- **Path** (3 connections)
- **MonkeyPatch** (1 connections)
- **Restarting an ERROR slot must run the full stop→create→load, not wedge.  Issue #** (1 connections) — `tests/slots/test_restart_errored_slot.py`
- **Fail the spawn so ``load()`` stamps the slot ERROR and re-raises.** (1 connections) — `tests/slots/test_restart_errored_slot.py`
- **An errored slot restarted after the fault clears lands READY.** (1 connections) — `tests/slots/test_restart_errored_slot.py`
- **ERROR is not a live state — restart must never no-op it as 'loaded'.** (1 connections) — `tests/slots/test_restart_errored_slot.py`
- **A terminate that blows up must not wedge the restart — it is best-effort     and** (1 connections) — `tests/slots/test_restart_errored_slot.py`

## Relationships

- [FakeContainerProvider](FakeContainerProvider.md) (2 shared connections)

## Source Files

- `tests/slots/test_restart_errored_slot.py`

## Audit Trail

- EXTRACTED: 50 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*