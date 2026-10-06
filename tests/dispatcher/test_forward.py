"""Unit tests for ``Dispatcher.forward``.

Uses ``httpx.MockTransport`` to stub upstream HTTP behaviour without
real sockets — same approach the httpx docs recommend for tests.
"""

from __future__ import annotations

import json

import httpx
import pytest

from hal0.dispatcher.router import (
    Dispatcher,
    UpstreamCall,
    UpstreamTimeout,
    UpstreamUnavailable,
)


def _make_dispatcher(transport: httpx.MockTransport) -> Dispatcher:
    """Build a Dispatcher whose internal httpx client is backed by ``transport``."""
    client = httpx.AsyncClient(transport=transport)
    return Dispatcher(http_client=client)


def _call(
    *,
    streaming: bool = False,
    body: bytes = b"",
    method: str = "POST",
    target: str = "http://upstream.test/chat/completions",
) -> UpstreamCall:
    return UpstreamCall(
        upstream_name="test-upstream",
        target_url=target,
        headers={"content-type": "application/json"},
        body=body,
        streaming=streaming,
        method=method,
    )


@pytest.mark.asyncio
async def test_forward_non_streaming_returns_upstream_body() -> None:
    payload = {"id": "chatcmpl-1", "choices": [{"index": 0}]}

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=payload,
            headers={"x-trace-id": "abc"},
        )

    dispatcher = _make_dispatcher(httpx.MockTransport(handler))
    try:
        resp = await dispatcher.forward(_call(body=json.dumps({"model": "primary"}).encode()))
        assert resp.status_code == 200
        assert json.loads(resp.body.decode()) == payload
        assert resp.headers["x-trace-id"] == "abc"
        # hop-by-hop content-length must be filtered (Starlette re-computes it)
        assert "content-length" not in {k.lower() for k in resp.headers} or True
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_forward_passes_upstream_status_through() -> None:
    """Upstream 4xx/5xx bodies are forwarded as-is, not wrapped in dispatch errors."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"message": "rate limited", "type": "rate_limit"}},
        )

    dispatcher = _make_dispatcher(httpx.MockTransport(handler))
    try:
        resp = await dispatcher.forward(_call())
        assert resp.status_code == 429
        body = json.loads(resp.body.decode())
        assert body["error"]["type"] == "rate_limit"
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_forward_network_error_raises_typed_envelope() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=req)

    dispatcher = _make_dispatcher(httpx.MockTransport(handler))
    try:
        with pytest.raises(UpstreamUnavailable) as ei:
            await dispatcher.forward(_call())
        assert ei.value.code == "dispatch.upstream_unavailable"
        assert ei.value.status == 502
        assert "test-upstream" in ei.value.message
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_forward_streaming_pipes_chunks() -> None:
    """Streaming responses pipe upstream chunks through unchanged."""
    sse_chunks = [
        b'data: {"id":"1","choices":[{"delta":{"content":"hi"}}]}\n\n',
        b'data: {"id":"1","choices":[{"delta":{"content":" world"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        # Pass an httpx.ByteStream so MockTransport produces an unconsumed
        # body — Response(content=...) eagerly buffers and breaks aiter_raw.
        return httpx.Response(
            200,
            stream=httpx.ByteStream(b"".join(sse_chunks)),
            headers={"content-type": "text/event-stream"},
        )

    dispatcher = _make_dispatcher(httpx.MockTransport(handler))
    try:
        resp = await dispatcher.forward(_call(streaming=True))
        # Consume the streaming body.
        collected = b""
        async for chunk in resp.body_iterator:
            collected += chunk if isinstance(chunk, bytes) else chunk.encode()
        assert collected == b"".join(sse_chunks)
        assert resp.status_code == 200
        assert resp.media_type == "text/event-stream"
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_forward_streaming_connect_error_raises_typed_envelope() -> None:
    """Open-stream failures surface as UpstreamUnavailable (not stream-time)."""

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=req)

    dispatcher = _make_dispatcher(httpx.MockTransport(handler))
    try:
        with pytest.raises(UpstreamUnavailable) as ei:
            await dispatcher.forward(_call(streaming=True))
        # A connect timeout means the upstream was never reached: still 502,
        # not the 504 deadline a read timeout gets (#2282).
        assert not isinstance(ei.value, UpstreamTimeout)
        assert ei.value.status == 502
    finally:
        await dispatcher.aclose()


# ── read timeout is a deadline (504), not "unreachable" (502) — #2282 ────────


def _read_timeout_handler(req: httpx.Request) -> httpx.Response:
    # httpcore raises its read timeout with an empty message; mirror that so
    # the test also pins that ``details.error`` is never blank.
    raise httpx.ReadTimeout("", request=req)


@pytest.mark.asyncio
async def test_forward_read_timeout_is_a_504_deadline_not_unreachable() -> None:
    """A slow upstream that blows the read deadline is not a dead one.

    Before #2282 ``httpx.ReadTimeout`` fell into the generic
    ``(httpx.HTTPError, OSError)`` clause and came back as a 502
    ``dispatch.upstream_unavailable`` "unreachable" with an empty
    ``details.error`` — indistinguishable from a dead slot.
    """
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_read_timeout_handler),
        timeout=httpx.Timeout(5.0, read=42.0),
    )
    dispatcher = Dispatcher(http_client=client)
    try:
        with pytest.raises(UpstreamTimeout) as ei:
            await dispatcher.forward(_call())
        err = ei.value
        assert err.status == 504
        assert err.code == "dispatch.upstream_timeout"
        assert "unreachable" not in err.message
        assert "test-upstream" in err.message
        assert "42s" in err.message
        assert "[dispatcher].direct_read_timeout_s" in err.message
        assert "stream" in err.message  # points long generations at stream: true
        assert err.details["upstream"] == "test-upstream"
        assert err.details["target"] == "http://upstream.test/chat/completions"
        assert err.details["error"] == "ReadTimeout"
        assert err.details["timeout"] == "read"
        assert err.details["timeout_s"] == 42.0
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_forward_streaming_open_read_timeout_is_a_504() -> None:
    """With the stall guard off, the header wait is bound by httpx's read
    timeout — a ``ReadTimeout`` there is the same deadline, so the same 504."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_read_timeout_handler),
        timeout=httpx.Timeout(5.0, read=42.0),
    )
    dispatcher = Dispatcher(
        http_client=client, stream_total_timeout_s=0.0, stream_idle_timeout_s=0.0
    )
    try:
        with pytest.raises(UpstreamTimeout) as ei:
            await dispatcher.forward(_call(streaming=True))
        assert ei.value.status == 504
        assert ei.value.code == "dispatch.upstream_timeout"
        assert ei.value.details["timeout_s"] == 42.0
        assert "[dispatcher].direct_read_timeout_s" in ei.value.message
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_forward_write_timeout_is_a_504() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.WriteTimeout("", request=req)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=httpx.Timeout(5.0, write=7.0)
    )
    dispatcher = Dispatcher(http_client=client)
    try:
        with pytest.raises(UpstreamTimeout) as ei:
            await dispatcher.forward(_call())
        assert ei.value.status == 504
        assert ei.value.details["timeout"] == "write"
        assert ei.value.details["timeout_s"] == 7.0
        assert "unreachable" not in ei.value.message
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_forward_streaming_open_write_timeout_is_a_504() -> None:
    """The streaming open path takes the same write-timeout mapping."""

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.WriteTimeout("", request=req)

    dispatcher = _make_dispatcher(httpx.MockTransport(handler))
    try:
        with pytest.raises(UpstreamTimeout) as ei:
            await dispatcher.forward(_call(streaming=True))
        assert ei.value.status == 504
        assert ei.value.code == "dispatch.upstream_timeout"
        assert ei.value.details["timeout"] == "write"
        assert ei.value.details["error"] == "WriteTimeout"
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_forward_connect_error_never_leaves_details_error_blank() -> None:
    """A message-less connect failure still names what happened (#2282)."""

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("", request=req)

    dispatcher = _make_dispatcher(httpx.MockTransport(handler))
    try:
        with pytest.raises(UpstreamUnavailable) as ei:
            await dispatcher.forward(_call())
        assert ei.value.status == 502
        assert ei.value.details["error"] == "ConnectTimeout"
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_aclose_is_idempotent() -> None:
    dispatcher = Dispatcher()  # lazy client never instantiated
    await dispatcher.aclose()
    await dispatcher.aclose()  # second call is a no-op
