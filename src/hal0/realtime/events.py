"""Realtime event vocabulary + typed error/event builders (spec §4b).

The wire is newline-free JSON text frames, each with a ``type`` field. This
module is the single source of truth for which client->server events the engine
**accepts**, which it **rejects** (typed ``error``, never a crash), and the
server->client events it **emits**. Payload shaping lives in the session state
machine; this module keeps the vocabulary + small pure builders.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

# ── client -> server: accepted (spec §4b) ────────────────────────────────────
ACCEPTED_CLIENT_EVENTS: frozenset[str] = frozenset(
    {
        "session.update",
        "input_audio_buffer.append",
        "input_audio_buffer.commit",
        "response.create",
        "response.cancel",
        "conversation.item.create",
    }
)

# ── client -> server: known-but-unimplemented -> typed error (spec §4b) ───────
REJECTED_CLIENT_EVENTS: frozenset[str] = frozenset(
    {
        "input_audio_buffer.clear",
        "conversation.item.delete",
        "conversation.item.truncate",
    }
)

# ── server -> client: emitted (spec §4b) ─────────────────────────────────────
EMITTED_SERVER_EVENTS: frozenset[str] = frozenset(
    {
        "session.created",
        "session.updated",
        "input_audio_buffer.speech_started",  # server_vad (user decision 1)
        "input_audio_buffer.speech_stopped",  # server_vad (user decision 1)
        "conversation.item.input_audio_transcription.completed",
        "response.created",
        "response.output_audio_transcript.delta",
        "response.output_audio.delta",
        "response.function_call_arguments.done",
        "response.done",
        "error",
    }
)


def new_id(prefix: str) -> str:
    """Short, stable-prefixed id for events/items/responses (``evt_``, ``resp_``…)."""
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def event(kind: str, **fields: Any) -> dict[str, Any]:
    """Build a server->client event dict with a fresh ``event_id`` + ``type``."""
    return {"event_id": new_id("evt"), "type": kind, **fields}


def error_event(
    message: str,
    *,
    code: str = "invalid_request_error",
    event_type: str | None = None,
    param: str | None = None,
) -> dict[str, Any]:
    """Build a Realtime ``error`` event (OpenAI shape: ``{type:error, error:{…}}``).

    ``event_type`` echoes the offending client event's ``type`` so a client can
    correlate the failure. Never raises — the engine turns every bad frame into
    one of these instead of tearing down the socket.
    """
    err: dict[str, Any] = {"type": "error", "code": code, "message": message}
    if param is not None:
        err["param"] = param
    if event_type is not None:
        err["event"] = event_type
    return {"event_id": new_id("evt"), "type": "error", "error": err}


def unix_ms() -> int:
    """Millisecond wall clock (audio buffer timestamps)."""
    return int(time.time() * 1000)


__all__ = [
    "ACCEPTED_CLIENT_EVENTS",
    "EMITTED_SERVER_EVENTS",
    "REJECTED_CLIENT_EVENTS",
    "error_event",
    "event",
    "new_id",
    "unix_ms",
]
