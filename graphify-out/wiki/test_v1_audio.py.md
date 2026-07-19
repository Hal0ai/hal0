# test_v1_audio.py

> 28 nodes

## Key Concepts

- **test_v1_audio.py** (13 connections) — `tests/api/test_v1_audio.py`
- **TestClient** (12 connections)
- **_install_mock_transport()** (10 connections) — `tests/api/test_v1_audio.py`
- **_seed_stt_upstream()** (8 connections) — `tests/api/test_v1_audio.py`
- **_pin_slot_ready()** (6 connections) — `tests/api/test_v1_audio.py`
- **_seed_tts_upstream()** (6 connections) — `tests/api/test_v1_audio.py`
- **test_scrubber_does_not_touch_non_audio_routes()** (6 connections) — `tests/api/test_v1_audio.py`
- **test_v1_audio_transcriptions_redacts_ffmpeg_argv()** (5 connections) — `tests/api/test_v1_audio.py`
- **test_v1_audio_transcriptions_clean_upstream_error_passes_through()** (5 connections) — `tests/api/test_v1_audio.py`
- **test_v1_audio_transcriptions_happy_path()** (5 connections) — `tests/api/test_v1_audio.py`
- **test_v1_audio_speech_happy_path()** (5 connections) — `tests/api/test_v1_audio.py`
- **test_v1_audio_speech_kokoro_v1_reaches_tts_upstream()** (5 connections) — `tests/api/test_v1_audio.py`
- **test_v1_audio_speech_missing_model_returns_400_envelope()** (3 connections) — `tests/api/test_v1_audio.py`
- **test_v1_audio_speech_empty_model_returns_400_envelope()** (3 connections) — `tests/api/test_v1_audio.py`
- **MockTransport** (1 connections)
- **Wiring tests for ``/v1/audio/*`` envelope semantics.  Covers two harness finding** (1 connections) — `tests/api/test_v1_audio.py`
- **Force ``slot_name`` to READY in the SlotManager state map.      The dispatcher's** (1 connections) — `tests/api/test_v1_audio.py`
- **Register a fake STT slot the dispatcher's legacy fallback will land on.      The** (1 connections) — `tests/api/test_v1_audio.py`
- **Register the TTS upstream EXACTLY as production does: a container remote.      P** (1 connections) — `tests/api/test_v1_audio.py`
- **Swap the dispatcher's httpx client for one backed by ``handler``.      The dispa** (1 connections) — `tests/api/test_v1_audio.py`
- **A non-audio multipart body must surface as 415 audio.unsupported_format.      Si** (1 connections) — `tests/api/test_v1_audio.py`
- **An upstream 5xx body that doesn't mention ffmpeg passes through verbatim.      R** (1 connections) — `tests/api/test_v1_audio.py`
- **POST /v1/audio/speech without ``model`` → 400 request.missing_model.      The di** (1 connections) — `tests/api/test_v1_audio.py`
- **Whitespace-only ``model`` is treated as missing.** (1 connections) — `tests/api/test_v1_audio.py`
- **A well-formed multipart STT request reaches the upstream and returns its body.** (1 connections) — `tests/api/test_v1_audio.py`
- *... and 3 more nodes in this community*

## Relationships

- [Upstream](Upstream.md) (3 shared connections)
- [SlotConfigError](SlotConfigError.md) (1 shared connections)

## Source Files

- `tests/api/test_v1_audio.py`

## Audit Trail

- EXTRACTED: 102 (96%)
- INFERRED: 4 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*