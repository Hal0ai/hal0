"""Stall guard for a never-terminating upstream stream (#1893).

A pathological upstream — the shape the Vulkan garbage-token defect produces
(``finish_reason`` forever ``null``, tokens forever) — used to be relayed
verbatim and forever: ``_forward_streaming`` piped ``aiter_raw()`` to
exhaustion, and httpx's per-read timeout is reset by every chunk, so a chatty
stream never trips it. Every downstream client (Hermes ``--cli`` included)
therefore sat on an open socket with no output and no diagnostic for as long
as the operator waited.

These tests pin the guard: a bounded total duration, a bounded inter-chunk
gap, and — on an OpenAI-compatible SSE stream — a terminal chunk that names
the cutoff followed by ``data: [DONE]`` so the client's stream loop ends
cleanly instead of hanging.

The real-socket tests exist because ``httpx.MockTransport`` ignores client
timeouts entirely: only a live connection can prove that ``httpx.ReadTimeout``
(NOT a subclass of builtin ``TimeoutError``) never escapes the guard.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from hal0.dispatcher.router import Dispatcher, UpstreamCall


class _EndlessStream(httpx.AsyncByteStream):
    """An upstream body that never ends.

    ``gap`` is the delay between chunks: a small gap models the chatty
    garbage-token stream (total-duration guard), a large one models an
    upstream that opened the stream and then went silent (idle guard).
    """

    def __init__(self, chunk: bytes, *, gap: float, burst: int | None = None) -> None:
        self._chunk = chunk
        self._gap = gap
        self._burst = burst
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:  # type: ignore[override]
        emitted = 0
        while True:
            if self._burst is not None and emitted >= self._burst:
                await asyncio.sleep(3600)  # opened, then silent forever
                continue
            await asyncio.sleep(self._gap)
            emitted += 1
            yield self._chunk

    async def aclose(self) -> None:
        self.closed = True


_GARBAGE_CHUNK = b'data: {"id":"1","choices":[{"delta":{"content":"!"},"finish_reason":null}]}\n\n'


def _call(
    *,
    streaming: bool = True,
    target_url: str = "http://upstream.test/chat/completions",
) -> UpstreamCall:
    return UpstreamCall(
        upstream_name="test-upstream",
        target_url=target_url,
        headers={"content-type": "application/json"},
        body=b"",
        streaming=streaming,
        method="POST",
    )


def _dispatcher(
    stream: httpx.AsyncByteStream,
    *,
    content_type: str = "text/event-stream",
    total: float = 0.25,
    idle: float = 0.25,
) -> Dispatcher:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream, headers={"content-type": content_type})

    return Dispatcher(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        stream_total_timeout_s=total,
        stream_idle_timeout_s=idle,
    )


async def _drain(resp: object, *, limit: float = 10.0) -> bytes:
    async def _read() -> bytes:
        collected = b""
        async for chunk in resp.body_iterator:  # type: ignore[attr-defined]
            collected += chunk if isinstance(chunk, bytes) else chunk.encode()
        return collected

    # The whole point is that this terminates; the outer bound only keeps a
    # regression from wedging the suite.
    return await asyncio.wait_for(_read(), timeout=limit)


def _stall_payload(body: bytes) -> dict:
    frames = [ln for ln in body.decode().splitlines() if ln.startswith("data: ")]
    assert frames[-1] == "data: [DONE]", f"stream did not end with [DONE]: {frames[-1]!r}"
    return json.loads(frames[-2][len("data: ") :])


@pytest.mark.asyncio
async def test_chatty_never_terminating_stream_is_cut_off_with_a_named_reason() -> None:
    """A stream that emits forever is cut at the total-duration bound."""
    upstream = _EndlessStream(_GARBAGE_CHUNK, gap=0.001)
    dispatcher = _dispatcher(upstream, total=0.2, idle=5.0)
    try:
        resp = await dispatcher.forward(_call())
        body = await _drain(resp)
    finally:
        await dispatcher.aclose()

    assert body.count(b"data: [DONE]") == 1
    payload = _stall_payload(body)
    stall = payload["x_hal0_stall"]
    assert stall["reason"] == "total"
    assert stall["upstream"] == "test-upstream"
    assert stall["elapsed_s"] >= 0.2
    choice = payload["choices"][0]
    # Operator-visible: the cutoff is rendered as text AND terminates the
    # client's stream loop via a non-null finish_reason.
    assert choice["finish_reason"] == "length"
    assert "hal0" in choice["delta"]["content"]
    assert "stall" in choice["delta"]["content"].lower()
    # The partial tokens the upstream did produce are still delivered.
    assert b'"content":"!"' in body


@pytest.mark.asyncio
async def test_silent_stream_is_cut_off_at_the_idle_bound() -> None:
    """An opened-then-silent stream trips the inter-chunk gap guard."""
    upstream = _EndlessStream(_GARBAGE_CHUNK, gap=30.0)
    dispatcher = _dispatcher(upstream, total=60.0, idle=0.2)
    try:
        resp = await dispatcher.forward(_call())
        body = await _drain(resp)
    finally:
        await dispatcher.aclose()

    assert _stall_payload(body)["x_hal0_stall"]["reason"] == "idle"


@pytest.mark.asyncio
async def test_non_sse_stream_is_bounded_without_injecting_sse_frames() -> None:
    """Binary/JSON streaming bodies are cut off, never rewritten."""
    upstream = _EndlessStream(b"\x00\x01\x02", gap=0.001)
    dispatcher = _dispatcher(upstream, content_type="application/octet-stream", total=0.2)
    try:
        resp = await dispatcher.forward(_call())
        body = await _drain(resp)
    finally:
        await dispatcher.aclose()

    assert b"[DONE]" not in body
    assert b"x_hal0_stall" not in body


@pytest.mark.asyncio
async def test_well_behaved_stream_is_untouched() -> None:
    """A stream that terminates normally is relayed byte-for-byte."""
    chunks = [
        b'data: {"id":"1","choices":[{"delta":{"content":"hi"}}]}\n\n',
        b'data: {"id":"1","choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    dispatcher = _dispatcher(httpx.ByteStream(b"".join(chunks)), total=0.2, idle=0.2)
    try:
        resp = await dispatcher.forward(_call())
        body = await _drain(resp)
    finally:
        await dispatcher.aclose()

    assert body == b"".join(chunks)


@pytest.mark.asyncio
async def test_guard_can_be_disabled_with_zero() -> None:
    """``0`` disables a bound — the escape hatch for a deliberate long stream."""
    upstream = _EndlessStream(_GARBAGE_CHUNK, gap=0.02, burst=20)
    dispatcher = _dispatcher(upstream, total=0.0, idle=0.15)
    try:
        resp = await dispatcher.forward(_call())
        body = await _drain(resp)
    finally:
        await dispatcher.aclose()

    # Total is disabled (0.0), so the stream survives well past 0.0s and all
    # 20 chunks are relayed; only the idle bound ends it.
    assert body.count(_GARBAGE_CHUNK) == 20
    assert _stall_payload(body)["x_hal0_stall"]["reason"] == "idle"


# ── real-socket tests: httpx.ReadTimeout must never escape the guard ─────────
#
# MockTransport ignores client timeouts entirely, so only a live connection
# can exercise the httpx read-timeout interaction (review of PR #1918,
# blocking 1).


@contextlib.asynccontextmanager
async def _silent_sse_upstream(prelude: bytes) -> AsyncIterator[int]:
    """A real TCP server: sends ``prelude`` as an SSE body, then goes silent."""

    handlers: list[asyncio.Task[None]] = []

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        handlers.append(asyncio.current_task())  # type: ignore[arg-type]
        with contextlib.suppress(Exception):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"content-type: text/event-stream\r\n"
                b"connection: close\r\n"
                b"\r\n" + prelude
            )
            await writer.drain()
            await asyncio.sleep(3600)  # opened, then silent forever

    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.close()
        # Python ≥3.12 wait_closed() waits for handler coroutines; ours sleeps
        # forever by design, so cancel it before waiting.
        for task in handlers:
            task.cancel()
        with contextlib.suppress(Exception):
            await server.wait_closed()


@pytest.mark.asyncio
async def test_idle_bound_above_read_timeout_still_produces_the_diagnostic() -> None:
    """``stream_idle_timeout_s > direct_read_timeout_s`` must not hand the
    cutoff to httpx: ``httpx.ReadTimeout`` is not a ``TimeoutError`` subclass
    and used to escape after headers were sent — a torn stream with no frame."""
    async with _silent_sse_upstream(_GARBAGE_CHUNK) as port:
        dispatcher = Dispatcher(
            direct_read_timeout_s=0.2,
            stream_total_timeout_s=60.0,
            stream_idle_timeout_s=0.8,
        )
        try:
            resp = await dispatcher.forward(
                _call(target_url=f"http://127.0.0.1:{port}/v1/chat/completions")
            )
            body = await _drain(resp)
        finally:
            await dispatcher.aclose()

    assert _GARBAGE_CHUNK in body  # the partial output was relayed
    assert _stall_payload(body)["x_hal0_stall"]["reason"] == "idle"


@pytest.mark.asyncio
async def test_idle_zero_disables_the_idle_cutoff_and_total_still_bounds() -> None:
    """``stream_idle_timeout_s = 0`` genuinely disables the idle bound — it
    must not silently fall through to httpx's read timeout.  The total bound
    then ends the stream with the diagnostic."""
    async with _silent_sse_upstream(_GARBAGE_CHUNK) as port:
        dispatcher = Dispatcher(
            direct_read_timeout_s=0.2,
            stream_total_timeout_s=0.7,
            stream_idle_timeout_s=0.0,
        )
        try:
            resp = await dispatcher.forward(
                _call(target_url=f"http://127.0.0.1:{port}/v1/chat/completions")
            )
            body = await _drain(resp)
        finally:
            await dispatcher.aclose()

    payload = _stall_payload(body)
    assert payload["x_hal0_stall"]["reason"] == "total"
    # The stream outlived the 0.2s client read timeout — proof httpx's
    # ReadTimeout neither cut it early nor escaped.
    assert payload["x_hal0_stall"]["elapsed_s"] >= 0.7


class _ReadTimeoutStream(httpx.AsyncByteStream):
    """An upstream body that raises a transport-level read timeout."""

    async def __aiter__(self) -> AsyncIterator[bytes]:  # type: ignore[override]
        yield _GARBAGE_CHUNK
        raise httpx.ReadTimeout("simulated transport read timeout")

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_transport_read_timeout_becomes_a_read_stall_frame() -> None:
    """Even with the guard fully disabled, a transport read timeout ends the
    stream with a diagnostic frame (reason ``read``), never a bare escape."""
    dispatcher = _dispatcher(_ReadTimeoutStream(), total=0.0, idle=0.0)
    try:
        resp = await dispatcher.forward(_call())
        body = await _drain(resp)
    finally:
        await dispatcher.aclose()

    payload = _stall_payload(body)
    assert payload["x_hal0_stall"]["reason"] == "read"
    assert body.count(b"data: [DONE]") == 1


# ── SSE event-boundary and endpoint-shape tests ──────────────────────────────


class _SplitEventThenSilentStream(httpx.AsyncByteStream):
    """Emits one clean event, then *half* of the next, then goes silent —
    ``aiter_raw()`` yields transport chunks, not complete SSE events."""

    async def __aiter__(self) -> AsyncIterator[bytes]:  # type: ignore[override]
        yield b'data: {"id":"1","choices":[{"delta":{"content":"word"}}]}\n\n'
        yield b'data: {"id":"2","cho'
        await asyncio.sleep(3600)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_stall_frame_after_a_mid_event_stall_parses_standalone() -> None:
    """A stall midway through a ``data:`` line must not swallow the diagnostic:
    the injected frames start on a fresh SSE event boundary (PR #1918
    review, blocking 2)."""
    dispatcher = _dispatcher(_SplitEventThenSilentStream(), total=60.0, idle=0.2)
    try:
        resp = await dispatcher.forward(_call())
        body = await _drain(resp)
    finally:
        await dispatcher.aclose()

    events = body.split(b"\n\n")
    # The partial upstream line was terminated as its own (discardable) event…
    assert b'data: {"id":"2","cho' in events
    # …and the stall frame is a standalone, parseable event of its own.
    stall_events = [e for e in events if e.startswith(b'data: {"id":"hal0-stall-guard"')]
    assert len(stall_events) == 1, f"stall frame not standalone: {events!r}"
    payload = json.loads(stall_events[0][len(b"data: ") :])
    assert payload["x_hal0_stall"]["reason"] == "idle"
    assert b"data: [DONE]" in body


@pytest.mark.asyncio
async def test_legacy_completions_stall_frame_is_text_shaped() -> None:
    """``/v1/completions`` streams get a ``text_completion`` chunk with
    ``choices[].text`` — not a chat-shaped ``delta`` (PR #1918 review,
    non-blocking 4)."""
    upstream = _EndlessStream(
        b'data: {"id":"1","choices":[{"text":"!","finish_reason":null}]}\n\n', gap=30.0
    )
    dispatcher = _dispatcher(upstream, total=60.0, idle=0.2)
    try:
        resp = await dispatcher.forward(_call(target_url="http://upstream.test/v1/completions"))
        body = await _drain(resp)
    finally:
        await dispatcher.aclose()

    payload = _stall_payload(body)
    assert payload["object"] == "text_completion"
    choice = payload["choices"][0]
    assert "delta" not in choice
    assert "stall" in choice["text"].lower()
    assert choice["finish_reason"] == "length"
    assert payload["x_hal0_stall"]["reason"] == "idle"
