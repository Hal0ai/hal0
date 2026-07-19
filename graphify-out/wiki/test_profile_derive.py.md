# test_profile_derive.py

> 42 nodes · cohesion 0.09

## Key Concepts

- **test_profile_derive.py** (23 connections) — `tests/install/test_profile_derive.py`
- **derive_profile()** (21 connections) — `src/hal0/install/profile_derive.py`
- **derive_device()** (15 connections) — `src/hal0/install/profile_derive.py`
- **_hw()** (13 connections) — `tests/install/test_profile_derive.py`
- **profile_derive.py** (5 connections) — `src/hal0/install/profile_derive.py`
- **npu_takes_utility()** (5 connections) — `src/hal0/install/profile_derive.py`
- **_cpu_hw()** (5 connections) — `tests/install/test_profile_derive.py`
- **test_embed_on_npu_box_derives_to_gpu_not_npu()** (5 connections) — `tests/install/test_profile_derive.py`
- **test_utility_capability_routes_like_chat_lane()** (5 connections) — `tests/install/test_profile_derive.py`
- **test_chat_on_rocm_box_picks_rocm()** (4 connections) — `tests/install/test_profile_derive.py`
- **test_chat_on_vulkan_only_box_picks_vulkan()** (4 connections) — `tests/install/test_profile_derive.py`
- **test_cpu_host_chat_derives_cpu_device()** (4 connections) — `tests/install/test_profile_derive.py`
- **test_npu_chat_lane_requires_present_and_optin()** (4 connections) — `tests/install/test_profile_derive.py`
- **test_npu_present_is_chat_only_no_trio_passengers()** (4 connections) — `tests/install/test_profile_derive.py`
- **test_npu_takes_utility_when_present_and_optin()** (4 connections) — `tests/install/test_profile_derive.py`
- **test_tts_is_cpu_kokoro()** (4 connections) — `tests/install/test_profile_derive.py`
- **test_cpu_host_chat_derives_cpu_llm_profile()** (3 connections) — `tests/install/test_profile_derive.py`
- **test_cpu_host_coder_derives_cpu_llm_profile()** (3 connections) — `tests/install/test_profile_derive.py`
- **test_cpu_host_embed_derives_cpu_llm_profile()** (3 connections) — `tests/install/test_profile_derive.py`
- **test_cpu_host_tts_still_derives_tts_profile()** (3 connections) — `tests/install/test_profile_derive.py`
- **test_strix_platform_forces_rocm_even_if_compute_flag_missing()** (3 connections) — `tests/install/test_profile_derive.py`
- **test_cpu_llm_profile_exists_in_seed_profiles()** (2 connections) — `tests/install/test_profile_derive.py`
- **test_embed_on_rocm_box_uses_embed_profile()** (2 connections) — `tests/install/test_profile_derive.py`
- **test_embed_on_vulkan_box_uses_vulkan_embed_profile()** (2 connections) — `tests/install/test_profile_derive.py`
- **test_rerank_on_rocm_box_uses_rerank_profile()** (2 connections) — `tests/install/test_profile_derive.py`
- *... and 17 more nodes in this community*

## Relationships

- [HardwareInfo](HardwareInfo.md) (6 shared connections)
- [test_profile_derivation_parity.py](test_profile_derivation_parity.py.md) (3 shared connections)
- [build_auto_selections](build_auto_selections.md) (2 shared connections)
- [orchestrate.py](orchestrate.py.md) (2 shared connections)
- [suggest_models](suggest_models.md) (2 shared connections)
- [test_probe.py](test_probe.py.md) (1 shared connections)

## Source Files

- `src/hal0/install/profile_derive.py`
- `tests/install/test_profile_derive.py`

## Audit Trail

- EXTRACTED: 106 (64%)
- INFERRED: 60 (36%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*