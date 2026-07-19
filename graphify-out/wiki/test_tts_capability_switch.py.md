# test_tts_capability_switch.py

> 33 nodes

## Key Concepts

- **test_tts_capability_switch.py** (16 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **profile_name_for_fit()** (7 connections) — `src/hal0/capabilities/profile_fit.py`
- **tts_profile_for_device()** (6 connections) — `src/hal0/capabilities/catalog.py`
- **_read_tts_slot()** (5 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **_write_tts_slot()** (5 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_apply_selecting_qwen3_gpu_swaps_tts_slot_to_qwen3_profile()** (5 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_apply_selecting_kokoro_cpu_swaps_tts_slot_back_to_kokoro_profile()** (5 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_apply_disabled_tts_selection_does_not_rewrite_profile()** (5 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_apply_non_tts_child_does_not_write_profile()** (3 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_profile_for_fit_matches_shared_helper()** (3 connections) — `tests/config/test_profile_derivation_parity.py`
- **test_fit_helper_is_non_mtp_on_rocm()** (3 connections) — `tests/config/test_profile_derivation_parity.py`
- **profile_fit.py** (2 connections) — `src/hal0/capabilities/profile_fit.py`
- **test_voice_tts_catalog_enumerates_both_engines()** (2 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_kokoro_row_offers_cpu_backend()** (2 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_qwen3_row_offers_gpu_rocm_backend()** (2 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_orchestrator_profile_for_fit_tts_gpu_is_qwen3()** (2 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_orchestrator_profile_for_fit_tts_cpu_is_kokoro()** (2 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_orchestrator_profile_for_fit_non_tts_gpu_unchanged()** (2 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_tts_profile_for_device_mapping()** (2 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **Return the TTS slot profile a device selection implies.      The engine switch:** (1 connections) — `src/hal0/capabilities/catalog.py`
- **Shared picker/apply profile-fit inference (device → runtime profile name).  Sing** (1 connections) — `src/hal0/capabilities/profile_fit.py`
- **Infer the runtime profile name implied by a picker/apply selection.      Keeps i** (1 connections) — `src/hal0/capabilities/profile_fit.py`
- **test_catalog_profile_for_fit_tts_gpu_is_qwen3()** (1 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_catalog_profile_for_fit_tts_cpu_is_kokoro()** (1 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **Any** (1 connections)
- *... and 8 more nodes in this community*

## Relationships

- [catalog.py](catalog.py.md) (5 shared connections)
- [CapabilitySelection](CapabilitySelection.md) (4 shared connections)
- [CapabilityOrchestrator](CapabilityOrchestrator.md) (3 shared connections)
- [test_profile_derive.py](test_profile_derive.py.md) (2 shared connections)
- [.apply](apply.md) (1 shared connections)
- [unknown_slot_config_keys](unknown_slot_config_keys.md) (1 shared connections)
- [ProfileConfig](ProfileConfig.md) (1 shared connections)

## Source Files

- `src/hal0/capabilities/catalog.py`
- `src/hal0/capabilities/profile_fit.py`
- `tests/capabilities/test_tts_capability_switch.py`
- `tests/config/test_profile_derivation_parity.py`

## Audit Trail

- EXTRACTED: 75 (81%)
- INFERRED: 18 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*