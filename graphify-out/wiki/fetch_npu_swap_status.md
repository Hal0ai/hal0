# fetch_npu_swap_status

> 20 nodes · cohesion 0.14

## Key Concepts

- **fetch_npu_swap_status()** (26 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **NpuSwapStatus** (9 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **npu_swap_status.py** (5 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **test_container_via_runtime_field_also_uses_container_path()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_disabled_npu_slot_ignored()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_no_slot_manager_means_all_none_settled()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **_enabled_npu_llm_slot()** (4 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **Any** (4 connections)
- **_slot_model_default()** (4 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **test_to_dict_shape()** (3 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **.to_dict()** (2 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **NPU trio chat-model swap-in-progress detection.  When the operator picks a new N** (1 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **Return the swap snapshot from the npu container slot's state.      Transitional** (1 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **Snapshot of the NPU trio swap state.      Attributes:         in_progress: True** (1 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **Return the (at most one) enabled NPU LLM slot config, or None.      The NPU-excl** (1 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **Pull ``model.default`` out of a slot config dict.** (1 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **No slot_manager wired → all-None settled snapshot (test bypass).** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **runtime='container' with no profile also triggers the container path.** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **NpuSwapStatus.to_dict matches the wire shape.** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **A disabled NPU LLM slot doesn't drive a swap.** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`

## Relationships

- [_container_npu_cfg](_container_npu_cfg.md) (17 shared connections)
- [test_npu_swap_status.py](test_npu_swap_status.py.md) (8 shared connections)
- [npu_occupancy](npu_occupancy.md) (2 shared connections)
- [is_container_npu_cfg](is_container_npu_cfg.md) (1 shared connections)
- [SlotState](SlotState.md) (1 shared connections)

## Source Files

- `src/hal0/dispatcher/npu_swap_status.py`
- `tests/dispatcher/test_npu_swap_status.py`

## Audit Trail

- EXTRACTED: 51 (63%)
- INFERRED: 30 (37%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*