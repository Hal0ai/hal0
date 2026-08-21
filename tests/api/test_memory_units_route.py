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


def test_degree_by_node_caused_by_weighs_same_as_causal():
    # wire-format alias (0.8.4 emits `caused_by`, not `causal`) must flow
    # through the same weighted-salience math, not floor to semantic's 1.0.
    graph = {
        "nodes": [{"data": {"id": "a"}}, {"data": {"id": "b"}}],
        "edges": [{"data": {"source": "a", "target": "b", "type": "caused_by"}}],
    }
    out = sg.degree_by_node(graph)
    assert out["a"] == (1, 4.0)
    assert out["b"] == (1, 4.0)


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


# ── A3b: truthful multi-type filtering ──────────────────────────────────────
#
# Upstream /memories/list's `type` is single-value exact-equality (0.8.4);
# `type=world,experience` silently returns an empty page, no 422.

TYPE_ROWS = [
    {"id": "t1", "fact_type": "world", "tags": [], "mentioned_at": "2026-01-01"},
    {"id": "t2", "fact_type": "experience", "tags": [], "mentioned_at": "2026-01-02"},
    {"id": "t3", "fact_type": "observation", "tags": [], "mentioned_at": "2026-01-03"},
]

TYPE_GRAPH = {
    "nodes": [
        {"data": {"id": "t1"}},
        {"data": {"id": "t2"}},
        {"data": {"id": "t3"}},
    ],
    "edges": [],
}


def test_units_single_type_still_forwarded_upstream():
    _reset_cache()
    app, rec = _app_with(rows=TYPE_ROWS, graph=TYPE_GRAPH)
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/units", params={"type": "world"})
        assert r.status_code == 200, r.text
    fwd = next(req for req in rec.requests if req["path"].endswith("/memories/list"))
    assert fwd["params"]["type"] == "world"


def test_units_multi_type_filters_hal0_side_not_forwarded():
    _reset_cache()
    app, rec = _app_with(rows=TYPE_ROWS, graph=TYPE_GRAPH)
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/units", params={"type": "world,experience"})
        assert r.status_code == 200, r.text
        body = r.json()
        ids = {u["id"] for u in body["items"]}
        assert ids == {"t1", "t2"}  # observation excluded
        assert body["total_matched"] == 2  # counts computed from the filtered set
    fwd = next(req for req in rec.requests if req["path"].endswith("/memories/list"))
    assert "type" not in fwd["params"]  # not forwarded — upstream can't OR it


def test_units_invalid_type_422():
    _reset_cache()
    app, _rec = _app_with(rows=TYPE_ROWS, graph=TYPE_GRAPH)
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/units", params={"type": "bogus"})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "memory.invalid_query"

        r2 = c.get("/api/memory/banks/shared/units", params={"type": "world,bogus"})
        assert r2.status_code == 422
        assert r2.json()["error"]["code"] == "memory.invalid_query"


# ── A3b fix round: unfiltered salience slab + truthful out-of-slab scoring ──
#
# The salience graph fetch must be unfiltered regardless of the listing's
# `type`/`q` — both because upstream's `type` param has the same
# single-value-exact-equality footgun as /memories/list, AND because a
# type-filtered graph query drops cross-type edges (upstream only returns an
# edge when both endpoints survive the filter), understating degree even for
# a single valid type.

CROSS_TYPE_GRAPH = {
    "nodes": TYPE_GRAPH["nodes"],
    "edges": [{"data": {"source": "t1", "target": "t2", "type": "semantic"}}],
}


def test_units_salience_graph_fetch_is_never_type_or_q_filtered():
    _reset_cache()
    app, rec = _app_with(rows=TYPE_ROWS, graph=CROSS_TYPE_GRAPH)
    with TestClient(app) as c:
        r = c.get(
            "/api/memory/banks/shared/units",
            params={"type": "world,experience", "q": "whatever"},
        )
        assert r.status_code == 200, r.text
    graph_fwd = next(req for req in rec.requests if req["path"].endswith("/graph"))
    assert "type" not in graph_fwd["params"]
    assert "q" not in graph_fwd["params"]


def test_units_salience_sees_cross_type_edges_despite_type_filter():
    _reset_cache()
    # t1 is `world`, t2 is `experience` — a type=world,experience listing
    # filter must not blind the t1<->t2 edge's contribution to salience.
    app, _rec = _app_with(rows=TYPE_ROWS, graph=CROSS_TYPE_GRAPH)
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/units", params={"type": "world,experience"})
        assert r.status_code == 200, r.text
        by_id = {u["id"]: u for u in r.json()["items"]}
        assert by_id["t1"]["salience"] == 1.0  # semantic floor weight
        assert by_id["t2"]["salience"] == 1.0
        assert by_id["t1"]["link_counts_by_type"] == {"semantic": 1}


def test_units_out_of_slab_rows_get_null_salience_and_sort_after_scored():
    _reset_cache()
    rows = [
        *TYPE_ROWS,
        {"id": "t4", "fact_type": "world", "tags": [], "mentioned_at": "2026-01-05"},
        {"id": "t5", "fact_type": "world", "tags": [], "mentioned_at": "2026-01-06"},
    ]
    # t4/t5 are NOT in the graph's node list — genuinely unknown connectivity,
    # not zero (the slab is a capped recent-units window, not the whole bank).
    app, _rec = _app_with(rows=rows, graph=CROSS_TYPE_GRAPH)
    with TestClient(app) as c:
        r = c.get("/api/memory/banks/shared/units", params={"sort": "salience"})
        assert r.status_code == 200, r.text
        items = r.json()["items"]
    by_id = {u["id"]: u for u in items}
    assert by_id["t4"]["salience"] is None
    assert by_id["t4"]["link_counts_by_type"] == {}
    assert by_id["t5"]["salience"] is None

    ordered_ids = [u["id"] for u in items]
    scored_positions = [ordered_ids.index(x) for x in ("t1", "t2", "t3")]
    unscored_positions = [ordered_ids.index(x) for x in ("t4", "t5")]
    assert max(scored_positions) < min(unscored_positions)  # nulls sort after scored
    assert ordered_ids.index("t5") < ordered_ids.index("t4")  # newer-first among nulls


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
