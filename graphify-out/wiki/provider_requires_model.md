# provider_requires_model

> 14 nodes · cohesion 0.22

## Key Concepts

- **provider_requires_model()** (12 connections) — `src/hal0/slots/state.py`
- **test_state_self_managed_providers.py** (10 connections) — `tests/slots/test_state_self_managed_providers.py`
- **test_flm_requires_model()** (2 connections) — `tests/slots/test_state_self_managed_providers.py`
- **test_kokoro_does_not_require_model()** (2 connections) — `tests/slots/test_state_self_managed_providers.py`
- **test_llama_server_requires_model()** (2 connections) — `tests/slots/test_state_self_managed_providers.py`
- **test_moonshine_does_not_require_model()** (2 connections) — `tests/slots/test_state_self_managed_providers.py`
- **test_provider_check_is_case_insensitive()** (2 connections) — `tests/slots/test_state_self_managed_providers.py`
- **test_provider_check_is_none_safe()** (2 connections) — `tests/slots/test_state_self_managed_providers.py`
- **test_qwen3tts_does_not_require_model()** (2 connections) — `tests/slots/test_state_self_managed_providers.py`
- **test_self_managed_providers_set_matches_ui()** (2 connections) — `tests/slots/test_state_self_managed_providers.py`
- **test_vibevoice_does_not_require_model()** (2 connections) — `tests/slots/test_state_self_managed_providers.py`
- **True when a slot of this provider needs an explicit model_id to serve.** (1 connections) — `src/hal0/slots/state.py`
- **Tests for the SELF_MANAGED_PROVIDERS gate.  Some providers (kokoro, qwen3tts, mo** (1 connections) — `tests/slots/test_state_self_managed_providers.py`
- **The Python set must stay in sync with the UI's SELF_MANAGED_PROVIDERS.** (1 connections) — `tests/slots/test_state_self_managed_providers.py`

## Relationships

- [SlotConfigError](SlotConfigError.md) (2 shared connections)
- [serialize_slot](serialize_slot.md) (1 shared connections)

## Source Files

- `src/hal0/slots/state.py`
- `tests/slots/test_state_self_managed_providers.py`

## Audit Trail

- EXTRACTED: 25 (58%)
- INFERRED: 18 (42%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*