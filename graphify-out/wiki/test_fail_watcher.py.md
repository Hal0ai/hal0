# test_fail_watcher.py

> 20 nodes

## Key Concepts

- **test_fail_watcher.py** (10 connections) — `tests/slots/test_fail_watcher.py`
- **_wait_for_state()** (6 connections) — `tests/slots/test_fail_watcher.py`
- **SlotManager** (6 connections)
- **test_fail_watcher_pushes_offline_when_unit_stops()** (6 connections) — `tests/slots/test_fail_watcher.py`
- **test_fail_watcher_demotes_to_error_when_health_fails()** (6 connections) — `tests/slots/test_fail_watcher.py`
- **Any** (5 connections)
- **FakeContainerProvider** (5 connections)
- **test_fail_watcher_emits_sse_frame_for_pushed_eviction()** (5 connections) — `tests/slots/test_fail_watcher.py`
- **test_fail_watcher_does_not_fire_when_slot_unloads_cleanly()** (5 connections) — `tests/slots/test_fail_watcher.py`
- **test_fail_watcher_keeps_ready_while_health_ok()** (5 connections) — `tests/slots/test_fail_watcher.py`
- **fast_fail_watch()** (3 connections) — `tests/slots/test_fail_watcher.py`
- **MonkeyPatch** (1 connections)
- **Tests for SlotManager's push-driven failure detector.  When a slot's container u** (1 connections) — `tests/slots/test_fail_watcher.py`
- **Tighten the fail-watch poll interval so tests run in <5s.      P3-slots §1b-watc** (1 connections) — `tests/slots/test_fail_watcher.py`
- **Poll the manager's in-memory state until ``target`` or timeout.** (1 connections) — `tests/slots/test_fail_watcher.py`
- **The unit going inactive while the slot is READY transitions to OFFLINE.      Uni** (1 connections) — `tests/slots/test_fail_watcher.py`
- **The watcher-triggered OFFLINE transition must broadcast to SSE subscribers.** (1 connections) — `tests/slots/test_fail_watcher.py`
- **A clean unload() must cancel the watcher; no spurious ERROR push.** (1 connections) — `tests/slots/test_fail_watcher.py`
- **#783/B4: a ready slot whose unit stays active but whose /health probe     starts** (1 connections) — `tests/slots/test_fail_watcher.py`
- **Guard: a healthy active slot must NOT be demoted by the watcher.** (1 connections) — `tests/slots/test_fail_watcher.py`

## Relationships

- [FakeContainerProvider](FakeContainerProvider.md) (2 shared connections)
- [SlotState](SlotState.md) (1 shared connections)

## Source Files

- `tests/slots/test_fail_watcher.py`

## Audit Trail

- EXTRACTED: 71 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*