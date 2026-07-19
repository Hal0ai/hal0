# test_tts_request_defaults.py

> 22 nodes · cohesion 0.15

## Key Concepts

- **test_tts_request_defaults.py** (16 connections) — `tests/api/test_tts_request_defaults.py`
- **_seed_tts_defaults()** (8 connections) — `src/hal0/api/routes/v1.py`
- **_tts_slot_config()** (8 connections) — `src/hal0/api/routes/v1.py`
- **_request_with_cfgs()** (5 connections) — `tests/api/test_tts_request_defaults.py`
- **isolated_app()** (3 connections) — `tests/api/test_tts_request_defaults.py`
- **isolated_client()** (3 connections) — `tests/api/test_tts_request_defaults.py`
- **FastAPI** (3 connections)
- **TestClient** (3 connections)
- **test_tts_slot_empty_when_no_tts_slots()** (3 connections) — `tests/api/test_tts_request_defaults.py`
- **test_tts_slot_falls_back_to_default_then_name()** (3 connections) — `tests/api/test_tts_request_defaults.py`
- **test_tts_slot_prefers_model_match()** (3 connections) — `tests/api/test_tts_request_defaults.py`
- **test_voices_offline_slot_fails_soft()** (3 connections) — `tests/api/test_tts_request_defaults.py`
- **_seed_tts_slot()** (2 connections) — `tests/api/test_tts_request_defaults.py`
- **test_seed_fills_omitted_params()** (2 connections) — `tests/api/test_tts_request_defaults.py`
- **test_seed_ignores_bool_speed()** (2 connections) — `tests/api/test_tts_request_defaults.py`
- **test_seed_never_overrides_explicit_params()** (2 connections) — `tests/api/test_tts_request_defaults.py`
- **test_seed_skips_unset_defaults()** (2 connections) — `tests/api/test_tts_request_defaults.py`
- **test_voices_unknown_slot_404()** (2 connections) — `tests/api/test_tts_request_defaults.py`
- **Config of the tts slot that will serve ``model``, or ``{}``.      Selection mirr** (1 connections) — `src/hal0/api/routes/v1.py`
- **Fill omitted /v1/audio/speech params from the slot's persisted defaults.** (1 connections) — `src/hal0/api/routes/v1.py`
- **SimpleNamespace** (1 connections)
- **TTS request-default injection + voices proxy (Settings → Voice, Phase 3).  ``/v1** (1 connections) — `tests/api/test_tts_request_defaults.py`

## Relationships

- [v1.py](v1.py.md) (7 shared connections)
- [types.py](types.py.md) (1 shared connections)
- [create_app](create_app.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/v1.py`
- `tests/api/test_tts_request_defaults.py`

## Audit Trail

- EXTRACTED: 62 (81%)
- INFERRED: 15 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*