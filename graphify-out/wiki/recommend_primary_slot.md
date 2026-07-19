# recommend_primary_slot

> 38 nodes

## Key Concepts

- **recommend_primary_slot()** (23 connections) — `src/hal0/hardware/recommend.py`
- **test_recommend.py** (19 connections) — `tests/hardware/test_recommend.py`
- **_amd_uma_host()** (15 connections) — `tests/hardware/test_recommend.py`
- **recommend.py** (8 connections) — `src/hal0/hardware/recommend.py`
- **_backend_for()** (5 connections) — `src/hal0/hardware/recommend.py`
- **test_cpu_only_host_seeds_chat_capable_profile()** (5 connections) — `tests/hardware/test_recommend.py`
- **_pick_chat_model()** (4 connections) — `src/hal0/hardware/recommend.py`
- **_resolve_primary_ctx()** (4 connections) — `src/hal0/hardware/recommend.py`
- **_vram_budget_gb()** (4 connections) — `src/hal0/hardware/recommend.py`
- **_cpu_only_host()** (4 connections) — `tests/hardware/test_recommend.py`
- **test_seeded_slot_validates_as_slotconfig()** (4 connections) — `tests/hardware/test_recommend.py`
- **nvidia_container_toolkit_present()** (3 connections) — `src/hal0/hardware/recommend.py`
- **_pick_cpu_model()** (3 connections) — `src/hal0/hardware/recommend.py`
- **test_96gb_strix_halo_seeds_35b_a3b()** (3 connections) — `tests/hardware/test_recommend.py`
- **test_48gb_seeds_35b_a3b()** (3 connections) — `tests/hardware/test_recommend.py`
- **test_32gb_seeds_9b()** (3 connections) — `tests/hardware/test_recommend.py`
- **test_16gb_seeds_9b()** (3 connections) — `tests/hardware/test_recommend.py`
- **test_8gb_seeds_4b()** (3 connections) — `tests/hardware/test_recommend.py`
- **test_moe_primary_gets_large_ctx()** (3 connections) — `tests/hardware/test_recommend.py`
- **test_9b_primary_gets_capped_ctx()** (3 connections) — `tests/hardware/test_recommend.py`
- **test_no_hardcoded_8192_for_moe()** (3 connections) — `tests/hardware/test_recommend.py`
- **test_cpu_host_gets_small_conservative_model()** (3 connections) — `tests/hardware/test_recommend.py`
- **test_seeded_slot_carries_container_runtime_and_profile()** (3 connections) — `tests/hardware/test_recommend.py`
- **test_seeded_slot_profile_is_a_known_seed_profile()** (3 connections) — `tests/hardware/test_recommend.py`
- **test_pick_chat_model_thresholds()** (2 connections) — `tests/hardware/test_recommend.py`
- *... and 13 more nodes in this community*

## Relationships

- [HardwareInfo](HardwareInfo.md) (5 shared connections)
- [get_curated](get_curated.md) (1 shared connections)
- [map_backend_to_device](map_backend_to_device.md) (1 shared connections)
- [test_probe.py](test_probe.py.md) (1 shared connections)
- [evaluate_model_fit](evaluate_model_fit.md) (1 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)
- [SlotConfig](SlotConfig.md) (1 shared connections)

## Source Files

- `src/hal0/hardware/recommend.py`
- `tests/hardware/test_recommend.py`

## Audit Trail

- EXTRACTED: 116 (78%)
- INFERRED: 33 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*