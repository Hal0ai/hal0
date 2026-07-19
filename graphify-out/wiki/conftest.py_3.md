# conftest.py

> 14 nodes · cohesion 0.24

## Key Concepts

- **conftest.py** (15 connections) — `tests/realtime/conftest.py`
- **default_backends()** (11 connections) — `tests/realtime/conftest.py`
- **test_auth.py** (6 connections) — `tests/realtime/test_auth.py`
- **_app()** (5 connections) — `tests/realtime/test_auth.py`
- **app()** (3 connections) — `tests/realtime/conftest.py`
- **fake_chat_plain()** (3 connections) — `tests/realtime/conftest.py`
- **fake_chat_steward()** (3 connections) — `tests/realtime/conftest.py`
- **fake_stt()** (2 connections) — `tests/realtime/conftest.py`
- **fake_tts()** (2 connections) — `tests/realtime/conftest.py`
- **test_upgrade_allowed_with_api_key()** (2 connections) — `tests/realtime/test_auth.py`
- **test_upgrade_denied_without_credentials()** (2 connections) — `tests/realtime/test_auth.py`
- **client()** (1 connections) — `tests/realtime/conftest.py`
- **Shared fixtures for the Realtime event-contract tests.  Fakes STT/TTS/chat at th** (1 connections) — `tests/realtime/conftest.py`
- **Auth on the WS upgrade: CLIENT tier, KB-1 enforcement (spec §4b auth).** (1 connections) — `tests/realtime/test_auth.py`

## Relationships

- [test_event_contract.py](test_event_contract.py.md) (8 shared connections)
- [create_app](create_app.md) (2 shared connections)
- [backends.py](backends.py.md) (2 shared connections)
- [EnergyVAD](EnergyVAD.md) (1 shared connections)

## Source Files

- `tests/realtime/conftest.py`
- `tests/realtime/test_auth.py`

## Audit Trail

- EXTRACTED: 45 (79%)
- INFERRED: 12 (21%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*