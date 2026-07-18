"""Tests for the /api/board/* router — src/hal0/api/routes/board.py.

hal0 now OWNS the board: the router reads/writes a local BoardStore instead of
proxying to Hermes. These assert the FROZEN wire contract (paths, payload
shapes, status codes) is preserved store-backed, audit rows land for every
mutation with the right action + actor, and — the R4 win — the board serves
with NO Hermes client wired.

Run targeted:
    PYTHONPATH=src .venv/bin/python -m pytest tests/board/test_board_routes.py -q
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.activity import AuditStore
from hal0.api.middleware import error_codes
from hal0.api.routes import board
from hal0.board.store import BoardStore


def _build_app(store: BoardStore, audit: AuditStore) -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(board.router, prefix="/api/board", tags=["board"])
    app.state.board_store = store
    app.state.hermes_kanban = None  # board owns state locally — no proxy needed
    app.state.audit = audit
    return app


@pytest.fixture
def store(tmp_path) -> BoardStore:
    s = BoardStore(tmp_path / "board.db")
    asyncio.run(s.ensure_initialized(None))
    return s


@pytest.fixture
def audit(tmp_path) -> AuditStore:
    store = AuditStore(tmp_path / "audit.db")
    store.init_schema()
    return store


@pytest.fixture
def app_client(store: BoardStore, audit: AuditStore) -> Iterator[tuple[FastAPI, TestClient]]:
    app = _build_app(store, audit)
    with TestClient(app) as c:
        yield app, c


def _audit_actions(audit: AuditStore) -> list[str]:
    return [r["action"] for r in audit.query(category="board")]


# ── reads: contract shapes, no Hermes ───────────────────────────────────────


def test_board_read_returns_all_visible_lanes(app_client: tuple) -> None:
    _app, client = app_client
    r = client.get("/api/board/board")
    assert r.status_code == 200
    lanes = r.json()["lanes"]
    assert list(lanes.keys()) == [
        "triage",
        "todo",
        "scheduled",
        "ready",
        "running",
        "blocked",
        "review",
        "done",
    ]


def test_include_archived_query(app_client: tuple) -> None:
    _app, client = app_client
    r = client.get("/api/board/board?include_archived=true")
    assert "archived" in r.json()["lanes"]


def test_boards_config_orchestration_reads(app_client: tuple) -> None:
    _app, client = app_client
    assert client.get("/api/board/boards").status_code == 200
    cfg = client.get("/api/board/config").json()
    assert cfg == {"tick_interval": 5, "failure_limit": 3, "claim_ttl": 600, "max_in_flight": 4}
    orch = client.get("/api/board/orchestration").json()
    assert set(orch) >= {"orchestrator_profile", "auto_decompose", "tick_interval"}


def test_stats_shape(app_client: tuple) -> None:
    _app, client = app_client
    stats = client.get("/api/board/stats").json()
    assert "total" in stats and "by_status" in stats


# ── mutations: forward to store + audit ─────────────────────────────────────


def test_create_task_returns_envelope_and_audits(app_client: tuple) -> None:
    app, client = app_client
    r = client.post("/api/board/tasks", json={"title": "hi", "status": "todo"})
    assert r.status_code == 200
    task = r.json()["task"]
    assert task["title"] == "hi"
    assert task["status"] == "todo"
    assert "board.task.create" in _audit_actions(app.state.audit)
    # audit target is the created id
    rows = app.state.audit.query(action="board.task.create")
    assert rows[0]["target"] == task["id"]


def test_update_task_moves_lane_and_audits(app_client: tuple, store: BoardStore) -> None:
    app, client = app_client
    tid = store.create_task({"title": "a", "status": "todo"})["task"]["id"]
    r = client.patch(f"/api/board/tasks/{tid}", json={"status": "done"})
    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert "board.task.update" in _audit_actions(app.state.audit)


def test_delete_task_and_audits(app_client: tuple, store: BoardStore) -> None:
    app, client = app_client
    tid = store.create_task({"title": "a"})["task"]["id"]
    r = client.delete(f"/api/board/tasks/{tid}")
    assert r.status_code == 200
    assert client.get(f"/api/board/tasks/{tid}").status_code == 404
    assert "board.task.delete" in _audit_actions(app.state.audit)


def test_comment_audits(app_client: tuple, store: BoardStore) -> None:
    app, client = app_client
    tid = store.create_task({"title": "a"})["task"]["id"]
    r = client.post(f"/api/board/tasks/{tid}/comments", json={"body": "lgtm"})
    assert r.status_code == 200
    assert r.json()["comment_count"] == 1
    assert "board.task.comment" in _audit_actions(app.state.audit)


def test_bulk_audits_and_updates(app_client: tuple, store: BoardStore) -> None:
    app, client = app_client
    a = store.create_task({"title": "a", "status": "todo"})["task"]["id"]
    b = store.create_task({"title": "b", "status": "todo"})["task"]["id"]
    r = client.post("/api/board/tasks/bulk", json={"ids": [a, b], "status": "ready"})
    assert r.status_code == 200
    assert r.json()["updated"] == 2
    assert "board.task.bulk" in _audit_actions(app.state.audit)


def test_links_add_remove_audit(app_client: tuple, store: BoardStore) -> None:
    app, client = app_client
    p = store.create_task({"title": "p"})["task"]["id"]
    c = store.create_task({"title": "c"})["task"]["id"]
    r = client.post("/api/board/links", json={"parent_id": p, "child_id": c})
    assert r.status_code == 200
    assert client.get(f"/api/board/tasks/{p}").json()["deps"]["children"] == [c]
    r = client.request("DELETE", f"/api/board/links?parent_id={p}&child_id={c}")
    assert r.status_code == 200
    assert client.get(f"/api/board/tasks/{p}").json()["deps"]["children"] == []
    actions = _audit_actions(app.state.audit)
    assert "board.link.add" in actions and "board.link.remove" in actions


def test_reassign_specify_decompose_reclaim_audit(app_client: tuple, store: BoardStore) -> None:
    app, client = app_client
    tid = store.create_task({"title": "a", "status": "running"})["task"]["id"]
    assert (
        client.post(f"/api/board/tasks/{tid}/reassign", json={"profile": "dev"}).status_code == 200
    )
    assert client.get(f"/api/board/tasks/{tid}").json()["assignee"] == "dev"
    assert client.post(f"/api/board/tasks/{tid}/specify", json={}).status_code == 200
    assert client.post(f"/api/board/tasks/{tid}/decompose", json={}).status_code == 200
    assert client.post(f"/api/board/tasks/{tid}/reclaim", json={}).status_code == 200
    assert client.get(f"/api/board/tasks/{tid}").json()["status"] == "ready"
    actions = _audit_actions(app.state.audit)
    for a in (
        "board.task.reassign",
        "board.task.specify",
        "board.task.decompose",
        "board.task.reclaim",
    ):
        assert a in actions


def test_dispatch_nudge_audits(app_client: tuple) -> None:
    app, client = app_client
    r = client.post("/api/board/dispatch?max=4")
    assert r.status_code == 200
    assert r.json() == {"dispatched": 0}
    assert "board.dispatch.nudge" in _audit_actions(app.state.audit)


def test_board_crud_and_switch_audit(app_client: tuple) -> None:
    app, client = app_client
    assert client.post("/api/board/boards", json={"slug": "proj-x", "name": "X"}).status_code == 200
    assert client.post("/api/board/boards/proj-x/switch", json={}).status_code == 200
    assert client.patch("/api/board/boards/proj-x", json={"name": "Y"}).status_code == 200
    assert client.delete("/api/board/boards/proj-x").status_code == 200
    actions = _audit_actions(app.state.audit)
    for a in (
        "board.board.create",
        "board.board.switch",
        "board.board.update",
        "board.board.delete",
    ):
        assert a in actions


def test_profile_and_orchestration_mutations_audit(app_client: tuple) -> None:
    app, client = app_client
    assert client.patch("/api/board/profiles/dev", json={"label": "Dev"}).status_code == 200
    r = client.put("/api/board/orchestration", json={"auto_decompose": True})
    assert r.status_code == 200
    assert r.json()["auto_decompose"] is True
    actions = _audit_actions(app.state.audit)
    assert "board.profile.update" in actions and "board.orchestration.update" in actions


# ── actor derivation (unchanged contract) ───────────────────────────────────


def test_actor_mcp_from_agent_header(app_client: tuple, store: BoardStore) -> None:
    app, client = app_client
    tid = store.create_task({"title": "a"})["task"]["id"]
    client.patch(
        f"/api/board/tasks/{tid}", json={"status": "done"}, headers={"X-hal0-Agent": "claude-dev"}
    )
    rows = app.state.audit.query(action="board.task.update")
    assert rows[0]["actor"] == "mcp:claude-dev"


def test_actor_dashboard_without_agent_header(app_client: tuple, store: BoardStore) -> None:
    app, client = app_client
    tid = store.create_task({"title": "a"})["task"]["id"]
    client.patch(f"/api/board/tasks/{tid}", json={"status": "ready"})
    rows = app.state.audit.query(action="board.task.update")
    assert rows[0]["actor"] == "dashboard"


def test_audit_row_after_is_set(app_client: tuple, store: BoardStore) -> None:
    app, client = app_client
    tid = store.create_task({"title": "a"})["task"]["id"]
    client.patch(f"/api/board/tasks/{tid}", json={"status": "done"})
    rows = app.state.audit.query(action="board.task.update")
    assert rows[0]["after"] is not None


# ── error mapping (JSON envelope) ───────────────────────────────────────────


def test_missing_task_returns_404(app_client: tuple) -> None:
    _app, client = app_client
    r = client.get("/api/board/tasks/t_ghost")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "board.task_not_found"


def test_invalid_status_returns_409(app_client: tuple, store: BoardStore) -> None:
    _app, client = app_client
    tid = store.create_task({"title": "a"})["task"]["id"]
    r = client.patch(f"/api/board/tasks/{tid}", json={"status": "nonsense"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "board.invalid_status"


def test_invalid_body_returns_400(app_client: tuple) -> None:
    _app, client = app_client
    r = client.post(
        "/api/board/tasks", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "board.invalid_body"


# ── board serves with no Hermes client (R4: core works without Hermes) ───────


def test_board_serves_without_hermes_client(tmp_path) -> None:
    """No app.state.board_store pre-injected and hermes_kanban=None: the router
    lazily builds the store and seeds a clean empty board."""
    import hal0.config.paths as paths_mod

    # Isolate the default db path so the lazily-built store lands in tmp.
    audit = AuditStore(tmp_path / "audit.db")
    audit.init_schema()
    app = FastAPI()
    error_codes.install(app)
    app.include_router(board.router, prefix="/api/board")
    app.state.audit = audit
    app.state.hermes_kanban = None
    orig = paths_mod.db_path
    paths_mod.db_path = lambda: tmp_path / "hal0.db"  # type: ignore[assignment]
    try:
        with TestClient(app) as client:
            r = client.get("/api/board/board")
            assert r.status_code == 200
            assert "todo" in r.json()["lanes"]
    finally:
        paths_mod.db_path = orig  # type: ignore[assignment]
