"""Regression tests for dispatcher HTTP client pool bounds (#415).

Without the fix:
  - The dispatcher's shared httpx.AsyncClient had no connection limits
    (httpx default = 100 max_connections) and a 300 s read timeout,
    allowing slow upstreams to exhaust the pool and starve new requests.

With the fix:
  - max_connections=64, max_keepalive_connections=16  (matches registry.py)
  - non-streaming read timeout = 60 s  (was 300 s)

These tests verify those bounds are in place and that the pool surfaces
a bounded error rather than hanging indefinitely.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from hal0.dispatcher.router import (
    Dispatcher,
    UpstreamCall,
    UpstreamUnavailable,
    _DIRECT_READ_TIMEOUT_S,
    _DISPATCHER_MAX_CONNECTIONS,
    _DISPATCHER_MAX_KEEPALIVE,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _call(
    *,
    streaming: bool = False,
    target: str = "http://upstream.test/v1/chat/completions",
) -> UpstreamCall:
    return UpstreamCall(
        upstream_name="test-upstream",
        target_url=target,
        headers={"content-type": "application/json"},
        body=b'{"model":"agent"}',
        streaming=streaming,
        method="POST",
    )


# ── constant assertions (fail without the fix) ────────────────────────────────


def test_dispatcher_pool_constants_are_bounded() -> None:
    """The module constants must reflect the bounded values from the fix."""
    # Before the fix these didn't exist; importing them would NameError.
    assert _DISPATCHER_MAX_CONNECTIONS == 64, (
        "max_connections should be 64, matching registry.py"
    )
    assert _DISPATCHER_MAX_KEEPALIVE == 16, (
        "max_keepalive_connections should be 16, matching registry.py"
    )


def test_direct_read_timeout_reduced() -> None:
    """Non-streaming read timeout must be <= 60 s (was 300 s before the fix)."""
    assert _DIRECT_READ_TIMEOUT_S <= 60.0, (
        f"Non-streaming read timeout is {_DIRECT_READ_TIMEOUT_S} s — "
        "must be <= 60 s to prevent pool starvation (#415)"
    )


# ── lazy-client construction (fail without limits= kwarg) ─────────────────────


def test_lazy_http_client_has_bounded_pool() -> None:
    """The lazily-constructed client must carry the connection limits."""
    d = Dispatcher()
    client = d._get_http_client()
    pool = client._transport._pool  # httpcore.AsyncConnectionPool
    assert pool._max_connections == _DISPATCHER_MAX_CONNECTIONS, (
        f"Expected max_connections={_DISPATCHER_MAX_CONNECTIONS}, "
        f"got {pool._max_connections} — limits= not passed to AsyncClient"
    )
    assert pool._max_keepalive_connections == _DISPATCHER_MAX_KEEPALIVE, (
        f"Expected max_keepalive_connections={_DISPATCHER_MAX_KEEPALIVE}, "
        f"got {pool._max_keepalive_connections}"
    )


def test_lazy_http_client_read_timeout() -> None:
    """The lazily-constructed client's read timeout must use the constant."""
    d = Dispatcher()
    client = d._get_http_client()
    assert client.timeout.read == _DIRECT_READ_TIMEOUT_S, (
        f"Client read timeout is {client.timeout.read} s, "
        f"expected {_DIRECT_READ_TIMEOUT_S} s"
    )


# ── pool-exhaustion / timeout surfaces as error not hang ─────────────────────


@pytest.mark.asyncio
async def test_pool_timeout_raises_upstream_unavailable() -> None:
    """A PoolTimeout from a saturated pool surfaces as UpstreamUnavailable.

    We inject an httpx.PoolTimeout via a transport that raises it directly,
    simulating what happens when all connections in a bounded pool are
    occupied.  Before #415 the dispatcher had no limits= so PoolTimeout
    would never fire; after the fix, _forward_direct catches the broader
    httpx.HTTPError (which PoolTimeout extends) and wraps it.
    """

    def pool_timeout_handler(req: httpx.Request) -> httpx.Response:
        raise httpx.PoolTimeout("connection pool is full", request=req)

    client = httpx.AsyncClient(transport=httpx.MockTransport(pool_timeout_handler))
    dispatcher = Dispatcher(http_client=client)

    try:
        with pytest.raises(UpstreamUnavailable) as exc_info:
            await dispatcher.forward(_call(streaming=False))
        # Ensure the error is correctly attributed and carries the upstream name.
        assert "test-upstream" in exc_info.value.message
        assert exc_info.value.status == 502
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_pool_timeout_streaming_raises_upstream_unavailable() -> None:
    """A PoolTimeout on stream-open also surfaces as UpstreamUnavailable."""

    def pool_timeout_handler(req: httpx.Request) -> httpx.Response:
        raise httpx.PoolTimeout("connection pool is full", request=req)

    client = httpx.AsyncClient(transport=httpx.MockTransport(pool_timeout_handler))
    dispatcher = Dispatcher(http_client=client)

    try:
        with pytest.raises(UpstreamUnavailable) as exc_info:
            await dispatcher.forward(_call(streaming=True))
        assert "test-upstream" in exc_info.value.message
        assert exc_info.value.status == 502
    finally:
        await dispatcher.aclose()
