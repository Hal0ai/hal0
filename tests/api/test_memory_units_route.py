"""Tests for `degree_by_node` (pure) and the composed
`GET /api/memory/banks/{bank_id}/units` route (memory_v2 ask #2).

Same MockTransport harness as test_memory_subgraph.py / test_memory_admin_routes.py.

Run targeted:
    .venv/bin/python -m pytest tests/api/test_memory_units_route.py -q
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

# ── Step 1: degree_by_node (pure) ───────────────────────────────────────────


def test_degree_by_node_weighted_by_type():
    graph = {
        "nodes": [{"data": {"id": "a"}}, {"data": {"id": "b"}}],
        "edges": [{"data": {"source": "a", "target": "b", "type": "causal"}}],
    }
    out = sg.degree_by_node(graph)
    assert out["a"] == (1, 4.0)  # causal type_weight floor
    assert out["b"] == (1, 4.0)


def test_degree_by_node_isolated_node_absent():
    graph = {"nodes": [{"data": {"id": "iso"}}], "edges": []}
    out = sg.degree_by_node(graph)
    assert out == {}  # adjacency only carries nodes with edges


# ── Step 2: composed route ───────────────────────────────────────────────────

ROWS = [
    {
        "id": "u1",
        "fact_type": "episodic",
        "tags": ["infra", "ops"],
        "mentioned_at": "2026-01-01",
        "entities": "alice,bob",
    },
    {
        "id": "u2",
        "fact_type": "semantic",
        "tags": ["infra"],
        "date": "2026-02-01",
    },
    {
        "id": "u3",
        "fact_type": "world",
        "tags": ["misc"],
        "mentioned_at": "2026-03-01",
    },
    {
        "id": "u4",
        "fact_type": "episodic",
        "tags": [],
    },
]

GRAPH = {
    "nodes": [
        {"data": {"id": "u1"}},
        {"data": {"id": "u2"}},
        {"data": {"id": "u3"}},
        {"data": {"id": "u4"}},
        {"data": {"id": "ent:alice"}},
    ],
    "edges": [
        {"data": {"source": "u1", "target": "u2", "type": "temporal"}},
        {"data": {"source": "u1", "target": "u3", "type": "semantic"}},
        {"data": {"source": "u1", "target": "ent:alice", "type": "entity"}},
        {"data": {"source": "u2", "target": "u3", "type": "semantic"}},
    ],
}


def _client_for(recorder: Any) -> HindsightRestClient:
    transport = httpx.MockTransport(recorder.handler)
    http = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9177")
    return HindsightRestClient(http_client=http, api_key="hal0-local-noauth")


def _reset_cache() -> None:
    from hal0.api.routes import memory_admin

    memory_admin._GRAPH_CACHE = sg.GraphCache()


def _app_with(rows: list[dict[str, Any]] = ROWS, graph: dict[str, Any] = GRAPH):
    rec = _Recorder()
    rec.respond("GET", "/v1/default/banks/shared/memories/list", 200, {"items": rows})
    rec.respond("GET", "/v1/default/banks/shared/graph", 200, graph)
    app = _build_app(_HindsightStubProvider(_client_for(rec)))
    return app, rec


def test_units_page_through_and_last_page_next_offset_null():
    _reset_cache()
    app, _rec = _app_with()
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/units", params={"limit": 2, "offset": 0})
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["items"]) == 2
        assert body["total_matched"] == 4
        assert body["next_offset"] == 2

        r2 = c.get("/api/memory/banks/shared/units", params={"limit": 2, "offset": 2})
        body2 = r2.json()
        assert len(body2["items"]) == 2
        assert body2["next_offset"] is None


def test_units_tags_filter_is_hal0_side():
    _reset_cache()
    app, rec = _app_with()
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/units", params={"tags": "infra"})
        assert r.status_code == 200, r.text
        ids = {u["id"] for u in r.json()["items"]}
        assert ids == {"u1", "u2"}
    # tags is never sent upstream — it's a hal0-side filter over the full slab
    fwd = next(req for req in rec.requests if req["path"].endswith("/memories/list"))
    assert "tags" not in fwd["params"]


def test_units_document_id_and_state_forward_upstream():
    _reset_cache()
    app, rec = _app_with()
    with TestClient(app) as c:
        r = c.get(
            "/api/memory/banks/shared/units",
            params={"document_id": "doc-9", "state": "invalidated"},
        )
        assert r.status_code == 200, r.text
    fwd = next(req for req in rec.requests if req["path"].endswith("/memories/list"))
    assert fwd["params"]["document_id"] == "doc-9"
    assert fwd["params"]["state"] == "invalidated"


def test_units_time_window_drops_undated_row_only_when_set():
    _reset_cache()
    app, _rec = _app_with()
    with TestClient(app) as c:
        r_all = c.get("/api/memory/banks/shared/units")
        ids_all = {u["id"] for u in r_all.json()["items"]}
        assert "u4" in ids_all  # undated row present with no window

        r_windowed = c.get(
            "/api/memory/banks/shared/units",
            params={"from": "2026-01-15", "to": "2026-03-31"},
        )
        ids_windowed = {u["id"] for u in r_windowed.json()["items"]}
        assert "u4" not in ids_windowed  # undated row drops once a window is given
        assert ids_windowed == {"u2", "u3"}  # u1 before `from`


def test_units_sort_salience_orders_by_graph_degree_math():
    _reset_cache()
    app, _rec = _app_with()
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/units", params={"sort": "salience"})
        assert r.status_code == 200, r.text
        ordered_ids = [u["id"] for u in r.json()["items"]]
        assert ordered_ids == ["u1", "u2", "u3", "u4"]  # by descending salience


def test_units_carry_salience_and_link_counts_by_type():
    _reset_cache()
    app, _rec = _app_with()
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/units")
        assert r.status_code == 200, r.text
        by_id = {u["id"]: u for u in r.json()["items"]}
        u1 = by_id["u1"]
        assert u1["link_counts_by_type"] == {"temporal": 1, "semantic": 1, "entity": 1}
        assert u1["salience"] == 5.0  # 3.0 (temporal) + 1.0 (semantic) + 1.0 (entity floor)
        assert by_id["u4"]["salience"] == 0.0
        assert by_id["u4"]["link_counts_by_type"] == {}


def test_units_sort_bogus_422():
    _reset_cache()
    app, _rec = _app_with()
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/units", params={"sort": "bogus"})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "memory.invalid_query"


def test_units_transport_failure_503():
    _reset_cache()
    rec = _Recorder()
    rec.fail_connect = True
    app = _build_app(_HindsightStubProvider(_client_for(rec)))
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/units")
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "memory.engine_unreachable"


# ── PR #1987 review B3: limit=0 / limit<0 pagination clamp ─────────────────────


def test_units_limit_zero_does_not_loop_forever():
    """limit=0 used to make next_offset == offset, so a client following

    next_offset would loop forever. max(1, ...) means limit=0 behaves like
    limit=1: a real page is returned and next_offset advances past offset.
    """
    _reset_cache()
    app, _rec = _app_with()
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/units", params={"limit": 0, "offset": 0})
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["items"]) == 1
        assert body["next_offset"] != 0
        assert body["next_offset"] == 1


def test_units_negative_limit_does_not_drop_last_row():
    """limit=-1 used to slice kept[offset : offset - 1], silently dropping

    the last matching row via negative-index slicing. Clamped to 1.
    """
    _reset_cache()
    app, _rec = _app_with()
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/units", params={"limit": -1, "offset": 0})
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["items"]) == 1


def test_units_limit_clamped_to_200_ceiling():
    _reset_cache()
    app, _rec = _app_with()
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/units", params={"limit": 99999})
        assert r.status_code == 200, r.text
        # only 4 rows exist upstream, but this pins the clamp doesn't reject
        # the request outright — total_matched proves the full slab was kept.
        assert r.json()["total_matched"] == 4


# ── PR #1987 review B2: truncated slab signal ───────────────────────────────────


def test_units_truncated_false_under_the_2000_row_slab():
    _reset_cache()
    app, _rec = _app_with()
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/units")
        assert r.status_code == 200, r.text
        assert r.json()["truncated"] is False


def test_units_truncated_true_at_the_2000_row_slab():
    _reset_cache()
    big_rows = [{"id": f"u{i}", "fact_type": "episodic", "date": "2026-01-01"} for i in range(2000)]
    app, _rec = _app_with(rows=big_rows, graph={"nodes": [], "edges": []})
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/units", params={"limit": 5})
        assert r.status_code == 200, r.text
        assert r.json()["truncated"] is True


# ── PR #1987 review M4: malformed int query params 422, not 500 ────────────────


def test_units_limit_abc_422_not_500():
    _reset_cache()
    app, _rec = _app_with()
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/units", params={"limit": "abc"})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "memory.invalid_query"


def test_units_offset_abc_422_not_500():
    _reset_cache()
    app, _rec = _app_with()
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/units", params={"offset": "abc"})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "memory.invalid_query"
