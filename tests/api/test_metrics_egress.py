"""No hal0-internal API path ever leaves for a third-party upstream (#1425).

Every assertion here is on the **actual outbound URL set** the aggregator
produces, captured at the httpx seam — not on a log message. Issue #1425 was
found by grepping the journal; a test that also greps a log would not catch a
regression that egresses silently.
"""

from __future__ import annotations

import asyncio
import types

import httpx
import pytest

import hal0.api.routes.hardware as hw_mod
from hal0.upstreams.registry import Upstream, UpstreamRegistry


class _RecordingClient:
    """httpx.AsyncClient stand-in that records every GET and answers 200."""

    def __init__(self, calls: list[str], payload: object, delay_hosts: set[str] | None = None):
        self._calls = calls
        self._payload = payload
        self._delay_hosts = delay_hosts or set()

    async def __aenter__(self) -> _RecordingClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get(self, url: str, **_kw: object) -> object:
        self._calls.append(url)
        if httpx.URL(url).host in self._delay_hosts:
            # Simulate an unreachable peer: hang until the caller's own
            # timeout budget gives up on us.
            await asyncio.sleep(3600)
        return types.SimpleNamespace(status_code=200, json=lambda: self._payload)


def _request_with(upstreams: UpstreamRegistry) -> object:
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(upstreams=upstreams))
    )


def _registry(*upstreams: Upstream) -> UpstreamRegistry:
    reg = UpstreamRegistry()
    for u in upstreams:
        reg.add(u)
    return reg


OPENROUTER = Upstream(name="openrouter", kind="remote", url="https://openrouter.ai/api/v1")
MINIMAX = Upstream(name="minimax", kind="remote", url="https://api.minimax.io/v1")
PEER = Upstream(name="peer", kind="remote", url="http://10.0.1.150:8080/v1")
SLOT = Upstream(name="agent", kind="slot", url="http://127.0.0.1:8087/v1", slot_name="agent")


def _patch_client(monkeypatch: pytest.MonkeyPatch, calls: list[str], **kw: object) -> None:
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **k: _RecordingClient(calls, {"ok": {}}, **kw),  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("suffix", ["/api/slots/metrics", "/api/stats/hardware"])
async def test_third_party_upstreams_get_zero_requests(
    monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    calls: list[str] = []
    _patch_client(monkeypatch, calls)

    out = await hw_mod._proxy_upstream_endpoint(
        _request_with(_registry(OPENROUTER, MINIMAX, SLOT)), suffix
    )

    assert calls == []
    assert out == {}


@pytest.mark.parametrize("suffix", ["/api/slots/metrics", "/api/stats/hardware"])
async def test_a_genuine_hal0_peer_still_receives_the_fanout(
    monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    """The feature is gated, not disabled."""
    calls: list[str] = []
    _patch_client(monkeypatch, calls)

    out = await hw_mod._proxy_upstream_endpoint(
        _request_with(_registry(OPENROUTER, MINIMAX, PEER, SLOT)), suffix
    )

    assert calls == [f"http://10.0.1.150:8080{suffix}"]
    assert out == {"peer": {"ok": {}}}


async def test_public_hostname_peer_opts_in_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _patch_client(monkeypatch, calls)
    peer = Upstream(name="wan", kind="remote", url="https://hal0.example.com/v1", hal0_peer=True)

    await hw_mod._proxy_upstream_endpoint(
        _request_with(_registry(OPENROUTER, peer)), "/api/slots/metrics"
    )

    assert calls == ["https://hal0.example.com/api/slots/metrics"]


async def test_no_outbound_url_contains_a_doubled_api_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even an operator who declares openrouter's URL shape as a peer.

    ``hal0_peer = true`` on a ``/api/v1`` base is the exact input that
    produced ``https://openrouter.ai/api/api/slots/metrics`` in the wild.
    """
    calls: list[str] = []
    _patch_client(monkeypatch, calls)
    weird = Upstream(name="weird", kind="remote", url="https://peer.example/api/v1", hal0_peer=True)

    await hw_mod._proxy_upstream_endpoint(_request_with(_registry(weird)), "/api/slots/metrics")

    assert calls == ["https://peer.example/api/slots/metrics"]
    assert not any("/api/api/" in c for c in calls)


async def test_disabled_peer_is_not_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _patch_client(monkeypatch, calls)
    off = Upstream(name="peer", kind="remote", url="http://10.0.1.150:8080/v1", enabled=False)

    await hw_mod._proxy_upstream_endpoint(_request_with(_registry(off)), "/api/slots/metrics")

    assert calls == []


async def test_unreachable_peer_does_not_serialise_the_fanout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Peers are probed concurrently and bounded by the httpx timeout.

    The fake client hangs forever on ``10.0.1.151``; the caller passes a
    0.2 s timeout, so the whole fan-out must finish in roughly one timeout,
    not N of them, and the reachable peer must still report.
    """
    calls: list[str] = []

    class _Client(_RecordingClient):
        pass

    def _factory(*_a: object, timeout: float = 0.0, **_k: object) -> object:
        client = _Client(calls, {"ok": {}}, delay_hosts={"10.0.1.151", "10.0.1.152", "10.0.1.153"})

        async def _get(url: str, **kw: object) -> object:
            return await asyncio.wait_for(_RecordingClient.get(client, url, **kw), timeout=timeout)

        client.get = _get  # type: ignore[method-assign]
        return client

    monkeypatch.setattr(httpx, "AsyncClient", _factory)

    dead = [
        Upstream(name=f"dead{i}", kind="remote", url=f"http://10.0.1.15{i}:8080/v1")
        for i in (1, 2, 3)
    ]
    reg = _registry(PEER, *dead)

    loop = asyncio.get_running_loop()
    started = loop.time()
    out = await hw_mod._proxy_upstream_endpoint(
        _request_with(reg), "/api/slots/metrics", timeout_s=0.2
    )
    elapsed = loop.time() - started

    assert out["peer"] == {"ok": {}}
    assert out["dead1"] is None and out["dead2"] is None and out["dead3"] is None
    # Serial would be >= 3 * 0.2 s; concurrent is ~1 * 0.2 s.
    assert elapsed < 0.5, f"fan-out serialised: {elapsed:.2f}s"
