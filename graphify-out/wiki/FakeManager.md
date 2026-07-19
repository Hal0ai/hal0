# FakeManager

> 85 nodes · cohesion 0.06

## Key Concepts

- **FakeManager** (50 connections) — `tests/slots/test_gpu_arbiter.py`
- **test_gpu_arbiter.py** (49 connections) — `tests/slots/test_gpu_arbiter.py`
- **Path** (38 connections)
- **_read_state()** (18 connections) — `tests/slots/test_gpu_arbiter.py`
- **_img_mode_arbiter()** (9 connections) — `tests/slots/test_gpu_arbiter.py`
- **test_img_never_ready_rolls_back_on_timeout()** (9 connections) — `tests/slots/test_gpu_arbiter.py`
- **test_img_terminal_error_rolls_back_immediately()** (9 connections) — `tests/slots/test_gpu_arbiter.py`
- **_cancel_loop()** (8 connections) — `tests/slots/test_gpu_arbiter.py`
- **test_ensure_img_drain_blocks_on_committed_dispatch_ticket()** (8 connections) — `tests/slots/test_gpu_arbiter.py`
- **test_zero_window_from_toml_never_auto_restores()** (8 connections) — `tests/slots/test_gpu_arbiter.py`
- **Any** (7 connections)
- **LogCaptureFixture** (7 connections)
- **MonkeyPatch** (7 connections)
- **test_ensure_img_restamps_activity_at_completion()** (7 connections) — `tests/slots/test_gpu_arbiter.py`
- **test_existing_load_raises_rollback_still_works()** (7 connections) — `tests/slots/test_gpu_arbiter.py`
- **test_guard_uses_derived_group_from_slot_toml()** (7 connections) — `tests/slots/test_gpu_arbiter.py`
- **test_idle_loop_survives_restore_exception()** (7 connections) — `tests/slots/test_gpu_arbiter.py`
- **test_idle_tick_defers_and_restamps_when_comfyui_queue_busy()** (7 connections) — `tests/slots/test_gpu_arbiter.py`
- **test_img_eventually_ready_succeeds()** (7 connections) — `tests/slots/test_gpu_arbiter.py`
- **test_img_load_failure_rollback_load_also_fails()** (7 connections) — `tests/slots/test_gpu_arbiter.py`
- **test_img_load_failure_rolls_back_llm_set()** (7 connections) — `tests/slots/test_gpu_arbiter.py`
- **test_concurrent_ensure_img_and_restore_serialize()** (6 connections) — `tests/slots/test_gpu_arbiter.py`
- **test_drain_timeout_proceeds()** (6 connections) — `tests/slots/test_gpu_arbiter.py`
- **test_ensure_img_skips_load_when_img_already_running()** (6 connections) — `tests/slots/test_gpu_arbiter.py`
- **test_guard_dispatch_raises_in_img_mode()** (6 connections) — `tests/slots/test_gpu_arbiter.py`
- *... and 60 more nodes in this community*

## Relationships

- [GpuArbiter](GpuArbiter.md) (30 shared connections)
- [arbiter.py](arbiter.py.md) (9 shared connections)
- [GpuImageMode](GpuImageMode.md) (4 shared connections)
- [SlotManager](SlotManager.md) (4 shared connections)
- [errors.py](errors.py.md) (3 shared connections)
- [evalrun.py](evalrun.py.md) (1 shared connections)

## Source Files

- `tests/slots/test_gpu_arbiter.py`

## Audit Trail

- EXTRACTED: 441 (96%)
- INFERRED: 16 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*