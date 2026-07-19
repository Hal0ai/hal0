# test_event_contract.py

> 27 nodes · cohesion 0.15

## Key Concepts

- **test_event_contract.py** (15 connections) — `tests/realtime/test_event_contract.py`
- **test_legs.py** (11 connections) — `tests/realtime/test_legs.py`
- **recv_until()** (9 connections) — `tests/realtime/conftest.py`
- **_connect()** (9 connections) — `tests/realtime/test_event_contract.py`
- **test_server_vad_auto_commit_turn()** (7 connections) — `tests/realtime/test_event_contract.py`
- **types()** (6 connections) — `tests/realtime/conftest.py`
- **voiced_b64()** (6 connections) — `tests/realtime/conftest.py`
- **test_plain_leg_none_mode_full_turn()** (6 connections) — `tests/realtime/test_event_contract.py`
- **_client()** (6 connections) — `tests/realtime/test_legs.py`
- **test_steward_leg_speaks_bounded_approval_notice()** (6 connections) — `tests/realtime/test_legs.py`
- **_drive_turn()** (5 connections) — `tests/realtime/test_legs.py`
- **test_plain_leg_emits_function_call_arguments_done()** (5 connections) — `tests/realtime/test_legs.py`
- **test_steward_leg_error_frame_surfaces_error()** (4 connections) — `tests/realtime/test_legs.py`
- **silence_b64()** (3 connections) — `tests/realtime/conftest.py`
- **test_bad_json_frame_returns_error_not_crash()** (2 connections) — `tests/realtime/test_event_contract.py`
- **test_commit_empty_buffer_errors()** (2 connections) — `tests/realtime/test_event_contract.py`
- **test_handshake_emits_session_created()** (2 connections) — `tests/realtime/test_event_contract.py`
- **test_reject_list_returns_typed_error()** (2 connections) — `tests/realtime/test_event_contract.py`
- **test_session_update_echoes_session_updated()** (2 connections) — `tests/realtime/test_event_contract.py`
- **test_unknown_event_returns_error()** (2 connections) — `tests/realtime/test_event_contract.py`
- **Read events until (and including) the first of ``type_``; return them.** (1 connections) — `tests/realtime/conftest.py`
- **WS /v1/realtime event-contract tests (spec decision d, both legs).** (1 connections) — `tests/realtime/test_event_contract.py`
- **append+commit -> transcription.completed -> response.created -> audio -> done.** (1 connections) — `tests/realtime/test_event_contract.py`
- **Default server_vad: voiced then silence -> speech_started/stopped -> turn.** (1 connections) — `tests/realtime/test_event_contract.py`
- **Both LLM legs: client-side tool passthrough (plain) + steward approval.** (1 connections) — `tests/realtime/test_legs.py`
- *... and 2 more nodes in this community*

## Relationships

- [conftest.py](conftest.py.md) (8 shared connections)
- [create_app](create_app.md) (1 shared connections)

## Source Files

- `tests/realtime/conftest.py`
- `tests/realtime/test_event_contract.py`
- `tests/realtime/test_legs.py`

## Audit Trail

- EXTRACTED: 116 (99%)
- INFERRED: 1 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*