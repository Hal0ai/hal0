# test_npu_swap_status.py

> 19 nodes · cohesion 0.15

## Key Concepts

- **test_npu_swap_status.py** (27 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_no_npu_slot_means_no_swap()** (6 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_swap_in_progress_with_gpu_peers_present()** (6 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **_make_slot()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **Any** (5 connections)
- **test_swap_status_endpoint_observes_npu_slot()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **_gpu_slot()** (4 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **_noncontainer_npu_cfg()** (4 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_noncontainer_npu_slot_settled_with_to_model()** (4 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_swap_status_endpoint_returns_default_shape()** (3 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **MonkeyPatch** (1 connections)
- **npu_swap_status: container slot lifecycle state drives the swap signal.  ``fetch** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **Legacy/unmigrated NPU record → no live container to observe.      The snapshot i** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **Non-NPU peer slots don't affect the swap signal.** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **Build a Slot-like mock whose .state is a real SlotState enum value.** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **GET /api/npu/swap-status returns the shape even with no NPU configured.** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **When a container NPU LLM slot is configured and its lifecycle state is     trans** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **Slot config for a legacy/unmigrated (non-container) NPU LLM slot.** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **Nothing configured → all-None settled snapshot.** (1 connections) — `tests/dispatcher/test_npu_swap_status.py`

## Relationships

- [_container_npu_cfg](_container_npu_cfg.md) (19 shared connections)
- [fetch_npu_swap_status](fetch_npu_swap_status.md) (8 shared connections)
- [SlotState](SlotState.md) (1 shared connections)

## Source Files

- `tests/dispatcher/test_npu_swap_status.py`

## Audit Trail

- EXTRACTED: 73 (94%)
- INFERRED: 5 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*