# SlotState

> 76 nodes · cohesion 0.06

## Key Concepts

- **SlotState** (105 connections) — `src/hal0/slots/state.py`
- **_RecordingSlotManager** (31 connections) — `tests/dispatcher/test_serving_integration.py`
- **UpstreamUnavailable** (27 connections) — `src/hal0/dispatcher/router.py`
- **test_serving_integration.py** (27 connections) — `tests/dispatcher/test_serving_integration.py`
- **_make_dispatcher()** (22 connections) — `tests/dispatcher/test_serving_integration.py`
- **MockTransport** (19 connections)
- **_RecordingContainerSlotManager** (15 connections) — `tests/dispatcher/test_serving_integration.py`
- **SlotLoadFailed** (13 connections) — `src/hal0/dispatcher/router.py`
- **_slot_call()** (13 connections) — `tests/dispatcher/test_serving_integration.py`
- **.in_flight_count()** (12 connections) — `tests/dispatcher/test_serving_integration.py`
- **_RecordingCtx** (11 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_forward_gates_slot_in_loading_state()** (9 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_container_slot_network_error_releases_serving()** (8 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_forward_dead_port_raises_upstream_unavailable_single_try()** (8 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_forward_error_state_raises_slot_load_failed()** (8 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_forward_releases_serving_on_network_error()** (8 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_forward_streaming_dead_port_raises_upstream_unavailable()** (8 connections) — `tests/dispatcher/test_serving_integration.py`
- **_container_slot_call()** (7 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_backend_aware_load_failure_raises_slot_load_failed_immediately()** (7 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_container_slot_call_enters_and_exits_serving()** (7 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_container_slot_streaming_holds_serving_until_drain()** (7 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_forward_error_state_is_not_slot_loading()** (7 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_forward_passes_through_ready_states()** (7 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_forward_remote_connect_error_raises_upstream_unavailable()** (7 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_forward_remote_protocol_error_raises_upstream_unavailable()** (7 connections) — `tests/dispatcher/test_serving_integration.py`
- *... and 51 more nodes in this community*

## Relationships

- [Dispatcher](Dispatcher.md) (17 shared connections)
- [SlotConfigError](SlotConfigError.md) (15 shared connections)
- [UpstreamCall](UpstreamCall.md) (11 shared connections)
- [_ArbiterSlotManager](_ArbiterSlotManager.md) (8 shared connections)
- [slot](slot.md) (7 shared connections)
- [SlotLoading](SlotLoading.md) (6 shared connections)
- [_slot](_slot.md) (5 shared connections)
- [arbiter.py](arbiter.py.md) (4 shared connections)
- [SlotManager](SlotManager.md) (3 shared connections)
- [SlotInterface](SlotInterface.md) (3 shared connections)
- [ReaperHost](ReaperHost.md) (3 shared connections)
- [StackApplyEngine](StackApplyEngine.md) (3 shared connections)

## Source Files

- `src/hal0/dispatcher/router.py`
- `src/hal0/slots/manager.py`
- `src/hal0/slots/state.py`
- `tests/dispatcher/test_serving_integration.py`
- `tests/slots/test_dispatchable_ready_set_single_source.py`

## Audit Trail

- EXTRACTED: 371 (74%)
- INFERRED: 132 (26%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*