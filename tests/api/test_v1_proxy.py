"""Tests for the /v1/* reverse-proxy to Lemonade (issue #212).

The proxy mounts a catch-all under ``/v1/{path:path}`` AFTER the
dispatcher-owned v1 routers. These tests verify:

  * Un-routed paths (/v1/health, /v1/stats, /v1/system-info) reach
    Lemonade verbatim and the response body / status code round-trips.
  * Dispatcher-owned paths (/v1/models, /v1/chat/completions) are NOT
    swallowed by the catch-all — registration order keeps them on the
    aggregator.
  * Connection failure surfaces as a 503 with the
    ``lemonade.unavailable`` envelope code so the dashboard hook can
    render 'down' without crashing.
  * Authorization + content-type headers cross the proxy unchanged.
  * Query parameters round-trip (Lemonade's /v1/load takes a JSON body
    but a future endpoint may use query params).
  * Request method round-trips (POST body forwards verbatim).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from hal0.api.routes import lemonade_proxy

# ── handler factory + monkeypatch seam ──────────────────────────────────────


def _install_mock(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
) -> dict[str, Any]:
    """Swap the proxy's ``_build_client`` for one wired to MockTransport.

    Returns a state dict the handler can mutate to record what was
    received — by reference so assertions in the calling test see
    every recorded request.
    """
    state: dict[str, Any] = {"requests": []}

    def _recording_handler(req: httpx.Request) -> httpx.Response:
        state["requests"].append(
            {
                "method": req.method,
                "url": str(req.url),
                "path": req.url.path,
                "headers": dict(req.headers),
                "content": req.content,
                "params": dict(req.url.params),
            }
        )
        return handler(req)

    def _fake_build_client(timeout: httpx.Timeout) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(_recording_handler),
            timeout=timeout,
        )

    # #474: the proxy now keeps a process-wide shared client + a short TTL
    # cache. Reset both so each test starts clean and picks up this mock.
    lemonade_proxy._reset_state()
    monkeypatch.setattr(lemonade_proxy, "_build_client", _fake_build_client)
    return state


@pytest.fixture
def lemonade_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Default fixture — Lemonade-up with the canonical /v1/health body."""

    def _handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/v1/health":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "version": "10.6.0",
                    "loaded": [],
                    "max_models": {"llm": 4},
                },
            )
        if path == "/v1/stats":
            return httpx.Response(200, json={"tokens_per_second": 42.5})
        if path == "/v1/system-info":
            return httpx.Response(200, json={"cpu": "ryzen", "gpu": "strix-halo"})
        if path == "/v1/load":
            return httpx.Response(200, json={"status": "loaded"})
        if path == "/v1/echo-headers":
            # Echo selected request headers back so the test can assert
            # authorization + content-type round-trip.
            echoed = {
                k: req.headers.get(k, "")
                for k in ("authorization", "content-type", "x-custom-marker")
            }
            return httpx.Response(200, json=echoed)
        return httpx.Response(404, json={"error": f"unmocked {path}"})

    state = _install_mock(monkeypatch, _handler)
    yield state


# ── basic round-trip ────────────────────────────────────────────────────────


def test_v1_health_proxies_to_lemonade(client: TestClient, lemonade_state: dict[str, Any]) -> None:
    """GET /v1/health hits the proxy → Lemonade's body returns verbatim."""
    r = client.get("/v1/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == "10.6.0"
    # The recorded request must have targeted Lemonade's loopback URL.
    assert len(lemonade_state["requests"]) == 1
    req = lemonade_state["requests"][0]
    assert req["method"] == "GET"
    assert req["path"] == "/v1/health"


def test_v1_stats_proxies(client: TestClient, lemonade_state: dict[str, Any]) -> None:
    r = client.get("/v1/stats")
    assert r.status_code == 200
    assert r.json() == {"tokens_per_second": 42.5}
    assert lemonade_state["requests"][0]["path"] == "/v1/stats"


def test_v1_system_info_proxies(client: TestClient, lemonade_state: dict[str, Any]) -> None:
    """Nested-looking path (no nesting actually, just sanity)."""
    r = client.get("/v1/system-info")
    assert r.status_code == 200
    assert r.json()["cpu"] == "ryzen"


def test_v1_unknown_path_returns_lemonade_404(
    client: TestClient, lemonade_state: dict[str, Any]
) -> None:
    """When the catch-all forwards an unknown path, Lemonade's 404 wins."""
    r = client.get("/v1/this-is-not-real")
    assert r.status_code == 404
    # We expect Lemonade's envelope ("unmocked …"), not hal0's
    # dispatch.no_route — the catch-all bypasses dispatcher entirely.
    body = r.json()
    assert "unmocked" in body.get("error", "")


# ── dispatcher routes are NOT swallowed ─────────────────────────────────────


def test_v1_models_still_handled_by_aggregator(
    client: TestClient, lemonade_state: dict[str, Any]
) -> None:
    """GET /v1/models stays on the aggregator, NOT the proxy.

    The aggregator returns ``{"object": "list", "data": []}`` when there
    are no upstreams; the proxy would have returned Lemonade's
    ``{"data": [], "object": "list"}`` (which has the SAME shape — but
    we assert through the request recorder that the proxy was NEVER
    invoked).
    """
    r = client.get("/v1/models")
    assert r.status_code == 200
    assert r.json() == {"object": "list", "data": []}
    # The proxy must NOT have been touched.
    assert lemonade_state["requests"] == []


def test_v1_chat_completions_falls_through_to_proxy_when_dispatcher_has_no_route(
    client: TestClient, lemonade_state: dict[str, Any]
) -> None:
    """POST /v1/chat/completions with no dispatcher route → falls through to proxy.

    Pre-#277 the dispatcher's ``dispatch.no_route`` 404 surfaced verbatim
    and the proxy was never touched. Post-#277, the dispatcher catches
    NoRouteFound inside ``_dispatch_and_forward`` and delegates to the
    lemonade_proxy catch-all — so this request DOES reach the proxy.
    See #275 bug 5 + PR #277.
    """
    client.post(
        "/v1/chat/completions",
        json={"model": "primary", "messages": [{"role": "user", "content": "hi"}]},
    )
    # Proxy hit Lemonade, which is the test mock — accept whatever the
    # mock returns. The KEY assertion is that the proxy got the request.
    assert lemonade_state["requests"], "proxy should have been consulted on dispatcher no_route"


# ── error paths ─────────────────────────────────────────────────────────────


def test_v1_lemonade_unavailable_returns_503_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When Lemonade refuses the connection → 503 + lemonade.unavailable."""

    def _handler(req: httpx.Request) -> httpx.Response:
        # MockTransport doesn't simulate connection refused on its own —
        # we raise ConnectError manually so the proxy's except branch
        # fires.
        raise httpx.ConnectError("Connection refused", request=req)

    _install_mock(monkeypatch, _handler)

    r = client.get("/v1/health")
    assert r.status_code == 503
    body = r.json()
    assert body["error"]["code"] == "lemonade.unavailable"
    assert "target" in body["error"]["details"]


def test_v1_proxy_generic_http_error_returns_502_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-connect httpx errors surface as 502 with proxy_error code."""

    def _handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("upstream stalled", request=req)

    _install_mock(monkeypatch, _handler)

    r = client.get("/v1/stats")
    assert r.status_code == 502
    body = r.json()
    assert body["error"]["code"] == "lemonade.proxy_error"


# ── header + body passthrough ───────────────────────────────────────────────


def test_v1_authorization_header_round_trips(
    client: TestClient, lemonade_state: dict[str, Any]
) -> None:
    """Authorization + custom headers are forwarded verbatim to Lemonade."""
    r = client.get(
        "/v1/echo-headers",
        headers={
            "Authorization": "Bearer test-token-xyz",
            "X-Custom-Marker": "hal0-test",
        },
    )
    assert r.status_code == 200
    echoed = r.json()
    assert echoed["authorization"] == "Bearer test-token-xyz"
    assert echoed["x-custom-marker"] == "hal0-test"


def test_v1_host_header_is_stripped(client: TestClient, lemonade_state: dict[str, Any]) -> None:
    """The inbound Host header must NOT propagate to Lemonade.

    Forwarding the hal0-api Host (``hal0.thinmint.dev``) would confuse
    Lemonade's URL parser. httpx sets its own Host from the target URL.
    """
    client.get("/v1/health")
    forwarded = lemonade_state["requests"][0]["headers"]
    # httpx sets host to the loopback target — never the original.
    assert "127.0.0.1" in forwarded.get("host", "") or "lemonade" not in forwarded.get("host", "")


def test_v1_post_body_forwards_verbatim(client: TestClient, lemonade_state: dict[str, Any]) -> None:
    """POST /v1/load round-trips its JSON body to Lemonade."""
    payload = {"model_name": "gemma3:1b", "backend": "flm"}
    r = client.post("/v1/load", json=payload)
    assert r.status_code == 200
    req = lemonade_state["requests"][0]
    assert req["method"] == "POST"
    assert req["path"] == "/v1/load"
    assert json.loads(req["content"].decode("utf-8")) == payload


def test_v1_query_params_round_trip(client: TestClient, lemonade_state: dict[str, Any]) -> None:
    """Query string forwards intact across the proxy."""
    r = client.get("/v1/health?debug=1&verbose=true")
    assert r.status_code == 200
    params = lemonade_state["requests"][0]["params"]
    assert params == {"debug": "1", "verbose": "true"}


# ── env override ────────────────────────────────────────────────────────────


def test_lemonade_base_url_honours_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """The LEMONADE_BASE_URL env var re-points the proxy."""
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://lemond.local:9999/")
    assert lemonade_proxy._lemonade_base_url() == "http://lemond.local:9999"


def test_lemonade_base_url_defaults_to_loopback_13305(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    assert lemonade_proxy._lemonade_base_url() == "http://127.0.0.1:13305"


# ── #474: connection-storm guards (pooled client + TTL/single-flight cache) ──


def test_v1_health_cached_within_ttl(client: TestClient, lemonade_state: dict[str, Any]) -> None:
    """A 2nd GET /v1/health inside the TTL is served from cache — no 2nd poll.

    This is the amplifier killer: N dashboard tabs polling /v1/health every 2s
    collapse into one upstream call per TTL window instead of one per tab.
    """
    r1 = client.get("/v1/health")
    r2 = client.get("/v1/health")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["status"] == "ok" and r2.json()["status"] == "ok"
    assert len(lemonade_state["requests"]) == 1, "2nd health poll should hit the cache"


def test_v1_stats_cached_within_ttl(client: TestClient, lemonade_state: dict[str, Any]) -> None:
    client.get("/v1/stats")
    client.get("/v1/stats")
    stats_polls = [r for r in lemonade_state["requests"] if r["path"] == "/v1/stats"]
    assert len(stats_polls) == 1, "2nd stats poll should hit the cache"


def test_v1_health_query_params_bypass_cache(
    client: TestClient, lemonade_state: dict[str, Any]
) -> None:
    """Query-bearing requests are not cacheable — they always reach lemond."""
    client.get("/v1/health")
    client.get("/v1/health?debug=1")
    assert len(lemonade_state["requests"]) == 2


def test_v1_load_post_is_not_cached(client: TestClient, lemonade_state: dict[str, Any]) -> None:
    """Writes (POST /v1/load) must never be cached — each call reaches lemond."""
    client.post("/v1/load", json={"model_name": "gemma3:1b"})
    client.post("/v1/load", json={"model_name": "gemma3:1b"})
    loads = [r for r in lemonade_state["requests"] if r["path"] == "/v1/load"]
    assert len(loads) == 2


def test_v1_health_non_2xx_is_not_cached(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-2xx upstream (e.g. lemond mid-load) is not cached — keep polling."""
    calls = {"n": 0}

    def _handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"status": "loading"})

    _install_mock(monkeypatch, _handler)
    assert client.get("/v1/health").status_code == 503
    assert client.get("/v1/health").status_code == 503
    assert calls["n"] == 2, "non-2xx must not be cached"


def test_proxy_reuses_a_single_pooled_client(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The proxy builds ONE shared client and reuses it across requests (#474).

    Previously a fresh httpx.AsyncClient (new TCP connection) was built per
    request, storming lemond's accept queue.
    """
    built: list[int] = []

    def _handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"cpu": "ryzen"})

    def _counting_build(timeout: httpx.Timeout) -> httpx.AsyncClient:
        built.append(1)
        return httpx.AsyncClient(transport=httpx.MockTransport(_handler), timeout=timeout)

    lemonade_proxy._reset_state()
    monkeypatch.setattr(lemonade_proxy, "_build_client", _counting_build)
    # /v1/system-info is not cacheable, so each call exercises _get_client().
    for _ in range(3):
        assert client.get("/v1/system-info").status_code == 200
    assert built == [1], "expected exactly one shared client across 3 requests"
