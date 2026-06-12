"""Tests for the /v1 surface after the catch-all proxy removal (epic #687).

The ``/v1/{path:path}`` reverse-proxy catch-all is GONE. There is no
fall-through to any external gateway:

  * Un-routed /v1 paths (/v1/health, /v1/stats, /v1/system-info,
    /v1/load, …) are plain 404/405s from local routing (the dashboard
    SPA mount at "/" answers 404 for unknown GETs and 405 for non-GET
    methods) — nothing forwards them anywhere.
  * A model that no configured upstream serves surfaces the dispatcher's
    typed ``dispatch.no_route`` envelope (404) — NoRouteFound propagates
    straight out of ``_dispatch_and_forward`` with no proxy delegate.
  * Dispatcher-owned paths (/v1/models, /v1/chat/completions) keep their
    aggregator / dispatch semantics.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

# ── dispatcher-owned routes still answer ────────────────────────────────────


def test_v1_models_handled_by_aggregator(client: TestClient) -> None:
    """GET /v1/models stays on the aggregator and returns the OpenAI shape."""
    r = client.get("/v1/models")
    assert r.status_code == 200
    assert r.json() == {"object": "list", "data": []}


# ── un-routed /v1 paths: plain 404, no fall-through ─────────────────────────


def test_v1_health_is_unrouted_404(client: TestClient) -> None:
    """GET /v1/health was a proxy-only path — it now 404s locally."""
    r = client.get("/v1/health")
    assert r.status_code == 404


def test_v1_stats_is_unrouted_404(client: TestClient) -> None:
    assert client.get("/v1/stats").status_code == 404


def test_v1_system_info_is_unrouted_404(client: TestClient) -> None:
    assert client.get("/v1/system-info").status_code == 404


def test_v1_load_post_is_unrouted(client: TestClient) -> None:
    """POST /v1/load was the upstream admin surface — gone with the proxy.

    The dashboard SPA mount at "/" matches the path but only serves GET,
    so the local answer is 404/405 — never a forwarded response.
    """
    r = client.post("/v1/load", json={"model_name": "gemma3:1b"})
    assert r.status_code in (404, 405)


def test_v1_arbitrary_unknown_path_404(client: TestClient) -> None:
    r = client.get("/v1/this-is-not-real")
    assert r.status_code == 404


# ── wrong method on a routed path: local 404/405, never proxied ─────────────


def test_v1_chat_completions_get_is_not_routed(client: TestClient) -> None:
    """GET on the POST-only chat route is a local routing miss, not a
    proxy hop (the SPA static mount answers 404 for the unknown GET)."""
    assert client.get("/v1/chat/completions").status_code in (404, 405)


def test_v1_embeddings_get_is_not_routed(client: TestClient) -> None:
    assert client.get("/v1/embeddings").status_code in (404, 405)


# ── model nothing serves: typed NoRouteFound envelope ───────────────────────


def test_v1_chat_completions_no_route_returns_typed_404(client: TestClient) -> None:
    """POST /v1/chat/completions for a model no upstream serves → 404
    ``dispatch.no_route``.

    Pre-#687 the dispatcher caught NoRouteFound and delegated to the
    catch-all proxy. The proxy is gone: the typed envelope surfaces
    verbatim, carrying the requested model in details.
    """
    r = client.post(
        "/v1/chat/completions",
        json={"model": "model-nobody-serves", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 404, r.text
    body = r.json()
    assert body["error"]["code"] == "dispatch.no_route"
    assert body["error"]["details"]["model"] == "model-nobody-serves"


def test_v1_completions_no_route_returns_typed_404(client: TestClient) -> None:
    r = client.post("/v1/completions", json={"model": "model-nobody-serves", "prompt": "hi"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "dispatch.no_route"


def test_v1_embeddings_no_route_returns_typed_404(client: TestClient) -> None:
    r = client.post("/v1/embeddings", json={"model": "model-nobody-serves", "input": "x"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "dispatch.no_route"


def test_v1_rerankings_no_route_returns_typed_404(client: TestClient) -> None:
    r = client.post(
        "/v1/rerankings",
        json={"model": "model-nobody-serves", "query": "q", "documents": ["d"]},
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "dispatch.no_route"
