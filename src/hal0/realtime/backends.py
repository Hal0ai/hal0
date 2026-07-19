"""STT / TTS / LLM-leg seams for the Realtime engine.

The engine never imports ``v1.py``, ``brain/chat.py``, ``providers``, ``slots``
or ``omni_router`` — it reaches them **only** over loopback HTTP through the
callables bundled here (fence: HP-realtime inc-1). Tests inject fakes by setting
``app.state.realtime_backends`` to a :class:`RealtimeBackends` built from stub
coroutines (the same "fake the slot at the app-state seam" pattern the brain /
board tests use), so the full event contract runs under FastAPI's ``TestClient``
websocket with **no audio hardware and no live model**.

Two LLM legs, selected by session ``model`` (spec decision 4a option 3):

* **plain leg** — ``POST /v1/chat/completions`` streamed, session-declared tools
  forwarded; ``tool_calls`` in the stream surface as ``tool_call`` chunks the
  session re-emits as ``response.function_call_arguments.done`` for **client-side**
  execution (the demo's native MCP path).
* **steward leg** — ``POST /api/brain/chat`` (``model:"hal0-brain"``), consuming
  the brain SSE frames verbatim (``token``/``approval_required``/``done``/``error``);
  tools run **server-side** inside the steward, so no function-call passthrough.
  ZERO edits to ``brain/chat.py`` — pure loopback consumption.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from hal0.realtime.audio import looks_like_wav, wav_to_pcm16

#: The sentinel model that routes a session to the server-side steward leg.
STEWARD_MODEL = "hal0-brain"


#: Loopback base URL for hal0-api's own routes (matches omni_router; §2d idiom).
def _self_base_url() -> str:
    return os.environ.get("HAL0_SELF_BASE_URL", "http://127.0.0.1:8080").rstrip("/")


#: Generous per-leg transport timeout — audio synthesis + tool rounds are slow.
_HTTP_TIMEOUT_S = 120.0


@dataclass
class ChatChunk:
    """One normalized event from an LLM leg the session consumes.

    ``kind``:
      * ``text``     — assistant text to speak (``text`` set).
      * ``tool_call``— a completed function call for client-side execution
                       (``name``/``arguments``/``call_id`` set) — plain leg only.
      * ``approval`` — a gated tool is waiting on operator approval
                       (``text`` = spoken notice) — steward leg only.
      * ``error``    — the leg failed (``text`` = message).
      * ``done``     — the turn's LLM output is complete.
    """

    kind: str
    text: str = ""
    name: str = ""
    arguments: str = ""
    call_id: str = ""


def _auth_headers(auth: str | None) -> dict[str, str]:
    return {"Authorization": auth} if auth else {}


# ── default loopback-HTTP implementations ────────────────────────────────────


async def _default_transcribe(wav: bytes, *, model: str, auth: str | None) -> str:
    url = f"{_self_base_url()}/v1/audio/transcriptions"
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
        resp = await client.post(
            url,
            files={"file": ("audio.wav", wav, "audio/wav")},
            data={"model": model},
            headers=_auth_headers(auth),
        )
        resp.raise_for_status()
        body = resp.json()
    text = body.get("text") if isinstance(body, dict) else None
    return text if isinstance(text, str) else ""


async def _default_synthesize(
    text: str, *, model: str, voice: str | None, auth: str | None
) -> bytes:
    url = f"{_self_base_url()}/v1/audio/speech"
    payload: dict[str, Any] = {"model": model, "input": text, "response_format": "pcm"}
    if voice:
        payload["voice"] = voice
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
        resp = await client.post(url, json=payload, headers=_auth_headers(auth))
        resp.raise_for_status()
        blob = resp.content
    # kokoro with response_format=pcm returns raw L16@24k; a wav fallback is
    # unwrapped so the framer always sees raw pcm (spec §4b outbound plan).
    if looks_like_wav(blob):
        pcm, _sr = wav_to_pcm16(blob)
        return pcm
    return blob


def _iter_sse_lines(raw: bytes) -> list[str]:
    """Split an SSE text chunk into ``data:`` JSON payloads (drop comments)."""
    out: list[str] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            out.append(line[len("data:") :].strip())
    return out


async def _default_chat_plain(
    *,
    messages: list[dict[str, Any]],
    model: str,
    tools: list[dict[str, Any]] | None,
    auth: str | None,
) -> AsyncIterator[ChatChunk]:
    """Stream ``/v1/chat/completions``; text deltas + accumulated tool_calls."""
    url = f"{_self_base_url()}/v1/chat/completions"
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
    if tools:
        payload["tools"] = tools
    # index -> {"id","name","arguments"} accumulator for streamed tool_calls.
    pending: dict[int, dict[str, str]] = {}
    async with (
        httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client,
        client.stream("POST", url, json=payload, headers=_auth_headers(auth)) as resp,
    ):
        if resp.status_code >= 400:
            body = await resp.aread()
            yield ChatChunk("error", text=f"chat leg {resp.status_code}: {body[:200]!r}")
            return
        async for raw in resp.aiter_bytes():
            for data in _iter_sse_lines(raw):
                if data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield ChatChunk("text", text=content)
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = pending.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]
    for idx in sorted(pending):
        slot = pending[idx]
        if slot["name"]:
            yield ChatChunk(
                "tool_call", name=slot["name"], arguments=slot["arguments"], call_id=slot["id"]
            )
    yield ChatChunk("done")


async def _default_chat_steward(
    *, payload: dict[str, Any], auth: str | None
) -> AsyncIterator[ChatChunk]:
    """Consume the brain SSE (``/api/brain/chat``); ZERO edits to brain/chat.py."""
    url = f"{_self_base_url()}/api/brain/chat"
    async with (
        httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client,
        client.stream("POST", url, json=payload, headers=_auth_headers(auth)) as resp,
    ):
        if resp.status_code >= 400:
            body = await resp.aread()
            yield ChatChunk("error", text=f"steward leg {resp.status_code}: {body[:200]!r}")
            return
        async for raw in resp.aiter_bytes():
            for data in _iter_sse_lines(raw):
                try:
                    frame = json.loads(data)
                except ValueError:
                    continue
                for chunk in _steward_frame_to_chunks(frame):
                    yield chunk
    # Brain 'done' frame already yields a done chunk; guard if the stream ends
    # without one so the session never hangs.
    yield ChatChunk("done")


def _steward_frame_to_chunks(frame: dict[str, Any]):
    """Map one brain SSE frame to zero-or-more :class:`ChatChunk` (spec §2b)."""
    kind = frame.get("type")
    if kind == "token":
        text = frame.get("text")
        if isinstance(text, str) and text:
            yield ChatChunk("text", text=text)
    elif kind == "approval_required":
        # A gated steward tool is waiting; speak a bounded notice, not dead air.
        tool = frame.get("tool") or frame.get("name") or "a tool"
        yield ChatChunk(
            "approval",
            text=f"That action ({tool}) needs your approval — check the approvals bell.",
        )
    elif kind == "error":
        yield ChatChunk("error", text=str(frame.get("message") or "steward error"))
    # 'thinking' / 'tool_call' / 'tool_result' / 'ping' are not spoken.
    # 'done' is emitted by the outer generator's trailing done chunk.


TranscribeFn = Callable[..., Awaitable[str]]
SynthesizeFn = Callable[..., Awaitable[bytes]]
ChatPlainFn = Callable[..., AsyncIterator[ChatChunk]]
ChatStewardFn = Callable[..., AsyncIterator[ChatChunk]]


@dataclass
class RealtimeBackends:
    """The four seams the session drives. Defaults hit loopback HTTP; tests
    override any subset with stub coroutines/async-generators."""

    transcribe: TranscribeFn = _default_transcribe
    synthesize: SynthesizeFn = _default_synthesize
    chat_plain: ChatPlainFn = _default_chat_plain
    chat_steward: ChatStewardFn = _default_chat_steward


def get_backends(app: Any) -> RealtimeBackends:
    """Return the app's :class:`RealtimeBackends`, building the loopback default
    on first use. Tests set ``app.state.realtime_backends`` before connecting."""
    state = getattr(app, "state", None)
    if state is None:
        return RealtimeBackends()
    existing = getattr(state, "realtime_backends", None)
    if isinstance(existing, RealtimeBackends):
        return existing
    backends = RealtimeBackends()
    state.realtime_backends = backends
    return backends


__all__ = [
    "STEWARD_MODEL",
    "ChatChunk",
    "RealtimeBackends",
    "get_backends",
]
