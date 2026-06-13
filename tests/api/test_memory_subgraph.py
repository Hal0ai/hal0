"""Tests for the composed memory subgraph endpoint + its pure graph-math.

Two layers:
* pure helpers in ``_memory_subgraph`` (ranking / induce / ego BFS / TTL cache)
* the composed ``GET /api/memory/banks/{bank}/graph/subgraph`` route, stubbed
  against Hindsight with the same MockTransport harness as the admin routes.

Run targeted:
    .venv/bin/python -m pytest tests/api/test_memory_subgraph.py -q
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi.testclient import TestClient

from hal0.api.routes import _memory_subgraph as sg
from hal0.memory.hindsight_client import HindsightRestClient
from tests.api.test_memory_admin_routes import (
    _build_app,
    _HindsightStubProvider,
    _Recorder,
)

GRAPH = {
    "nodes": [{"data": {"id": n}} for n in ("a", "b", "c", "d", "iso")],
    "edges": [
        {"data": {"source": "a", "target": "b", "type": "causal", "weight": 1}},
        {"data": {"source": "a", "target": "c", "type": "semantic", "weight": 1}},
        {"data": {"source": "a", "target": "d", "type": "semantic", "weight": 1}},
        {"data": {"source": "b", "target": "c", "type": "temporal", "weight": 1}},
    ],
}


# ── Task 1: ranking + induce ─────────────────────────────────────────────────


def test_type_weight_orders_causal_above_semantic():
    assert (
        sg.type_weight("causal")
        > sg.type_weight("temporal")
        > sg.type_weight("cooccurrence")
        > sg.type_weight("semantic")
    )
    assert sg.type_weight("mystery") == sg.type_weight("semantic")  # default floor


def test_rank_by_degree_weights_salient_edges():
    ranked = sg.rank_by_degree(GRAPH)  # -> list[node_id] high→low
    assert ranked[0] == "a"  # degree 3, includes a causal edge
    assert "iso" in ranked and ranked[-1] == "iso"  # isolated sorts last


def test_rank_by_recency_tolerant_timestamp_missing_last():
    g = {
        "nodes": [
            {"data": {"id": "old", "created_at": "2026-01-01T00:00:00Z"}},
            {"data": {"id": "new", "t": "2026-06-13T00:00:00Z"}},
            {"data": {"id": "none"}},
        ],
        "edges": [],
    }
    ranked = sg.rank_by_recency(g)
    assert ranked[0] == "new"
    assert ranked.index("old") < ranked.index("none")  # missing ts sorts last


def test_induce_subgraph_keeps_only_internal_edges_and_verbatim_data():
    out = sg.induce_subgraph(GRAPH, {"a", "b"})
    ids = {n["data"]["id"] for n in out["nodes"]}
    assert ids == {"a", "b"}
    assert len(out["edges"]) == 1  # only a-b (causal); a-c/a-d/b-c dropped
    assert out["edges"][0]["data"]["type"] == "causal"  # verbatim passthrough


# ── Task 2: ego BFS + TTL cache ──────────────────────────────────────────────


def test_ego_bfs_depth_limits_and_center():
    reach1 = sg.ego_bfs(GRAPH, "a", depth=1, limit=100)
    assert reach1 == {"a", "b", "c", "d"}  # ring-1 of a
    reach2 = sg.ego_bfs(GRAPH, "b", depth=2, limit=100)
    assert "d" in reach2  # b->a->d at depth 2
    assert sg.ego_bfs(GRAPH, "iso", depth=2, limit=100) == {"iso"}  # isolated
    assert sg.ego_bfs(GRAPH, "a", depth=1, limit=2)  # capped (center + ≤cap)


def test_graph_cache_ttl():
    clock = {"now": 1000.0}
    cache = sg.GraphCache(ttl=45.0, clock=lambda: clock["now"])
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return {"nodes": [], "edges": []}

    cache.get_or_fetch("shared:memories::", fetch)
    cache.get_or_fetch("shared:memories::", fetch)
    assert calls["n"] == 1  # served from cache
    clock["now"] += 46
    cache.get_or_fetch("shared:memories::", fetch)
    assert calls["n"] == 2  # expired → re-fetch


def test_graph_cache_peek_put():
    clock = {"now": 0.0}
    cache = sg.GraphCache(ttl=45.0, clock=lambda: clock["now"])
    assert cache.peek("k") is None
    cache.put("k", {"nodes": [], "edges": []})
    assert cache.peek("k") == {"nodes": [], "edges": []}
    clock["now"] += 46
    assert cache.peek("k") is None  # expired


# ── Task 3: composed route ───────────────────────────────────────────────────

BIG = {
    "nodes": [{"data": {"id": f"n{i}", "t": f"2026-06-{(i % 28) + 1:02d}"}} for i in range(50)],
    "edges": [
        {"data": {"source": "n0", "target": f"n{i}", "type": "semantic"}} for i in range(1, 40)
    ],
}


def _client_for(recorder: Any) -> HindsightRestClient:
    transport = httpx.MockTransport(recorder.handler)
    http = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177")
    return HindsightRestClient(http_client=http, api_key="hal0-local-noauth")


def _reset_cache() -> None:
    from hal0.api.routes import memory_admin

    memory_admin._GRAPH_CACHE = sg.GraphCache()


def test_subgraph_top_degree_bounds_and_counts():
    _reset_cache()
    rec = _Recorder()
    rec.respond("GET", "/v1/default/banks/shared/graph", 200, BIG)
    app = _build_app(_HindsightStubProvider(_client_for(rec)))
    with TestClient(app) as c:
        r = c.get(
            "/api/memory/banks/shared/graph/subgraph",
            params={"mode": "top", "by": "degree", "top_k": 10},
        )
        assert r.status_code == 200, r.text
        b = r.json()
        assert len(b["nodes"]) <= 10
        assert b["total_units"] == 50 and b["returned_nodes"] == len(b["nodes"])
        assert b["truncated"] is True
        assert any(n["data"]["id"] == "n0" for n in b["nodes"])  # hub kept


def test_subgraph_ego_requires_node():
    _reset_cache()
    rec = _Recorder()
    rec.respond("GET", "/v1/default/banks/shared/graph", 200, BIG)
    app = _build_app(_HindsightStubProvider(_client_for(rec)))
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/graph/subgraph", params={"mode": "ego"})
        assert r.status_code == 422
        r2 = c.get(
            "/api/memory/banks/shared/graph/subgraph",
            params={"mode": "ego", "node": "nope"},
        )
        assert r2.status_code == 404


def test_subgraph_bad_kind_and_mode_422():
    _reset_cache()
    rec = _Recorder()
    rec.respond("GET", "/v1/default/banks/shared/graph", 200, BIG)
    app = _build_app(_HindsightStubProvider(_client_for(rec)))
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/graph/subgraph", params={"kind": "bogus"})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "memory.invalid_query"
        r2 = c.get("/api/memory/banks/shared/graph/subgraph", params={"mode": "bogus"})
        assert r2.status_code == 422


def test_subgraph_ego_returns_connected_slice():
    _reset_cache()
    rec = _Recorder()
    rec.respond("GET", "/v1/default/banks/shared/graph", 200, BIG)
    app = _build_app(_HindsightStubProvider(_client_for(rec)))
    with TestClient(app) as c:
        r = c.get(
            "/api/memory/banks/shared/graph/subgraph",
            params={"mode": "ego", "node": "n0", "depth": 1},
        )
        assert r.status_code == 200, r.text
        b = r.json()
        ids = {n["data"]["id"] for n in b["nodes"]}
        assert "n0" in ids
        assert b["center"] == "n0"
        assert b["mode"] == "ego"


def test_subgraph_entities_kind_hits_entities_graph():
    _reset_cache()
    rec = _Recorder()
    rec.respond(
        "GET",
        "/v1/default/banks/shared/entities/graph",
        200,
        {"nodes": [{"data": {"id": "e1"}}], "edges": []},
    )
    app = _build_app(_HindsightStubProvider(_client_for(rec)))
    with TestClient(app) as c:
        r = c.get(
            "/api/memory/banks/shared/graph/subgraph",
            params={"kind": "entities", "mode": "top"},
        )
        assert r.status_code == 200
        b = r.json()
        assert "total_entities" in b
        assert any(req["path"].endswith("/entities/graph") for req in rec.requests)


def test_subgraph_caches_upstream_fetch():
    _reset_cache()
    rec = _Recorder()
    rec.respond("GET", "/v1/default/banks/shared/graph", 200, BIG)
    app = _build_app(_HindsightStubProvider(_client_for(rec)))
    with TestClient(app) as c:
        c.get("/api/memory/banks/shared/graph/subgraph", params={"mode": "top"})
        c.get("/api/memory/banks/shared/graph/subgraph", params={"mode": "top"})
    graph_pulls = [r for r in rec.requests if r["path"] == "/v1/default/banks/shared/graph"]
    assert len(graph_pulls) == 1  # second served from per-bank TTL cache
