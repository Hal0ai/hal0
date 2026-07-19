# test_fail_watcher_warming.py

> 30 nodes

## Key Concepts

- **test_fail_watcher_warming.py** (15 connections) — `tests/slots/test_fail_watcher_warming.py`
- **_load_into_warming()** (11 connections) — `tests/slots/test_fail_watcher_warming.py`
- **SlotManager** (10 connections)
- **FakeContainerProvider** (8 connections)
- **test_warming_slot_with_fresh_timestamp_is_not_recovered()** (8 connections) — `tests/slots/test_fail_watcher_warming.py`
- **test_warming_slot_recovers_when_stale()** (8 connections) — `tests/slots/test_fail_watcher_warming.py`
- **Path** (7 connections)
- **_await_state()** (7 connections) — `tests/slots/test_fail_watcher_warming.py`
- **test_repeated_warming_error_cycles_recover_without_watcher_leak()** (7 connections) — `tests/slots/test_fail_watcher_warming.py`
- **test_warming_stale_recovery_that_fails_lands_error()** (7 connections) — `tests/slots/test_fail_watcher_warming.py`
- **test_warming_slot_gets_a_watcher_and_survives_failing_health()** (6 connections) — `tests/slots/test_fail_watcher_warming.py`
- **test_warming_slot_flips_error_when_unit_dies()** (6 connections) — `tests/slots/test_fail_watcher_warming.py`
- **test_warming_slot_tolerates_a_transient_inactive_blip()** (6 connections) — `tests/slots/test_fail_watcher_warming.py`
- **_spy_recovery()** (6 connections) — `tests/slots/test_fail_watcher_warming.py`
- **MonkeyPatch** (4 connections)
- **fast_fail_watch()** (3 connections) — `tests/slots/test_fail_watcher_warming.py`
- **test_warming_is_in_the_watched_set()** (1 connections) — `tests/slots/test_fail_watcher_warming.py`
- **Any** (1 connections)
- **WARMING is fail-watched — but only on unit liveness, never /health.  A health-wa** (1 connections) — `tests/slots/test_fail_watcher_warming.py`
- **P3-slots §1b-watchdog: the real sleep lives in hal0.slots.watchdog now.** (1 connections) — `tests/slots/test_fail_watcher_warming.py`
- **Drive load() through a health-wait timeout so the slot parks WARMING.** (1 connections) — `tests/slots/test_fail_watcher_warming.py`
- **Active unit + failing /health must NOT strike a WARMING slot.** (1 connections) — `tests/slots/test_fail_watcher_warming.py`
- **A stably-dead unit under a WARMING slot is caught (was the blind spot).** (1 connections) — `tests/slots/test_fail_watcher_warming.py`
- **One or two inactive reads (unit still activating) must not strike out.** (1 connections) — `tests/slots/test_fail_watcher_warming.py`
- **Record calls to sm.unload / sm.load while delegating to the originals.** (1 connections) — `tests/slots/test_fail_watcher_warming.py`
- *... and 5 more nodes in this community*

## Relationships

- [FakeContainerProvider](FakeContainerProvider.md) (2 shared connections)
- [SlotState](SlotState.md) (1 shared connections)

## Source Files

- `tests/slots/test_fail_watcher_warming.py`

## Audit Trail

- EXTRACTED: 133 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*