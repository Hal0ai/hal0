# SlotLoading

> 34 nodes · cohesion 0.11

## Key Concepts

- **SlotLoading** (20 connections) — `src/hal0/dispatcher/router.py`
- **test_container_gate.py** (16 connections) — `tests/dispatcher/test_container_gate.py`
- **_make_dispatcher_with_manager()** (8 connections) — `tests/dispatcher/test_container_gate.py`
- **_ok_transport()** (8 connections) — `tests/dispatcher/test_container_gate.py`
- **_container_slot_name_of()** (7 connections) — `src/hal0/dispatcher/router.py`
- **_container_call()** (7 connections) — `tests/dispatcher/test_container_gate.py`
- **test_container_gate_crashed_raises_slot_loading()** (7 connections) — `tests/dispatcher/test_container_gate.py`
- **test_container_gate_inactive_raises_slot_loading_not_502()** (7 connections) — `tests/dispatcher/test_container_gate.py`
- **test_container_gate_starting_raises_slot_loading()** (7 connections) — `tests/dispatcher/test_container_gate.py`
- **_slot_manager_stub()** (6 connections) — `tests/dispatcher/test_container_gate.py`
- **test_container_gate_ready_passes_through_to_upstream()** (6 connections) — `tests/dispatcher/test_container_gate.py`
- **_remote_upstream()** (5 connections) — `tests/dispatcher/test_container_gate.py`
- **test_container_gate_no_slot_manager_skips_gate()** (5 connections) — `tests/dispatcher/test_container_gate.py`
- **test_slot_kind_upstream_unaffected_by_container_gate()** (5 connections) — `tests/dispatcher/test_container_gate.py`
- **_slot_upstream()** (4 connections) — `tests/dispatcher/test_container_gate.py`
- **test_container_slot_name_of_remote_with_slot_name()** (4 connections) — `tests/dispatcher/test_container_gate.py`
- **test_container_slot_name_of_remote_without_slot_name()** (4 connections) — `tests/dispatcher/test_container_gate.py`
- **test_container_slot_name_of_slot_kind()** (4 connections) — `tests/dispatcher/test_container_gate.py`
- **MockTransport** (2 connections)
- **Return the container slot name for a ``kind=remote`` container-backed upstream.** (1 connections) — `src/hal0/dispatcher/router.py`
- **The target slot is mid-swap — model is starting/loading/unloading.      Raised b** (1 connections) — `src/hal0/dispatcher/router.py`
- **Tests for the container-slot readiness gate in ``Dispatcher.forward``.  Issue #6** (1 connections) — `tests/dispatcher/test_container_gate.py`
- **Build a SlotManager mock whose container_readiness_check is wired.** (1 connections) — `tests/dispatcher/test_container_gate.py`
- **Returns slot_name when the remote upstream was registered as container-backed.** (1 connections) — `tests/dispatcher/test_container_gate.py`
- **Returns empty string for genuine remotes (no slot_name set).** (1 connections) — `tests/dispatcher/test_container_gate.py`
- *... and 9 more nodes in this community*

## Relationships

- [Dispatcher](Dispatcher.md) (9 shared connections)
- [SlotState](SlotState.md) (6 shared connections)
- [UpstreamCall](UpstreamCall.md) (4 shared connections)
- [Upstream](Upstream.md) (3 shared connections)
- [router.py](router.py.md) (2 shared connections)
- [SingleFlightGroup](SingleFlightGroup.md) (1 shared connections)

## Source Files

- `src/hal0/dispatcher/router.py`
- `tests/dispatcher/test_container_gate.py`

## Audit Trail

- EXTRACTED: 123 (84%)
- INFERRED: 24 (16%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*