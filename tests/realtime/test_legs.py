"""Both LLM legs: client-side tool passthrough (plain) + steward approval."""

from __future__ import annotations

import contextlib

from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.realtime.backends import ChatChunk
from tests.realtime.conftest import default_backends, recv_until, types, voiced_b64


@contextlib.contextmanager
def _client(**backend_overrides):
    app = create_app()
    app.state.realtime_backends = default_backends(**backend_overrides)
    with TestClient(app) as c:
        yield c


def _drive_turn(ws) -> None:
    ws.receive_json()  # session.created
    ws.send_json({"type": "session.update", "session": {"turn_detection": None}})
    ws.receive_json()  # session.updated
    ws.send_json({"type": "input_audio_buffer.append", "audio": voiced_b64(100)})
    ws.send_json({"type": "input_audio_buffer.commit"})


def test_plain_leg_emits_function_call_arguments_done() -> None:
    """A tool_call in the plain leg surfaces for client-side execution."""

    async def chat_with_tool(*, messages, model, tools, auth):
        yield ChatChunk(
            "tool_call", name="load_slot", arguments='{"slot":"agent"}', call_id="call_42"
        )
        yield ChatChunk("done")

    with (
        _client(chat_plain=chat_with_tool) as client,
        client.websocket_connect("/v1/realtime?model=gpt-test") as ws,
    ):
        _drive_turn(ws)
        evs = recv_until(ws, "response.done")
        fc = next(e for e in evs if e["type"] == "response.function_call_arguments.done")
        assert fc["name"] == "load_slot"
        assert fc["arguments"] == '{"slot":"agent"}'
        assert fc["call_id"] == "call_42"


def test_steward_leg_speaks_bounded_approval_notice() -> None:
    """model=hal0-brain: an approval_required frame is spoken, not dead air."""

    async def steward_with_approval(*, payload, auth):
        yield ChatChunk("text", text="Let me do that.")
        yield ChatChunk(
            "approval", text="That action (load_slot) needs your approval — check the bell."
        )
        yield ChatChunk("done")

    with (
        _client(chat_steward=steward_with_approval) as client,
        client.websocket_connect("/v1/realtime?model=hal0-brain") as ws,
    ):
        _drive_turn(ws)
        evs = recv_until(ws, "response.done")
        transcripts = [
            e["delta"] for e in evs if e["type"] == "response.output_audio_transcript.delta"
        ]
        assert any("approval" in t.lower() for t in transcripts)
        assert "response.output_audio.delta" in types(evs)


def test_steward_leg_error_frame_surfaces_error() -> None:
    async def steward_error(*, payload, auth):
        yield ChatChunk("error", text="brain exploded")

    with (
        _client(chat_steward=steward_error) as c,
        c.websocket_connect("/v1/realtime?model=hal0-brain") as ws,
    ):
        _drive_turn(ws)
        evs = recv_until(ws, "response.done")
        assert any(e["type"] == "error" and "brain exploded" in e["error"]["message"] for e in evs)
