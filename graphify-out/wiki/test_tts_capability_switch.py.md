# test_tts_capability_switch.py

> 22 nodes · cohesion 0.12

## Key Concepts

- **test_tts_capability_switch.py** (16 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_apply_disabled_tts_selection_does_not_rewrite_profile()** (6 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_apply_selecting_kokoro_cpu_swaps_tts_slot_back_to_kokoro_profile()** (6 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_apply_selecting_qwen3_gpu_swaps_tts_slot_to_qwen3_profile()** (6 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **_read_tts_slot()** (5 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **_write_tts_slot()** (5 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_apply_non_tts_child_does_not_write_profile()** (4 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_kokoro_row_offers_cpu_backend()** (2 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_orchestrator_profile_for_fit_non_tts_gpu_unchanged()** (2 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_orchestrator_profile_for_fit_tts_cpu_is_kokoro()** (2 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_orchestrator_profile_for_fit_tts_gpu_is_qwen3()** (2 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_qwen3_row_offers_gpu_rocm_backend()** (2 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_voice_tts_catalog_enumerates_both_engines()** (2 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **Any** (1 connections)
- **voice.tts capability switch — Kokoro (CPU) vs Qwen3-TTS (GPU/ROCm).  The deferre** (1 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **Write the canonical single ``tts`` slot in a known engine state.** (1 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **Picking qwen3 / gpu-rocm rewrites the ``tts`` slot's profile to tts-qwen3.** (1 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **Picking kokoro / cpu reverts the ``tts`` slot to the Kokoro profile.      Starti** (1 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **A disabled selection flips ``enabled`` but never rewrites the engine.      Post-** (1 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **Regression: a non-tts child's slot reconciliation never injects a profile.** (1 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_catalog_profile_for_fit_tts_cpu_is_kokoro()** (1 connections) — `tests/capabilities/test_tts_capability_switch.py`
- **test_catalog_profile_for_fit_tts_gpu_is_qwen3()** (1 connections) — `tests/capabilities/test_tts_capability_switch.py`

## Relationships

- [CapabilitySelection](CapabilitySelection.md) (4 shared connections)
- [SlotConfigStore](SlotConfigStore.md) (4 shared connections)
- [catalog.py](catalog.py.md) (3 shared connections)
- [CapabilityOrchestrator](CapabilityOrchestrator.md) (3 shared connections)
- [unknown_slot_config_keys](unknown_slot_config_keys.md) (1 shared connections)

## Source Files

- `tests/capabilities/test_tts_capability_switch.py`

## Audit Trail

- EXTRACTED: 59 (86%)
- INFERRED: 10 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*