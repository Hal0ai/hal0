"""Tests for the /api/board/* proxy router — src/hal0/api/routes/board.py.

Stubs Hermes kanban with httpx.MockTransport behind the real HermesKanbanClient,
and a real AuditStore so audit rows can be queried. Asserts the FROZEN SPEC §4
contract: verbatim method/path/query/body forwarding, ?board= threading, audit
rows for every mutation with the right action + actor, and error mapping.

Run targeted:
    PYTHONPATH=src .venv/bin/python -m pytest tests/board/test_board_routes.py -q
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.activity import AuditStore
from hal0.api.middleware import error_codes
from hal0.api.routes import board
from hal0.board import KANBAN_BASE_PATH, HermesKanbanClient

P = KANBAN_BASE_PATH  # "/api/plugins/kanban"


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses: dict[tuple[str, str], httpx.Response] = {}
        self.fail_connect = False

    def respond(self, method: str, path: str, status: int, body: Any) -> None:
        self.responses[(method, path)] = httpx.Response(status, json=body)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail_connect:
            raise httpx.ConnectError("connection refused", request=request)
        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "params": dict(request.url.params),
                "body": request.content.decode() if request.content else "",
            }
        )
        key = (request.method, request.url.path)
        if key in self.responses:
            return self.responses[key]
        return httpx.Response(200, json={"ok": True, "echo": request.url.path})


def _build_app(recorder: _Recorder, audit: AuditStore, *, no_client: bool = False) -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(board.router, prefix="/api/board", tags=["board"])
    if no_client:
        app.state.hermes_kanban = None
    else:
        transport = httpx.MockTransport(recorder.handler)
        http = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9119")
        app.state.hermes_kanban = HermesKanbanClient(
            http_client=http, session_token_resolver=lambda: "TOK"
        )
    app.state.audit = audit
    return app


@pytest.fixture
def recorder() -> _Recorder:
    return _Recorder()


@pytest.fixture
def audit(tmp_path) -> AuditStore:
    store = AuditStore(tmp_path / "audit.db")
    store.init_schema()
    return store


@pytest.fixture
def app_client(recorder: _Recorder, audit: AuditStore) -> Iterator[tuple[FastAPI, TestClient]]:
    app = _build_app(recorder, audit)
    with TestClient(app) as c:
        yield app, c


def _last(recorder: _Recorder) -> dict[str, Any]:
    return recorder.requests[-1]


# ── reads forward verbatim ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "hal0_path,upstream_path",
    [
        ("/api/board/board", f"{P}/board"),
        ("/api/board/tasks/t1", f"{P}/tasks/t1"),
        ("/api/board/tasks/t1/log", f"{P}/tasks/t1/log"),
        ("/api/board/boards", f"{P}/boards"),
        ("/api/board/profiles", f"{P}/profiles"),
        ("/api/board/assignees", f"{P}/assignees"),
        ("/api/board/stats", f"{P}/stats"),
        ("/api/board/diagnostics", f"{P}/diagnostics"),
        ("/api/board/workers/active", f"{P}/workers/active"),
        ("/api/board/runs/r1", f"{P}/runs/r1"),
        ("/api/board/config", f"{P}/config"),
        ("/api/board/orchestration", f"{P}/orchestration"),
    ],
)
def test_reads_forward_path(
    app_client: tuple, recorder: _Recorder, hal0_path: str, upstream_path: str
) -> None:
    _app, client = app_client
    r = client.get(hal0_path)
    assert r.status_code == 200
    assert _last(recorder)["method"] == "GET"
    assert _last(recorder)["path"] == upstream_path


def test_board_query_threads(app_client: tuple, recorder: _Recorder) -> None:
    _app, client = app_client
    client.get("/api/board/board?board=alpha&include_archived=true")
    fwd = _last(recorder)
    assert fwd["params"]["board"] == "alpha"
    assert fwd["params"]["include_archived"] == "true"


# ── mutations: forward + audit ──────────────────────────────────────────────


def _audit_actions(audit: AuditStore) -> list[str]:
    return [r["action"] for r in audit.query(category="board")]


def test_create_task_forwards_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    recorder.respond("POST", f"{P}/tasks", 200, {"task": {"id": "t9"}})
    r = client.post("/api/board/tasks", json={"title": "hi"})
    assert r.status_code == 200
    assert _last(recorder)["method"] == "POST"
    assert _last(recorder)["path"] == f"{P}/tasks"
    assert '"title"' in _last(recorder)["body"]
    assert "board.task.create" in _audit_actions(app.state.audit)


def test_update_task_forwards_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.patch("/api/board/tasks/t1", json={"status": "done"})
    assert r.status_code == 200
    assert _last(recorder)["method"] == "PATCH"
    assert _last(recorder)["path"] == f"{P}/tasks/t1"
    assert '"status"' in _last(recorder)["body"]
    assert "board.task.update" in _audit_actions(app.state.audit)


def test_delete_task_forwards_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.delete("/api/board/tasks/t1")
    assert r.status_code == 200
    assert _last(recorder)["method"] == "DELETE"
    assert _last(recorder)["path"] == f"{P}/tasks/t1"
    assert "board.task.delete" in _audit_actions(app.state.audit)


def test_comment_forwards_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.post("/api/board/tasks/t1/comments", json={"body": "lgtm"})
    assert r.status_code == 200
    assert _last(recorder)["path"] == f"{P}/tasks/t1/comments"
    assert "board.task.comment" in _audit_actions(app.state.audit)


def test_bulk_forwards_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.post("/api/board/tasks/bulk", json={"ids": ["a", "b"], "status": "todo"})
    assert r.status_code == 200
    assert _last(recorder)["path"] == f"{P}/tasks/bulk"
    assert "board.task.bulk" in _audit_actions(app.state.audit)


def test_reassign_forwards_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.post("/api/board/tasks/t1/reassign", json={"profile": "dev"})
    assert r.status_code == 200
    assert _last(recorder)["path"] == f"{P}/tasks/t1/reassign"
    assert "board.task.reassign" in _audit_actions(app.state.audit)


def test_specify_forwards_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.post("/api/board/tasks/t1/specify", json={})
    assert r.status_code == 200
    assert _last(recorder)["path"] == f"{P}/tasks/t1/specify"
    assert "board.task.specify" in _audit_actions(app.state.audit)


def test_decompose_forwards_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.post("/api/board/tasks/t1/decompose", json={})
    assert r.status_code == 200
    assert _last(recorder)["path"] == f"{P}/tasks/t1/decompose"
    assert "board.task.decompose" in _audit_actions(app.state.audit)


def test_reclaim_forwards_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.post("/api/board/tasks/t1/reclaim", json={})
    assert r.status_code == 200
    assert _last(recorder)["path"] == f"{P}/tasks/t1/reclaim"
    assert "board.task.reclaim" in _audit_actions(app.state.audit)


def test_add_link_forwards_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.post("/api/board/links", json={"parent_id": "p", "child_id": "c"})
    assert r.status_code == 200
    assert _last(recorder)["method"] == "POST"
    assert _last(recorder)["path"] == f"{P}/links"
    assert "board.link.add" in _audit_actions(app.state.audit)


def test_remove_link_forwards_query_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.request("DELETE", "/api/board/links?parent_id=p&child_id=c")
    assert r.status_code == 200
    fwd = _last(recorder)
    assert fwd["method"] == "DELETE"
    assert fwd["path"] == f"{P}/links"
    # parent_id/child_id ride as QUERY params, not body.
    assert fwd["params"]["parent_id"] == "p"
    assert fwd["params"]["child_id"] == "c"
    assert fwd["body"] == ""
    assert "board.link.remove" in _audit_actions(app.state.audit)


def test_dispatch_nudge_forwards_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.post("/api/board/dispatch?max=4")
    assert r.status_code == 200
    fwd = _last(recorder)
    assert fwd["path"] == f"{P}/dispatch"
    assert fwd["params"]["max"] == "4"
    assert "board.dispatch.nudge" in _audit_actions(app.state.audit)


def test_create_board_forwards_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.post("/api/board/boards", json={"slug": "proj-x"})
    assert r.status_code == 200
    assert _last(recorder)["path"] == f"{P}/boards"
    assert "board.board.create" in _audit_actions(app.state.audit)


def test_update_board_forwards_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.patch("/api/board/boards/proj-x", json={"name": "X"})
    assert r.status_code == 200
    assert _last(recorder)["path"] == f"{P}/boards/proj-x"
    assert "board.board.update" in _audit_actions(app.state.audit)


def test_delete_board_forwards_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.delete("/api/board/boards/proj-x?delete=true")
    assert r.status_code == 200
    assert _last(recorder)["path"] == f"{P}/boards/proj-x"
    assert _last(recorder)["params"]["delete"] == "true"
    assert "board.board.delete" in _audit_actions(app.state.audit)


def test_switch_board_forwards_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.post("/api/board/boards/proj-x/switch", json={})
    assert r.status_code == 200
    assert _last(recorder)["path"] == f"{P}/boards/proj-x/switch"
    assert "board.board.switch" in _audit_actions(app.state.audit)


def test_update_profile_forwards_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.patch("/api/board/profiles/dev", json={"description": "x"})
    assert r.status_code == 200
    assert _last(recorder)["path"] == f"{P}/profiles/dev"
    assert "board.profile.update" in _audit_actions(app.state.audit)


def test_update_orchestration_forwards_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.put("/api/board/orchestration", json={"auto_decompose": False})
    assert r.status_code == 200
    assert _last(recorder)["method"] == "PUT"
    assert _last(recorder)["path"] == f"{P}/orchestration"
    assert "board.orchestration.update" in _audit_actions(app.state.audit)


# ── actor derivation ────────────────────────────────────────────────────────


def test_actor_mcp_from_agent_header(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    client.patch(
        "/api/board/tasks/t1", json={"status": "done"}, headers={"X-hal0-Agent": "claude-dev"}
    )
    rows = app.state.audit.query(action="board.task.update")
    assert rows[0]["actor"] == "mcp:claude-dev"


def test_actor_dashboard_without_agent_header(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    client.patch("/api/board/tasks/t2", json={"status": "ready"})
    rows = app.state.audit.query(action="board.task.update")
    assert rows[0]["actor"] == "dashboard"


def test_audit_row_after_is_set(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    recorder.respond("PATCH", f"{P}/tasks/t1", 200, {"id": "t1", "status": "done"})
    client.patch("/api/board/tasks/t1", json={"status": "done"})
    rows = app.state.audit.query(action="board.task.update")
    assert rows[0]["after"] is not None


# ── error mapping through the route ─────────────────────────────────────────


def test_upstream_404_passes_through(app_client: tuple, recorder: _Recorder) -> None:
    _app, client = app_client
    recorder.respond("GET", f"{P}/tasks/ghost", 404, {"detail": "no task"})
    r = client.get("/api/board/tasks/ghost")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "board.upstream_error"


def test_upstream_500_maps_to_502(app_client: tuple, recorder: _Recorder) -> None:
    _app, client = app_client
    recorder.respond("GET", f"{P}/board", 500, {"detail": "boom"})
    r = client.get("/api/board/board")
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "board.upstream_error"


def test_connect_error_returns_503(app_client: tuple, recorder: _Recorder) -> None:
    _app, client = app_client
    recorder.fail_connect = True
    r = client.get("/api/board/board")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "board.unreachable"


def test_no_client_returns_503(recorder: _Recorder, audit: AuditStore) -> None:
    app = _build_app(recorder, audit, no_client=True)
    with TestClient(app) as c:
        r = c.get("/api/board/board")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "board.unreachable"
