# test_npu_trio.py

> 19 nodes · cohesion 0.15

## Key Concepts

- **test_npu_trio.py** (24 connections) — `tests/dispatcher/test_npu_trio.py`
- **_slot_manager_with_container_npu()** (17 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_container_npu_resolves_static_port()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_container_npu_via_runtime_field_resolves_static_port()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_disabled_container_resolves_none()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_idle_container_npu_resolves_static_port()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_missing_port_resolves_none()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_non_ready_container_resolves_none()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_serving_container_npu_resolves_static_port()** (4 connections) — `tests/dispatcher/test_npu_trio.py`
- **test_offline_container_resolves_none()** (3 connections) — `tests/dispatcher/test_npu_trio.py`
- **NpuTrioRouter: container-only static-port dispatch (Phase E).  Covers:   - ready** (1 connections) — `tests/dispatcher/test_npu_trio.py`
- **SERVING (inference in flight) still resolves — a concurrent STT/embed     reques** (1 connections) — `tests/dispatcher/test_npu_trio.py`
- **IDLE npu container → static port.      IDLE = "warm but quiet" (no in-flight inf** (1 connections) — `tests/dispatcher/test_npu_trio.py`
- **Container npu slot still starting up → None (trio not available).      Uses STAR** (1 connections) — `tests/dispatcher/test_npu_trio.py`
- **enabled=False → not a live container target → None.** (1 connections) — `tests/dispatcher/test_npu_trio.py`
- **Container npu config without a port → None (nothing to dial).** (1 connections) — `tests/dispatcher/test_npu_trio.py`
- **SlotManager mock for a container npu slot.      Mocks ``get_config`` plus the #6** (1 connections) — `tests/dispatcher/test_npu_trio.py`
- **Ready container npu slot → static URL from the slot config port.** (1 connections) — `tests/dispatcher/test_npu_trio.py`
- **runtime='container' with no profile also qualifies as container slot.** (1 connections) — `tests/dispatcher/test_npu_trio.py`

## Relationships

- [_mock_transport](_mock_transport.md) (11 shared connections)
- [NpuTrioRouter](NpuTrioRouter.md) (8 shared connections)
- [NpuTrioNotAvailable](NpuTrioNotAvailable.md) (4 shared connections)
- [test_dispatch_stt_raises_when_no_container_npu_slot](test_dispatch_stt_raises_when_no_container_npu_slot.md) (3 shared connections)
- [test_get_config_raises_resolves_none](test_get_config_raises_resolves_none.md) (2 shared connections)
- [test_is_ready_for_dispatch_raises_resolves_none](test_is_ready_for_dispatch_raises_resolves_none.md) (1 shared connections)

## Source Files

- `tests/dispatcher/test_npu_trio.py`

## Audit Trail

- EXTRACTED: 73 (90%)
- INFERRED: 8 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*