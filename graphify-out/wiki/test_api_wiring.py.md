# test_api_wiring.py

> 11 nodes

## Key Concepts

- **test_api_wiring.py** (5 connections) — `tests/omni_router/test_api_wiring.py`
- **TestClient** (4 connections)
- **test_app_starts_with_omni_router_attached()** (3 connections) — `tests/omni_router/test_api_wiring.py`
- **test_chat_completions_with_omni_true_falls_back_when_no_slot_matches()** (3 connections) — `tests/omni_router/test_api_wiring.py`
- **test_chat_completions_omni_false_unchanged()** (3 connections) — `tests/omni_router/test_api_wiring.py`
- **test_chat_completions_without_omni_field_unchanged()** (3 connections) — `tests/omni_router/test_api_wiring.py`
- **Smoke tests for the OmniRouter wiring in /v1/chat/completions.  PR-16 attaches a** (1 connections) — `tests/omni_router/test_api_wiring.py`
- **Lifespan attaches an OmniRouter to app.state.      The TestClient fixture runs t** (1 connections) — `tests/omni_router/test_api_wiring.py`
- **Body field ``omni: true`` against an empty slot tree → standard     no-route env** (1 connections) — `tests/omni_router/test_api_wiring.py`
- **Body field ``omni: false`` is treated as no opt-in — passthrough.** (1 connections) — `tests/omni_router/test_api_wiring.py`
- **Bodies without ``omni`` field skip the OmniRouter loop.      With no slots confi** (1 connections) — `tests/omni_router/test_api_wiring.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/omni_router/test_api_wiring.py`

## Audit Trail

- EXTRACTED: 26 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*