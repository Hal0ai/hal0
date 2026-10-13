"""Slot traffic goes direct, never through the proxy environment (#2585).

The dispatcher's shared client serves both local slots (llama-server
children and container slots on ``127.0.0.1``) and remote cloud providers,
so it cannot turn ``trust_env`` off. Before the fix, with ``HTTP_PROXY`` /
``HTTPS_PROXY`` / ``ALL_PROXY`` set and ``NO_PROXY`` not covering loopback,
httpx handed every slot request, prompt included, to the proxy.
``hal0.http_client.async_client`` now mounts loopback hosts on a direct
transport, and the slot-facing clients are built with it.

These tests run a real fake-proxy listener and a real fake-slot listener: a
loopback request must reach the slot and never the proxy, and a remote
request must still reach the proxy (absolute-form request line, or CONNECT
for https).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest

from hal0.dispatcher.router import (
    _DISPATCHER_MAX_CONNECTIONS,
    _DISPATCHER_MAX_KEEPALIVE,
    Dispatcher,
    UpstreamCall,
)
from hal0.http_client import _loopback_mount_patterns, async_client
from hal0.service_identity import _LOOPBACK_HOSTS, is_loopback_url
from hal0.upstreams.registry import Upstream, UpstreamRegistry

_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
_NO_PROXY_VARS = ("NO_PROXY", "no_proxy")


class _FakeHttpServer:
    """A loopback HTTP/1.1 listener that records each request line and answers 200."""

    def __init__(self) -> None:
        self.request_lines: list[str] = []
        self.port = 0
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = (await reader.readline()).decode("latin-1").strip()
            self.request_lines.append(line)
            length = 0
            while True:
                header = await reader.readline()
                if header in (b"\r\n", b"\n", b""):
                    break
                name, _, value = header.decode("latin-1").partition(":")
                if name.strip().lower() == "content-length":
                    length = int(value.strip())
            if length:
                await reader.readexactly(length)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: 2\r\nConnection: close\r\n\r\n{}"
            )
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()


@dataclass
class _Net:
    proxy: _FakeHttpServer
    slot: _FakeHttpServer

    def slot_url(self, host: str = "127.0.0.1", path: str = "/v1/chat/completions") -> str:
        return f"http://{host}:{self.slot.port}{path}"


async def _net(monkeypatch: pytest.MonkeyPatch, proxy_vars: tuple[str, ...]) -> _Net:
    net = _Net(proxy=_FakeHttpServer(), slot=_FakeHttpServer())
    await net.proxy.start()
    await net.slot.start()
    proxy_url = f"http://127.0.0.1:{net.proxy.port}"
    for name in _PROXY_VARS:
        for spelled in (name, name.lower()):
            if name in proxy_vars:
                monkeypatch.setenv(spelled, proxy_url)
            else:
                monkeypatch.delenv(spelled, raising=False)
    for name in _NO_PROXY_VARS:
        monkeypatch.delenv(name, raising=False)
    return net


@pytest.fixture(
    params=[("HTTP_PROXY",), ("ALL_PROXY",), ("HTTPS_PROXY", "ALL_PROXY"), _PROXY_VARS],
    ids=["http_proxy", "all_proxy", "https+all_proxy", "every_proxy_var"],
)
async def net(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[_Net]:
    """Fake proxy + fake slot, with plain-http requests proxied by the env and no NO_PROXY."""
    n = await _net(monkeypatch, request.param)
    try:
        yield n
    finally:
        await n.proxy.stop()
        await n.slot.stop()


def _call(url: str) -> UpstreamCall:
    return UpstreamCall(
        upstream_name="u",
        target_url=url,
        headers={"content-type": "application/json"},
        body=b'{"model":"m","messages":[{"role":"user","content":"secret prompt"}]}',
        streaming=False,
        method="POST",
    )


# ── the dispatcher's shared client ───────────────────────────────────────


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
async def test_dispatcher_slot_request_skips_the_proxy(net: _Net, host: str) -> None:
    dispatcher = Dispatcher()
    try:
        response = await dispatcher.forward(_call(net.slot_url(host)))
    finally:
        await dispatcher.aclose()

    assert response.status_code == 200
    assert net.proxy.request_lines == [], "slot request (prompt and all) went to the proxy"
    assert net.slot.request_lines == ["POST /v1/chat/completions HTTP/1.1"]


async def test_dispatcher_streaming_slot_request_skips_the_proxy(net: _Net) -> None:
    call = _call(net.slot_url())
    call = UpstreamCall(**{**call.__dict__, "streaming": True})
    dispatcher = Dispatcher()
    try:
        response = await dispatcher.forward(call)
        async for _chunk in response.body_iterator:  # type: ignore[attr-defined]
            pass
    finally:
        await dispatcher.aclose()

    assert net.proxy.request_lines == []
    assert net.slot.request_lines == ["POST /v1/chat/completions HTTP/1.1"]


async def test_dispatcher_remote_request_still_uses_the_proxy(net: _Net) -> None:
    dispatcher = Dispatcher()
    try:
        response = await dispatcher.forward(_call("http://cloud.invalid/v1/chat/completions"))
    finally:
        await dispatcher.aclose()

    assert response.status_code == 200
    # Absolute-form request line: the proxy, not a direct connection, carried it.
    assert net.proxy.request_lines == ["POST http://cloud.invalid/v1/chat/completions HTTP/1.1"]
    assert net.slot.request_lines == []


def test_dispatcher_loopback_transport_keeps_the_pool_bounds() -> None:
    """The direct loopback transport carries the dispatcher's limits (#415)."""
    client = Dispatcher()._get_http_client()
    for url in ("http://127.0.0.1:8081", "http://localhost:8081", "http://[::1]:8081"):
        transport = client._transport_for_url(httpx.URL(url))
        assert transport is not client._transport, url
        pool = transport._pool  # type: ignore[attr-defined]  # httpcore.AsyncConnectionPool
        assert pool._max_connections == _DISPATCHER_MAX_CONNECTIONS, url
        assert pool._max_keepalive_connections == _DISPATCHER_MAX_KEEPALIVE, url
    # One transport for every loopback host, so slot traffic shares one pool.
    transports = {
        id(client._transport_for_url(httpx.URL(u)))
        for u in ("http://127.0.0.1:1", "http://localhost:1", "http://[::1]:1")
    }
    assert len(transports) == 1


# ── the other clients that reach slots ───────────────────────────────────


async def test_registry_slot_health_probe_skips_the_proxy(net: _Net) -> None:
    registry = UpstreamRegistry()
    slot = Upstream(name="s", kind="slot", url=net.slot_url(path="/v1"), slot_name="s")
    try:
        assert await registry.health(slot) is True
    finally:
        await registry.aclose()

    assert net.proxy.request_lines == []
    assert net.slot.request_lines == ["GET /health HTTP/1.1"]


async def test_registry_remote_probe_still_uses_the_proxy(net: _Net) -> None:
    registry = UpstreamRegistry()
    remote = Upstream(name="r", kind="remote", url="http://cloud.invalid/v1", auth_style="none")
    try:
        assert await registry.health(remote) is True
    finally:
        await registry.aclose()

    assert net.proxy.request_lines == ["GET http://cloud.invalid/health HTTP/1.1"]
    assert net.slot.request_lines == []


async def test_omni_router_client_skips_the_proxy_for_loopback(net: _Net) -> None:
    from hal0.api import _omni_router_http_client

    client = _omni_router_http_client()
    try:
        loop_resp = await client.post(net.slot_url(), json={"messages": []})
        remote_resp = await client.post("http://cloud.invalid/v1/chat/completions", json={})
    finally:
        await client.aclose()

    assert loop_resp.status_code == remote_resp.status_code == 200
    assert net.slot.request_lines == ["POST /v1/chat/completions HTTP/1.1"]
    assert net.proxy.request_lines == ["POST http://cloud.invalid/v1/chat/completions HTTP/1.1"]


async def test_npu_trio_per_call_client_skips_the_proxy(net: _Net) -> None:
    from hal0.dispatcher.npu_trio import NpuTrioRouter

    router = NpuTrioRouter(slot_manager=None)
    resp = await router._post(
        net.slot_url(path="/v1/audio/transcriptions"),
        content=b"audio",
        headers={"content-type": "application/octet-stream"},
    )

    assert resp.status_code == 200
    assert net.proxy.request_lines == []
    assert net.slot.request_lines == ["POST /v1/audio/transcriptions HTTP/1.1"]


async def test_llama_server_health_probe_skips_the_proxy(net: _Net) -> None:
    from hal0.providers.llama_server import LlamaServerProvider

    await LlamaServerProvider().health(net.slot.port)

    assert net.proxy.request_lines == []
    assert net.slot.request_lines[0] == "GET /v1/models HTTP/1.1"


async def test_slot_voices_probe_skips_the_proxy(net: _Net) -> None:
    from hal0.slots.voices import fetch_for_slot

    await fetch_for_slot("tts", net.slot.port)

    assert net.proxy.request_lines == []
    assert net.slot.request_lines == ["GET /v1/audio/voices HTTP/1.1"]


# ── async_client itself ──────────────────────────────────────────────────


@pytest.fixture
async def https_net(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Net]:
    """Only HTTPS_PROXY set: https requests are proxied (CONNECT), plain http is not."""
    n = await _net(monkeypatch, ("HTTPS_PROXY",))
    try:
        yield n
    finally:
        await n.proxy.stop()
        await n.slot.stop()


async def test_async_client_https_loopback_skips_https_proxy(https_net: _Net) -> None:
    async with async_client(timeout=2.0) as client:
        # The fake slot speaks plain HTTP, so the TLS handshake fails; what
        # matters is where the connection went.
        with pytest.raises(httpx.HTTPError):
            await client.get(f"https://127.0.0.1:{https_net.slot.port}/health")
    assert https_net.proxy.request_lines == []
    assert len(https_net.slot.request_lines) == 1


async def test_async_client_https_remote_uses_https_proxy(https_net: _Net) -> None:
    async with async_client(timeout=2.0) as client:
        with pytest.raises(httpx.HTTPError):
            await client.get("https://cloud.invalid/v1/models")
    assert https_net.proxy.request_lines == ["CONNECT cloud.invalid:443 HTTP/1.1"]
    assert https_net.slot.request_lines == []


async def test_async_client_keeps_a_callers_transport() -> None:
    """A caller-supplied transport still sees loopback requests (tests rely on it)."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200)

    async with async_client(transport=httpx.MockTransport(handler)) as client:
        await client.get("http://127.0.0.1:9/health")
    assert seen == ["http://127.0.0.1:9/health"]


def test_async_client_trust_env_false_needs_no_mount() -> None:
    client = async_client(trust_env=False)
    assert client._transport_for_url(httpx.URL("http://127.0.0.1:1")) is client._transport


@pytest.mark.parametrize("option", ["proxy", "mounts", "transport"])
async def test_async_client_forwarded_none_routing_keeps_the_mount(net: _Net, option: str) -> None:
    """A forwarded ``proxy=None`` (or ``mounts``/``transport``) is not an override.

    httpx still reads the proxy environment for it, so the loopback mount must
    stay and the slot request must go direct.
    """
    async with async_client(**{option: None}) as client:
        await client.get(f"http://127.0.0.1:{net.slot.port}/health")
    assert net.proxy.request_lines == []
    assert net.slot.request_lines[0] == "GET /health HTTP/1.1"


def test_loopback_patterns_cover_exactly_the_loopback_hosts() -> None:
    """One owner for "loopback": the same hosts ``is_loopback_url`` accepts."""
    patterns = _loopback_mount_patterns()
    assert len(patterns) == len(_LOOPBACK_HOSTS)
    for pattern in patterns:
        assert is_loopback_url(pattern.replace("all://", "http://", 1)), pattern
