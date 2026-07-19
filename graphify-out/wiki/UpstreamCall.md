# UpstreamCall

> 29 nodes · cohesion 0.12

## Key Concepts

- **UpstreamCall** (50 connections) — `src/hal0/dispatcher/router.py`
- **.forward()** (12 connections) — `src/hal0/dispatcher/router.py`
- **._forward_direct()** (9 connections) — `src/hal0/dispatcher/router.py`
- **._forward_streaming()** (9 connections) — `src/hal0/dispatcher/router.py`
- **.arbiter()** (9 connections) — `src/hal0/slots/manager.py`
- **._forward_with_serving()** (8 connections) — `src/hal0/dispatcher/router.py`
- **._check_slot_ready_for_dispatch()** (7 connections) — `src/hal0/dispatcher/router.py`
- **._forward_plain()** (7 connections) — `src/hal0/dispatcher/router.py`
- **._guard_dead_port_image_mode()** (6 connections) — `src/hal0/dispatcher/router.py`
- **._build_loading_response()** (5 connections) — `src/hal0/dispatcher/router.py`
- **._check_container_slot_ready()** (5 connections) — `src/hal0/dispatcher/router.py`
- **._ensure_slot_loaded_backend_aware()** (5 connections) — `src/hal0/dispatcher/router.py`
- **._guard_gpu_image_mode()** (5 connections) — `src/hal0/dispatcher/router.py`
- **_filter_response_headers()** (5 connections) — `src/hal0/dispatcher/router.py`
- **._get_http_client()** (4 connections) — `src/hal0/dispatcher/router.py`
- **AsyncClient** (4 connections)
- **Response** (4 connections)
- **StreamingResponse** (1 connections)
- **Drop hop-by-hop and length headers so Starlette can recompute them.** (1 connections) — `src/hal0/dispatcher/router.py`
- **A fully-resolved routing decision ready to be forwarded.      Mirrors the shape** (1 connections) — `src/hal0/dispatcher/router.py`
- **Execute the HTTP forward and return a FastAPI Response.          Two paths:** (1 connections) — `src/hal0/dispatcher/router.py`
- **Refuse llm-group dispatch while the GPU is in exclusive image mode.          Del** (1 connections) — `src/hal0/dispatcher/router.py`
- **Kick a backend-aware load on a cold miss before forwarding.          B1 — the na** (1 connections) — `src/hal0/dispatcher/router.py`
- **Raise :class:`SlotLoading` if a container-backed slot isn't ready.          Dele** (1 connections) — `src/hal0/dispatcher/router.py`
- **Raise a typed error if the target slot isn't ready to serve.          Ready set:** (1 connections) — `src/hal0/dispatcher/router.py`
- *... and 4 more nodes in this community*

## Relationships

- [Dispatcher](Dispatcher.md) (21 shared connections)
- [SlotState](SlotState.md) (11 shared connections)
- [Upstream](Upstream.md) (5 shared connections)
- [SlotLoading](SlotLoading.md) (4 shared connections)
- [_ArbiterSlotManager](_ArbiterSlotManager.md) (4 shared connections)
- [router.py](router.py.md) (3 shared connections)
- [SlotManager](SlotManager.md) (3 shared connections)
- [.record_error](record_error.md) (3 shared connections)
- [FakeUpstreamRegistry](FakeUpstreamRegistry.md) (3 shared connections)
- [SingleFlightGroup](SingleFlightGroup.md) (2 shared connections)
- [v1.py](v1.py.md) (2 shared connections)
- [_Headers](_Headers.md) (1 shared connections)

## Source Files

- `src/hal0/dispatcher/router.py`
- `src/hal0/slots/manager.py`

## Audit Trail

- EXTRACTED: 134 (81%)
- INFERRED: 32 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*