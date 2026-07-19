# test_pulling_serving_idle.py

> 37 nodes · cohesion 0.11

## Key Concepts

- **test_pulling_serving_idle.py** (20 connections) — `tests/slots/test_pulling_serving_idle.py`
- **Path** (17 connections)
- **FakeContainerProvider** (16 connections)
- **_write_min_slot()** (10 connections) — `tests/slots/test_pulling_serving_idle.py`
- **test_evicted_slot_wakes_on_next_request()** (6 connections) — `tests/slots/test_pulling_serving_idle.py`
- **test_explicit_positive_ttl_is_honored()** (6 connections) — `tests/slots/test_pulling_serving_idle.py`
- **test_idle_monitor_demotes_ready_to_idle()** (6 connections) — `tests/slots/test_pulling_serving_idle.py`
- **test_idle_sweep_does_not_evict_default_anchor()** (6 connections) — `tests/slots/test_pulling_serving_idle.py`
- **test_idle_sweep_never_evicts_serving_slot()** (6 connections) — `tests/slots/test_pulling_serving_idle.py`
- **test_idle_sweep_pins_slot_with_zero_timeout()** (6 connections) — `tests/slots/test_pulling_serving_idle.py`
- **test_idle_sweep_unloads_slot_past_ttl()** (6 connections) — `tests/slots/test_pulling_serving_idle.py`
- **test_idle_monitor_skips_serving_slots()** (5 connections) — `tests/slots/test_pulling_serving_idle.py`
- **test_load_skips_pulling_when_model_cached()** (5 connections) — `tests/slots/test_pulling_serving_idle.py`
- **test_load_transitions_through_pulling_when_not_cached()** (5 connections) — `tests/slots/test_pulling_serving_idle.py`
- **test_load_without_pull_runner_never_enters_pulling()** (5 connections) — `tests/slots/test_pulling_serving_idle.py`
- **test_pull_runner_failure_flips_to_error()** (5 connections) — `tests/slots/test_pulling_serving_idle.py`
- **test_serving_concurrent_requests_keep_state_serving()** (5 connections) — `tests/slots/test_pulling_serving_idle.py`
- **test_serving_from_idle_returns_to_ready()** (5 connections) — `tests/slots/test_pulling_serving_idle.py`
- **test_serving_resets_idle_clock()** (5 connections) — `tests/slots/test_pulling_serving_idle.py`
- **test_serving_context_flips_ready_to_serving_and_back()** (4 connections) — `tests/slots/test_pulling_serving_idle.py`
- **Tests for the three slot states wired in task #10.  Covers PLAN.md §5 state mach** (1 connections) — `tests/slots/test_pulling_serving_idle.py`
- **No pull_runner wired → legacy offline → starting → warming → ready.** (1 connections) — `tests/slots/test_pulling_serving_idle.py`
- **A raising pull_runner surfaces as ERROR + the exception propagates.** (1 connections) — `tests/slots/test_pulling_serving_idle.py`
- **N concurrent requests must NOT toggle READY↔SERVING mid-flight.** (1 connections) — `tests/slots/test_pulling_serving_idle.py`
- **A request that lands on an IDLE slot wakes it to SERVING → READY.** (1 connections) — `tests/slots/test_pulling_serving_idle.py`
- *... and 12 more nodes in this community*

## Relationships

- [SlotManager](SlotManager.md) (16 shared connections)
- [conftest.py](conftest.py.md) (1 shared connections)
- [FakeContainerProvider](FakeContainerProvider.md) (1 shared connections)

## Source Files

- `tests/slots/test_pulling_serving_idle.py`

## Audit Trail

- EXTRACTED: 150 (90%)
- INFERRED: 16 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*