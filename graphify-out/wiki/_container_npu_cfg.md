# _container_npu_cfg

> 28 nodes · cohesion 0.10

## Key Concepts

- **_container_npu_cfg()** (19 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **_slot_manager()** (18 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_error_means_settled()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_idle_means_settled()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_offline_means_settled()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_pulling_means_swap_in_progress()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_ready_means_settled()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_serving_means_settled()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_starting_means_swap_in_progress()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_unloading_means_swap_in_progress()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_warming_means_swap_in_progress()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_empty_slot_model_default_means_no_to_model()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_missing_model_section_means_no_to_model()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_status_raises_degrades_to_settled()** (4 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **SlotState.STARTING → in_progress=True (container restarting for model swap).** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **SlotState.PULLING → in_progress=True.** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **SlotState.WARMING → in_progress=True.** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **SlotState.UNLOADING → in_progress=True (transition still in flight).** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **SlotState.READY → in_progress=False, to_model populated from config.** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **SlotState.SERVING → in_progress=False (inference in flight, not a swap).** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **SlotState.IDLE → in_progress=False (warm but quiet).** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **SlotState.OFFLINE → in_progress=False (slot is not running).** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **SlotState.ERROR → in_progress=False (swap not in progress).** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **NPU LLM slot with empty model.default → to_model is None.** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **NPU LLM slot missing the [model] section entirely → to_model is None.** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- *... and 3 more nodes in this community*

## Relationships

- [test_npu_swap_status.py](test_npu_swap_status.py.md) (19 shared connections)
- [fetch_npu_swap_status](fetch_npu_swap_status.md) (17 shared connections)

## Source Files

- `tests/dispatcher/test_npu_swap_status.py`

## Audit Trail

- EXTRACTED: 98 (89%)
- INFERRED: 12 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*