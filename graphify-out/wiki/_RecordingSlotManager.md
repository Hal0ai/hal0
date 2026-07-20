# _RecordingSlotManager

> 68 nodes

## Key Concepts

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
- **test_forward_releases_serving_on_network_error()** (8 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_forward_error_state_raises_slot_load_failed()** (8 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_forward_dead_port_raises_upstream_unavailable_single_try()** (8 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_forward_streaming_dead_port_raises_upstream_unavailable()** (8 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_container_slot_network_error_releases_serving()** (8 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_forward_streaming_holds_serving_until_drain()** (7 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_forward_error_state_is_not_slot_loading()** (7 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_backend_aware_load_failure_raises_slot_load_failed_immediately()** (7 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_forward_passes_through_ready_states()** (7 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_forward_remote_connect_error_raises_upstream_unavailable()** (7 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_forward_remote_protocol_error_raises_upstream_unavailable()** (7 connections) — `tests/dispatcher/test_serving_integration.py`
- **_container_slot_call()** (7 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_container_slot_call_enters_and_exits_serving()** (7 connections) — `tests/dispatcher/test_serving_integration.py`
- **test_container_slot_streaming_holds_serving_until_drain()** (7 connections) — `tests/dispatcher/test_serving_integration.py`
- *... and 43 more nodes in this community*

## Relationships

- [UpstreamCall](UpstreamCall.md) (12 shared connections)
- [SlotState](SlotState.md) (11 shared connections)
- [Dispatcher](Dispatcher.md) (9 shared connections)
- [SlotLoading](SlotLoading.md) (5 shared connections)
- [_ArbiterSlotManager](_ArbiterSlotManager.md) (4 shared connections)
- [v1.py](v1.py.md) (2 shared connections)
- [SingleFlightGroup](SingleFlightGroup.md) (2 shared connections)
- [test_forward.py](test_forward.py.md) (2 shared connections)
- [test_pool_bounds.py](test_pool_bounds.py.md) (2 shared connections)
- [Upstream](Upstream.md) (1 shared connections)

## Source Files

- `src/hal0/dispatcher/router.py`
- `tests/dispatcher/test_serving_integration.py`

## Audit Trail

- EXTRACTED: 323 (85%)
- INFERRED: 59 (15%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*