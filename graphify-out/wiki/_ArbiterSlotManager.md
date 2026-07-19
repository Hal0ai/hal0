# _ArbiterSlotManager

> 62 nodes

## Key Concepts

- **_ArbiterSlotManager** (27 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **test_arbiter_dispatch.py** (25 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **_ArbiterContainerSlotManager** (13 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **_wire_image_app()** (12 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **Path** (11 connections)
- **_make_dispatcher()** (11 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **test_drain_waits_for_request_past_guard_before_serving()** (10 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **MockTransport** (9 connections)
- **TestClient** (9 connections)
- **test_llm_slot_forward_guarded_in_img_mode()** (9 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **test_dead_port_in_img_mode_raises_gpu_image_mode()** (9 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **test_container_dead_port_in_img_mode_raises_gpu_image_mode()** (9 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **test_npu_and_cpu_slots_unaffected_in_img_mode()** (8 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **test_dead_port_in_llm_mode_raises_upstream_unavailable()** (8 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **test_container_dead_port_in_llm_mode_raises_upstream_unavailable()** (8 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **_slot_call()** (7 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **_seed_img_upstream()** (7 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **MonkeyPatch** (7 connections)
- **Any** (6 connections)
- **_write_img_mode_state()** (6 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **test_image_request_triggers_switch()** (6 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **test_img_activity_touched_on_completion()** (6 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **test_image_defaults_filled_from_image_section()** (6 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **test_image_defaults_do_not_override_explicit_values()** (6 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- **test_llm_request_in_img_mode_503_retry_after()** (6 connections) — `tests/dispatcher/test_arbiter_dispatch.py`
- *... and 37 more nodes in this community*

## Relationships

- [GpuImageMode](GpuImageMode.md) (5 shared connections)
- [_RecordingSlotManager](_RecordingSlotManager.md) (4 shared connections)
- [UpstreamCall](UpstreamCall.md) (4 shared connections)
- [Dispatcher](Dispatcher.md) (4 shared connections)
- [SlotState](SlotState.md) (4 shared connections)
- [GpuArbiter](GpuArbiter.md) (3 shared connections)
- [arbiter.py](arbiter.py.md) (2 shared connections)
- [Upstream](Upstream.md) (2 shared connections)
- [LlamaServerProvider](LlamaServerProvider.md) (1 shared connections)

## Source Files

- `tests/dispatcher/test_arbiter_dispatch.py`

## Audit Trail

- EXTRACTED: 283 (92%)
- INFERRED: 24 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*