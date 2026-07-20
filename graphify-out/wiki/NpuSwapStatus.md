# NpuSwapStatus

> 12 nodes

## Key Concepts

- **NpuSwapStatus** (9 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **npu_swap_status.py** (5 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **Any** (4 connections)
- **_enabled_npu_llm_slot()** (4 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **_slot_model_default()** (4 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **test_to_dict_shape()** (3 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **.to_dict()** (2 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **NPU trio chat-model swap-in-progress detection.  When the operator picks a new N** (1 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **Snapshot of the NPU trio swap state.      Attributes:         in_progress: True** (1 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **Return the (at most one) enabled NPU LLM slot config, or None.      The NPU-excl** (1 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **Pull ``model.default`` out of a slot config dict.** (1 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **NpuSwapStatus.to_dict matches the wire shape.** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`

## Relationships

- [test_npu_swap_status.py](test_npu_swap_status.py.md) (8 shared connections)
- [npu_occupancy](npu_occupancy.md) (1 shared connections)
- [SlotState](SlotState.md) (1 shared connections)

## Source Files

- `src/hal0/dispatcher/npu_swap_status.py`
- `tests/dispatcher/test_npu_swap_status.py`

## Audit Trail

- EXTRACTED: 30 (83%)
- INFERRED: 6 (17%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*