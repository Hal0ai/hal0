"""WS /v1/realtime event-contract tests (spec decision d, both legs)."""

from __future__ import annotations

from tests.realtime.conftest import recv_until, silence_b64, types, voiced_b64


def _connect(client, model: str = "gpt-test"):
    return client.websocket_connect(f"/v1/realtime?model={model}")


def test_handshake_emits_session_created(client) -> None:
    with _connect(client) as ws:
        created = ws.receive_json()
        assert created["type"] == "session.created"
        assert created["session"]["model"] == "gpt-test"


def test_session_update_echoes_session_updated(client) -> None:
    with _connect(client) as ws:
        ws.receive_json()  # session.created
        ws.send_json({"type": "session.update", "session": {"voice": "af_sky"}})
        updated = ws.receive_json()
        assert updated["type"] == "session.updated"
        assert updated["session"]["voice"] == "af_sky"


def test_plain_leg_none_mode_full_turn(client) -> None:
    """append+commit -> transcription.completed -> response.created -> audio -> done."""
    with _connect(client) as ws:
        ws.receive_json()  # session.created
        ws.send_json({"type": "session.update", "session": {"turn_detection": None}})
        assert ws.receive_json()["type"] == "session.updated"

        ws.send_json({"type": "input_audio_buffer.append", "audio": voiced_b64(100)})
        ws.send_json({"type": "input_audio_buffer.commit"})

        got = types(recv_until(ws, "response.done"))
        assert got[0] == "conversation.item.input_audio_transcription.completed"
        assert "response.created" in got
        assert "response.output_audio_transcript.delta" in got
        assert "response.output_audio.delta" in got
        assert got[-1] == "response.done"


def test_server_vad_auto_commit_turn(client) -> None:
    """Default server_vad: voiced then silence -> speech_started/stopped -> turn."""
    with _connect(client) as ws:
        ws.receive_json()  # session.created (turn_detection defaults server_vad)
        ws.send_json({"type": "input_audio_buffer.append", "audio": voiced_b64(300)})
        ws.send_json({"type": "input_audio_buffer.append", "audio": silence_b64(600)})

        got = types(recv_until(ws, "response.done"))
        assert "input_audio_buffer.speech_started" in got
        assert "input_audio_buffer.speech_stopped" in got
        assert "conversation.item.input_audio_transcription.completed" in got
        assert "response.output_audio.delta" in got
        assert got[-1] == "response.done"


def test_reject_list_returns_typed_error(client) -> None:
    with _connect(client) as ws:
        ws.receive_json()
        ws.send_json({"type": "input_audio_buffer.clear"})
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["error"]["code"] == "unsupported_event"
        assert err["error"]["event"] == "input_audio_buffer.clear"


def test_unknown_event_returns_error(client) -> None:
    with _connect(client) as ws:
        ws.receive_json()
        ws.send_json({"type": "totally.made.up"})
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["error"]["code"] == "unknown_event"


def test_bad_json_frame_returns_error_not_crash(client) -> None:
    with _connect(client) as ws:
        ws.receive_json()
        ws.send_text("{not json")
        err = ws.receive_json()
        assert err["type"] == "error"
        # socket still alive: a valid frame still works afterwards
        ws.send_json({"type": "session.update", "session": {}})
        assert ws.receive_json()["type"] == "session.updated"


def test_commit_empty_buffer_errors(client) -> None:
    with _connect(client) as ws:
        ws.receive_json()
        ws.send_json({"type": "session.update", "session": {"turn_detection": None}})
        ws.receive_json()
        ws.send_json({"type": "input_audio_buffer.commit"})
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["error"]["code"] == "input_audio_buffer_commit_empty"
