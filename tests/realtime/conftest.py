"""Shared fixtures for the Realtime event-contract tests.

Fakes STT/TTS/chat at the ``app.state.realtime_backends`` seam (the same
"inject at app-state" pattern the brain/board tests use), so the full WS event
contract runs under FastAPI's ``TestClient`` with no audio hardware and no live
model.
"""

from __future__ import annotations

import struct

import pytest
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.realtime import audio
from hal0.realtime.backends import ChatChunk, RealtimeBackends

_SR = 24000


def voiced_b64(ms: int, amp: int = 6000) -> str:
    n = int(_SR * ms / 1000)
    pcm = b"".join(struct.pack("<h", amp if i % 2 else -amp) for i in range(n))
    return audio.b64_encode(pcm)


def silence_b64(ms: int) -> str:
    n = int(_SR * ms / 1000)
    return audio.b64_encode(b"\x00\x00" * n)


async def fake_stt(wav: bytes, *, model: str, auth: str | None) -> str:
    assert audio.looks_like_wav(wav)  # commit must wrap pcm in a container
    return "hello box"


async def fake_tts(text: str, *, model: str, voice: str | None, auth: str | None) -> bytes:
    # 960 bytes == exactly one 20ms frame @ 24k, so N sentences -> N audio deltas.
    return b"\x11\x22" * 480


async def fake_chat_plain(*, messages, model, tools, auth):
    yield ChatChunk("text", text="Hi there.")
    yield ChatChunk("done")


async def fake_chat_steward(*, payload, auth):
    yield ChatChunk("text", text="Working on it.")
    yield ChatChunk("done")


def default_backends(**overrides) -> RealtimeBackends:
    return RealtimeBackends(
        transcribe=overrides.get("transcribe", fake_stt),
        synthesize=overrides.get("synthesize", fake_tts),
        chat_plain=overrides.get("chat_plain", fake_chat_plain),
        chat_steward=overrides.get("chat_steward", fake_chat_steward),
    )


@pytest.fixture
def app():
    application = create_app()
    application.state.realtime_backends = default_backends()
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def recv_until(ws, type_: str, *, cap: int = 60) -> list[dict]:
    """Read events until (and including) the first of ``type_``; return them."""
    seen: list[dict] = []
    for _ in range(cap):
        ev = ws.receive_json()
        seen.append(ev)
        if ev.get("type") == type_:
            return seen
    raise AssertionError(f"never saw {type_!r}; got {[e.get('type') for e in seen]}")


def types(events: list[dict]) -> list[str]:
    return [e.get("type") for e in events]
