"""Cancellation (barge-in hook): response.cancel aborts + flushes + done."""

from __future__ import annotations

import asyncio

from hal0.config.schema import RealtimeConfig
from hal0.realtime.backends import ChatChunk, RealtimeBackends
from hal0.realtime.session import RealtimeSession


async def _fake_tts(text, *, model, voice, auth):
    return b"\x11\x22" * 480


def test_response_cancel_emits_cancelled_done_and_stops_audio() -> None:
    async def scenario() -> list[dict]:
        emitted: list[dict] = []

        async def emit(ev: dict) -> None:
            emitted.append(ev)

        gate = asyncio.Event()

        async def blocking_chat(*, messages, model, tools, auth):
            yield ChatChunk("text", text="Starting now.")
            await gate.wait()  # never released — simulates a slow in-flight turn
            yield ChatChunk("done")

        session = RealtimeSession(
            emit=emit,
            backends=RealtimeBackends(chat_plain=blocking_chat, synthesize=_fake_tts),
            cfg=RealtimeConfig(),
            model="gpt-test",
        )
        session.messages.append({"role": "user", "content": "hi"})
        await session._start_response(barge_in=False)
        # Let the leg emit response.created + speak the first sentence, then block.
        for _ in range(50):
            await asyncio.sleep(0.005)
            if any(e["type"] == "response.output_audio.delta" for e in emitted):
                break
        await session._on_cancel()
        return emitted

    emitted = asyncio.run(scenario())
    kinds = [e["type"] for e in emitted]
    assert "response.created" in kinds
    assert "response.output_audio.delta" in kinds  # first sentence spoke before cancel
    done = [e for e in emitted if e["type"] == "response.done"]
    assert done, kinds
    assert done[-1]["response"]["status"] == "cancelled"


def test_cancel_with_no_response_running_acks_cancelled() -> None:
    async def scenario() -> list[dict]:
        emitted: list[dict] = []

        async def emit(ev: dict) -> None:
            emitted.append(ev)

        session = RealtimeSession(
            emit=emit,
            backends=RealtimeBackends(synthesize=_fake_tts),
            cfg=RealtimeConfig(),
            model="gpt-test",
        )
        await session._on_cancel()
        return emitted

    emitted = asyncio.run(scenario())
    assert emitted and emitted[-1]["type"] == "response.done"
    assert emitted[-1]["response"]["status"] == "cancelled"
