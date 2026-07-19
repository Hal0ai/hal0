# test_npu_swap_status.py

> 50 nodes

## Key Concepts

- **test_npu_swap_status.py** (27 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **fetch_npu_swap_status()** (26 connections) — `src/hal0/dispatcher/npu_swap_status.py`
- **_container_npu_cfg()** (19 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **_slot_manager()** (18 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_no_npu_slot_means_no_swap()** (6 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_swap_in_progress_with_gpu_peers_present()** (6 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **Any** (5 connections)
- **test_disabled_npu_slot_ignored()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_no_slot_manager_means_all_none_settled()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_starting_means_swap_in_progress()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_pulling_means_swap_in_progress()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_warming_means_swap_in_progress()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_unloading_means_swap_in_progress()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_ready_means_settled()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_serving_means_settled()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_idle_means_settled()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_offline_means_settled()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_error_means_settled()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_via_runtime_field_also_uses_container_path()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_empty_slot_model_default_means_no_to_model()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_missing_model_section_means_no_to_model()** (5 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **_noncontainer_npu_cfg()** (4 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **_gpu_slot()** (4 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_noncontainer_npu_slot_settled_with_to_model()** (4 connections) — `tests/dispatcher/test_npu_swap_status.py`
- **test_container_status_raises_degrades_to_settled()** (4 connections) — `tests/dispatcher/test_npu_swap_status.py`
- *... and 25 more nodes in this community*

## Relationships

- [NpuSwapStatus](NpuSwapStatus.md) (8 shared connections)
- [_make_slot](_make_slot.md) (4 shared connections)
- [npu_occupancy](npu_occupancy.md) (1 shared connections)
- [._post](_post.md) (1 shared connections)

## Source Files

- `src/hal0/dispatcher/npu_swap_status.py`
- `tests/dispatcher/test_npu_swap_status.py`

## Audit Trail

- EXTRACTED: 180 (82%)
- INFERRED: 40 (18%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*