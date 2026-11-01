"""Tests for the /api/board/* router — src/hal0/api/routes/board.py.

The routes are an audited proxy to the Hermes kanban plugin through the
``BoardClient`` facade. A recorder ``httpx.MockTransport`` sits behind a real
``HermesKanbanClient``; a real ``AuditStore`` records mutations. Asserts the
recorded Hermes method/path/query/body, the audit rows, and error mapping.

Run targeted:
    PYTHONPATH=src .venv/bin/python -m pytest tests/board/test_board_routes.py -q
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from hal0.activity import AuditStore
from hal0.api.middleware import error_codes
from hal0.api.routes import board
from hal0.board import KANBAN_BASE_PATH, BoardNoToken, HermesKanbanClient
from hal0.board.client import BoardClient

P = KANBAN_BASE_PATH


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
                "body": json.loads(request.content) if request.content else None,
                "agent": request.headers.get("x-hal0-agent"),
            }
        )
        resp = self.responses.get((request.method, request.url.path))
        return resp or httpx.Response(200, json={"ok": True})


def _app(audit: AuditStore) -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(board.router, prefix="/api/board", tags=["board"])
    app.state.audit = audit
    app.state.board_legacy_done = True
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
    app = _app(audit)
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(recorder.handler), base_url="http://hermes.test"
    )
    kanban = HermesKanbanClient(
        base_url="http://hermes.test", token_resolver=lambda: "tok", http_client=http
    )
    app.state.board_client = BoardClient(kanban)
    app.state.board_installed = True
    with TestClient(app) as c:
        yield app, c


def _last(recorder: _Recorder) -> dict[str, Any]:
    return recorder.requests[-1]


def _actions(audit: AuditStore) -> list[str]:
    return [r["action"] for r in audit.query(category="board")]


# ── reads ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,hal0_path,upstream",
    [
        ("board", "/board", "/board"),
        ("task", "/tasks/t1", "/tasks/t1"),
        ("task_log", "/tasks/t1/log", "/tasks/t1/log"),
        ("boards", "/boards", "/boards"),
        ("profiles", "/profiles", "/profiles"),
        ("assignees", "/assignees", "/assignees"),
        ("stats", "/stats", "/stats"),
        ("diagnostics", "/diagnostics", "/diagnostics"),
        ("workers_active", "/workers/active", "/workers/active"),
        ("run", "/runs/r1", "/runs/r1"),
        ("config", "/config", "/config"),
        ("orchestration", "/orchestration", "/orchestration"),
    ],
)
def test_read_row_forwards(
    app_client: tuple, recorder: _Recorder, name: str, hal0_path: str, upstream: str
) -> None:
    client = app_client[1]
    recorder.respond("GET", f"{P}{upstream}", 200, {"row": name})
    r = client.get(f"/api/board{hal0_path}?board=alpha")
    assert r.status_code == 200
    assert r.json() == {"row": name}
    fwd = _last(recorder)
    assert (fwd["method"], fwd["path"]) == ("GET", f"{P}{upstream}")
    assert fwd["params"] == {"board": "alpha"}


def test_get_board_query_params_pass_through(app_client: tuple, recorder: _Recorder) -> None:
    client = app_client[1]
    client.get("/api/board/board?tenant=t&include_archived=yes&workflow_template_id=w&board=b")
    assert _last(recorder)["params"] == {
        "tenant": "t",
        "include_archived": "true",
        "workflow_template_id": "w",
        "board": "b",
    }
    client.get("/api/board/board?include_archived=0")
    assert _last(recorder)["params"] == {"include_archived": "false"}
    client.get("/api/board/board")
    assert _last(recorder)["params"] == {}


def test_task_log_tail_is_int(app_client: tuple, recorder: _Recorder) -> None:
    client = app_client[1]
    client.get("/api/board/tasks/t1/log?tail=50")
    assert _last(recorder)["params"] == {"tail": "50"}


def test_invalid_tail_400_without_upstream_call(app_client: tuple, recorder: _Recorder) -> None:
    client = app_client[1]
    r = client.get("/api/board/tasks/t1/log?tail=lots")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "board.invalid_query"
    assert recorder.requests == []


def test_agent_header_is_forwarded(app_client: tuple, recorder: _Recorder) -> None:
    client = app_client[1]
    client.get("/api/board/stats", headers={"X-hal0-Agent": "claude-dev"})
    assert _last(recorder)["agent"] == "claude-dev"


def test_no_etag_header(app_client: tuple, recorder: _Recorder) -> None:
    client = app_client[1]
    recorder.respond("GET", f"{P}/tasks/t1", 200, {"id": "t1", "revision": 3})
    r = client.get("/api/board/tasks/t1")
    assert r.status_code == 200
    assert "etag" not in r.headers


# ── mutations: forward + audit ──────────────────────────────────────────────

# (name, method, hal0 path, json body, upstream path, audit action)
_MUTATIONS = [
    ("create_task", "POST", "/tasks", {"title": "hi"}, "/tasks", "board.task.create"),
    ("update_task", "PATCH", "/tasks/t1", {"status": "done"}, "/tasks/t1", "board.task.update"),
    ("delete_task", "DELETE", "/tasks/t1", None, "/tasks/t1", "board.task.delete"),
    (
        "comment",
        "POST",
        "/tasks/t1/comments",
        {"body": "x"},
        "/tasks/t1/comments",
        "board.task.comment",
    ),
    ("bulk", "POST", "/tasks/bulk", {"ids": ["a", "b"]}, "/tasks/bulk", "board.task.bulk"),
    (
        "reassign",
        "POST",
        "/tasks/t1/reassign",
        {"profile": "d"},
        "/tasks/t1/reassign",
        "board.task.reassign",
    ),
    ("specify", "POST", "/tasks/t1/specify", {"a": 1}, "/tasks/t1/specify", "board.task.specify"),
    (
        "decompose",
        "POST",
        "/tasks/t1/decompose",
        {"a": 1},
        "/tasks/t1/decompose",
        "board.task.decompose",
    ),
    ("reclaim", "POST", "/tasks/t1/reclaim", {"a": 1}, "/tasks/t1/reclaim", "board.task.reclaim"),
    ("add_link", "POST", "/links", {"parent_id": "p", "child_id": "c"}, "/links", "board.link.add"),
    ("create_board", "POST", "/boards", {"slug": "x"}, "/boards", "board.board.create"),
    ("update_board", "PATCH", "/boards/x", {"name": "X"}, "/boards/x", "board.board.update"),
    ("switch_board", "POST", "/boards/x/switch", None, "/boards/x/switch", "board.board.switch"),
    (
        "update_profile",
        "PATCH",
        "/profiles/dev",
        {"d": "x"},
        "/profiles/dev",
        "board.profile.update",
    ),
    (
        "update_orch",
        "PUT",
        "/orchestration",
        {"auto_decompose": False},
        "/orchestration",
        "board.orchestration.update",
    ),
]


@pytest.mark.parametrize(
    "name,method,path,body,upstream,action", _MUTATIONS, ids=[m[0] for m in _MUTATIONS]
)
def test_mutation_row_forwards_and_audits(
    app_client: tuple, recorder: _Recorder, name, method, path, body, upstream, action
) -> None:
    app, client = app_client
    r = client.request(method, f"/api/board{path}?board=alpha", json=body)
    assert r.status_code == 200
    fwd = _last(recorder)
    assert (fwd["method"], fwd["path"]) == (method, f"{P}{upstream}")
    assert fwd["params"] == {"board": "alpha"}
    assert fwd["body"] == body
    assert action in _actions(app.state.audit)


def test_dispatch_nudge_forwards_and_audits(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.post("/api/board/dispatch?max=4")
    assert r.status_code == 200
    fwd = _last(recorder)
    assert (fwd["method"], fwd["path"], fwd["params"]) == ("POST", f"{P}/dispatch", {"max": "4"})
    assert "board.dispatch.nudge" in _actions(app.state.audit)


def test_delete_board_forwards_delete_flag(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    client.delete("/api/board/boards/x?delete=true")
    assert _last(recorder)["params"] == {"delete": "true"}
    client.delete("/api/board/boards/x")
    assert _last(recorder)["params"] == {"delete": "false"}
    assert "board.board.delete" in _actions(app.state.audit)


def test_remove_link_reads_query_ids(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    r = client.delete("/api/board/links?parent_id=p&child_id=c&board=alpha")
    assert r.status_code == 200
    fwd = _last(recorder)
    assert (fwd["method"], fwd["path"]) == ("DELETE", f"{P}/links")
    assert fwd["params"] == {"parent_id": "p", "child_id": "c", "board": "alpha"}
    assert fwd["body"] is None
    assert "board.link.remove" in _actions(app.state.audit)


def test_remove_link_body_fallback(app_client: tuple, recorder: _Recorder) -> None:
    _, client = app_client
    client.request("DELETE", "/api/board/links", json={"parent_id": "p", "child_id": "c"})
    fwd = _last(recorder)
    assert fwd["params"] == {"parent_id": "p", "child_id": "c"}
    assert fwd["body"] is None


def test_remove_link_query_wins_over_body(app_client: tuple, recorder: _Recorder) -> None:
    _, client = app_client
    client.request(
        "DELETE",
        "/api/board/links?parent_id=q1&child_id=q2",
        json={"parent_id": "b1", "child_id": "b2"},
    )
    assert _last(recorder)["params"] == {"parent_id": "q1", "child_id": "q2"}


@pytest.mark.parametrize("url", ["/api/board/links", "/api/board/links?parent_id=p"])
def test_remove_link_missing_id_400(app_client: tuple, recorder: _Recorder, url: str) -> None:
    _, client = app_client
    r = client.delete(url)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "board.invalid_query"
    assert recorder.requests == []


@pytest.mark.parametrize(
    "url", ["/api/board/tasks/t1/log?tail=-1", "/api/board/tasks/t1/log?tail=0"]
)
def test_non_positive_tail_400(app_client: tuple, recorder: _Recorder, url: str) -> None:
    _, client = app_client
    r = client.get(url)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "board.invalid_query"
    assert recorder.requests == []


def test_negative_max_400(app_client: tuple, recorder: _Recorder) -> None:
    _, client = app_client
    r = client.post("/api/board/dispatch?max=-2")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "board.invalid_query"
    assert recorder.requests == []


def test_agent_id_is_header_only(app_client: tuple, recorder: _Recorder) -> None:
    _, client = app_client
    client.post("/api/board/tasks", json={"title": "x", "agent_id": "spoof"})
    fwd = _last(recorder)
    assert fwd["agent"] != "spoof"
    assert fwd["body"]["agent_id"] == "spoof"  # body is data, not identity


def test_mutation_is_audited(app_client: tuple, recorder: _Recorder) -> None:
    app, client = app_client
    recorder.respond("POST", f"{P}/tasks", 200, {"task": {"id": "t9", "title": "hi"}})
    client.post("/api/board/tasks", json={"title": "hi"}, headers={"X-hal0-Agent": "claude-dev"})
    rows = app.state.audit.query(action="board.task.create")
    assert len(rows) == 1
    assert rows[0]["actor"] == "mcp:claude-dev"
    assert rows[0]["target"] == "t9"
    assert rows[0]["after"] is not None
    assert _last(recorder)["agent"] == "claude-dev"


def test_actor_dashboard_without_agent_header(app_client: tuple) -> None:
    app, client = app_client
    client.patch("/api/board/tasks/t2", json={"status": "ready"})
    assert app.state.audit.query(action="board.task.update")[0]["actor"] == "dashboard"


# ── errors ──────────────────────────────────────────────────────────────────


def test_board_404_passes_through(app_client: tuple, recorder: _Recorder) -> None:
    client = app_client[1]
    recorder.respond("GET", f"{P}/board", 404, {"detail": "no such board"})
    r = client.get("/api/board/board?board=gone")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "board.upstream_error"


def test_upstream_500_maps_to_502(app_client: tuple, recorder: _Recorder) -> None:
    client = app_client[1]
    recorder.respond("GET", f"{P}/board", 500, {"detail": "boom"})
    r = client.get("/api/board/board")
    assert r.status_code == 502


def test_upstream_error_on_mutation_is_audited_as_error(
    app_client: tuple, recorder: _Recorder
) -> None:
    app, client = app_client
    recorder.respond("PATCH", f"{P}/tasks/t1", 404, {"detail": "gone"})
    r = client.patch("/api/board/tasks/t1", json={"status": "done"})
    assert r.status_code == 404
    assert app.state.audit.query(action="board.task.update")[0]["outcome"] == "error"


def test_invalid_json_body_400_without_upstream_call(
    app_client: tuple, recorder: _Recorder
) -> None:
    client = app_client[1]
    r = client.post(
        "/api/board/tasks", content=b"{nope", headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "board.invalid_body"
    assert recorder.requests == []


def test_unreachable_503(app_client: tuple, recorder: _Recorder) -> None:
    client = app_client[1]
    recorder.fail_connect = True
    r = client.get("/api/board/board")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "board.unreachable"


def test_no_token_503(recorder: _Recorder, audit: AuditStore) -> None:
    app = _app(audit)

    def no_token() -> str:
        raise BoardNoToken("no dashboard token")

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(recorder.handler), base_url="http://hermes.test"
    )
    app.state.board_client = BoardClient(
        HermesKanbanClient(base_url="http://hermes.test", token_resolver=no_token, http_client=http)
    )
    with TestClient(app) as c:
        r = c.get("/api/board/board")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "board.no_token"
    assert recorder.requests == []


def test_upstream_error_is_logged(app_client: tuple, recorder: _Recorder) -> None:
    _, client = app_client
    recorder.respond("GET", f"{P}/tasks/ghost", 404, {"detail": "no"})
    with capture_logs() as logs:
        client.get("/api/board/tasks/ghost")
    ev = [e for e in logs if e["event"] == "board.upstream"]
    assert len(ev) == 1
    assert ev[0]["code"] == "board.upstream_error"
    assert ev[0]["status"] == 404
    assert ev[0]["path"] == "/api/board/tasks/ghost"
    assert ev[0]["log_level"] == "warning"


def test_not_installed_is_logged(audit: AuditStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hal0.board.auth.hermes_installed", lambda: False)
    with TestClient(_app(audit)) as c, capture_logs() as logs:
        c.get("/api/board/board")
    ev = [e for e in logs if e["event"] == "board.upstream"]
    assert len(ev) == 1
    assert (ev[0]["code"], ev[0]["status"], ev[0]["path"]) == (
        "board.not_installed",
        503,
        "/api/board/board",
    )


def test_not_installed_503(audit: AuditStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hal0.board.auth.hermes_installed", lambda: False)
    app = _app(audit)  # no board_client / board_installed on state
    with TestClient(app) as c:
        r = c.get("/api/board/board")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "board.not_installed"
