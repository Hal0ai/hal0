# UpstreamCall

> 62 nodes

## Key Concepts

- **UpstreamCall** (50 connections) — `src/hal0/dispatcher/router.py`
- **router.py** (28 connections) — `src/hal0/dispatcher/router.py`
- **.dispatch()** (19 connections) — `src/hal0/dispatcher/router.py`
- **.forward()** (12 connections) — `src/hal0/dispatcher/router.py`
- **._forward_direct()** (9 connections) — `src/hal0/dispatcher/router.py`
- **._forward_streaming()** (9 connections) — `src/hal0/dispatcher/router.py`
- **.arbiter()** (9 connections) — `src/hal0/slots/manager.py`
- **._forward_with_serving()** (8 connections) — `src/hal0/dispatcher/router.py`
- **._check_slot_ready_for_dispatch()** (7 connections) — `src/hal0/dispatcher/router.py`
- **._forward_plain()** (7 connections) — `src/hal0/dispatcher/router.py`
- **._guard_dead_port_image_mode()** (6 connections) — `src/hal0/dispatcher/router.py`
- **_resolve_target_url()** (5 connections) — `src/hal0/dispatcher/router.py`
- **._guard_gpu_image_mode()** (5 connections) — `src/hal0/dispatcher/router.py`
- **._ensure_slot_loaded_backend_aware()** (5 connections) — `src/hal0/dispatcher/router.py`
- **._check_container_slot_ready()** (5 connections) — `src/hal0/dispatcher/router.py`
- **._build_loading_response()** (5 connections) — `src/hal0/dispatcher/router.py`
- **._build_headers()** (5 connections) — `src/hal0/dispatcher/router.py`
- **_filter_response_headers()** (5 connections) — `src/hal0/dispatcher/router.py`
- **AsyncClient** (4 connections)
- **._get_http_client()** (4 connections) — `src/hal0/dispatcher/router.py`
- **Response** (4 connections)
- **._cold_prefetch()** (4 connections) — `src/hal0/dispatcher/router.py`
- **._log_decision()** (4 connections) — `src/hal0/dispatcher/router.py`
- **_remap_model()** (4 connections) — `src/hal0/dispatcher/router.py`
- **_slot_name_of()** (4 connections) — `src/hal0/dispatcher/router.py`
- *... and 37 more nodes in this community*

## Relationships

- [Dispatcher](Dispatcher.md) (33 shared connections)
- [_RecordingSlotManager](_RecordingSlotManager.md) (12 shared connections)
- [SlotLoading](SlotLoading.md) (7 shared connections)
- [Upstream](Upstream.md) (7 shared connections)
- [resolve_by_capability](resolve_by_capability.md) (6 shared connections)
- [_ArbiterSlotManager](_ArbiterSlotManager.md) (4 shared connections)
- [.record_error](record_error.md) (3 shared connections)
- [FakeUpstreamRegistry](FakeUpstreamRegistry.md) (3 shared connections)
- [SlotManager](SlotManager.md) (3 shared connections)
- [SingleFlightGroup](SingleFlightGroup.md) (2 shared connections)
- [SlotState](SlotState.md) (2 shared connections)
- [v1.py](v1.py.md) (2 shared connections)

## Source Files

- `src/hal0/dispatcher/router.py`
- `src/hal0/slots/manager.py`

## Audit Trail

- EXTRACTED: 242 (88%)
- INFERRED: 33 (12%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*