"""Tests for the board chat orchestrator — src/hal0/api/routes/board_chat.py.

The LLM backend is injected via app.state.board_chat_llm (a stub). Board
mutations go through the real HermesKanbanClient behind an httpx.MockTransport
recorder. Asserts SSE framing, tool→mutation mapping, per-tool audit, ?board
threading, loop termination, and error handling.

Run targeted:
    PYTHONPATH=src .venv/bin/python -m pytest tests/board/test_board_chat.py -q
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.activity import AuditStore
from hal0.api.middleware import error_codes
from hal0.api.routes import board
from hal0.api.routes.board_chat import _extract_tool_calls, _resolve_tool, _tool_schemas
from hal0.board import KANBAN_BASE_PATH, HermesKanbanClient

P = KANBAN_BASE_PATH


# ── harness ─────────────────────────────────────────────────────────────────


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses: dict[tuple[str, str], httpx.Response] = {}

    def respond(self, method: str, path: str, body: Any, status: int = 200) -> None:
        self.responses[(method, f"{P}{path}")] = httpx.Response(status, json=body)

    async def handler(self, request: httpx.Request) -> httpx.Response:
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
        return httpx.Response(200, json={"ok": True})

    def recorded(self, method: str, path: str) -> list[dict[str, Any]]:
        full = f"{P}{path}"
        return [r for r in self.requests if r["method"] == method and r["path"] == full]


class _StubLLM:
    """Pops a canned chat-completion response per call."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(body)
        if self._responses:
            return self._responses.pop(0)
        # Default: terminate with a plain message.
        return _final_response("done")


def _tool_call_response(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ],
                }
            }
        ]
    }


def _multi_tool_response(specs: list[tuple[str, dict[str, Any], str]]) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": cid,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                        for name, args, cid in specs
                    ],
                }
            }
        ]
    }


def _final_response(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def _make_app(recorder: _Recorder, stub: Any, tmp_path, *, no_client: bool = False) -> tuple:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(board.router, prefix="/api/board")
    if no_client:
        app.state.hermes_kanban = None
    else:
        transport = httpx.MockTransport(recorder.handler)
        http = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9119")
        app.state.hermes_kanban = HermesKanbanClient(
            http_client=http, session_token_resolver=lambda: "TOK"
        )
    store = AuditStore(tmp_path / "audit.db")
    store.init_schema()
    app.state.audit = store
    app.state.board_chat_llm = stub
    return app, TestClient(app)


def _sse_events(text: str) -> list[dict[str, Any]]:
    out = []
    for line in text.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[len("data: ") :]))
    return out


# ── SSE framing ─────────────────────────────────────────────────────────────


def test_sse_framing_tool_then_token_then_done(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM(
        [
            _tool_call_response("move_task", {"task_id": "t1", "status": "done"}, "c1"),
            _final_response("moved it"),
        ]
    )
    _app, client = _make_app(rec, stub, tmp_path)
    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "go"}]})
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    types = [e["type"] for e in events]
    assert "tool_call" in types
    assert "tool_result" in types
    assert types.index("tool_call") < types.index("tool_result")
    assert events[-1]["type"] == "done"
    # token from the final round
    token = next(e for e in events if e["type"] == "token")
    assert token["text"] == "moved it"
    tc = next(e for e in events if e["type"] == "tool_call")
    assert tc["name"] == "move_task"
    assert tc["arguments"] == {"task_id": "t1", "status": "done"}
    assert tc["id"] == "c1"


# ── tool → mutation mapping ─────────────────────────────────────────────────


def _tool_mutation_case(
    tool: str,
    args: dict,
    expected_method: str,
    expected_path: str,
    expected_body_subset: dict | None,
    tmp_path,
):
    rec = _Recorder()
    rec.respond(expected_method, expected_path, {"ok": True})
    stub = _StubLLM([_tool_call_response(tool, args, "c_mut"), _final_response("ok")])
    _app, client = _make_app(rec, stub, tmp_path)
    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "do"}]})
    assert resp.status_code == 200
    hits = rec.recorded(expected_method, expected_path)
    assert len(hits) >= 1, f"Expected {expected_method} {expected_path}, got {rec.requests}"
    if expected_body_subset:
        body = json.loads(hits[0]["body"]) if hits[0]["body"] else {}
        for k, v in expected_body_subset.items():
            assert body.get(k) == v, f"body mismatch on {k}: {body}"


def test_tool_move_task(tmp_path) -> None:
    _tool_mutation_case(
        "move_task",
        {"task_id": "t1", "status": "done"},
        "PATCH",
        "/tasks/t1",
        {"status": "done"},
        tmp_path,
    )


def test_tool_assign_task(tmp_path) -> None:
    _tool_mutation_case(
        "assign_task",
        {"task_id": "t2", "assignee": "bob"},
        "PATCH",
        "/tasks/t2",
        {"assignee": "bob"},
        tmp_path,
    )


def test_tool_create_task(tmp_path) -> None:
    _tool_mutation_case(
        "create_task",
        {"title": "foo"},
        "POST",
        "/tasks",
        {"title": "foo"},
        tmp_path,
    )


def test_tool_comment_task(tmp_path) -> None:
    _tool_mutation_case(
        "comment_task",
        {"task_id": "t3", "body": "lgtm"},
        "POST",
        "/tasks/t3/comments",
        {"body": "lgtm"},
        tmp_path,
    )


def test_tool_add_dependency(tmp_path) -> None:
    _tool_mutation_case(
        "add_dependency",
        {"parent_id": "p", "child_id": "c"},
        "POST",
        "/links",
        {"parent_id": "p", "child_id": "c"},
        tmp_path,
    )


def test_tool_remove_dependency(tmp_path) -> None:
    # DELETE /links carries parent_id/child_id as QUERY params (SPEC §4).
    rec = _Recorder()
    rec.respond("DELETE", "/links", {"ok": True})
    stub = _StubLLM(
        [
            _tool_call_response("remove_dependency", {"parent_id": "p1", "child_id": "c1"}, "c_rm"),
            _final_response("ok"),
        ]
    )
    _app, client = _make_app(rec, stub, tmp_path)
    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert resp.status_code == 200
    hits = rec.recorded("DELETE", "/links")
    assert len(hits) >= 1, f"got {rec.requests}"
    assert hits[0]["params"]["parent_id"] == "p1"
    assert hits[0]["params"]["child_id"] == "c1"


def test_tool_block_task(tmp_path) -> None:
    _tool_mutation_case(
        "block_task",
        {"task_id": "t4", "block_reason": "waiting"},
        "PATCH",
        "/tasks/t4",
        {"status": "blocked", "block_reason": "waiting"},
        tmp_path,
    )


def test_tool_specify_task(tmp_path) -> None:
    _tool_mutation_case(
        "specify_task",
        {"task_id": "t5"},
        "POST",
        "/tasks/t5/specify",
        None,
        tmp_path,
    )


def test_tool_decompose_task(tmp_path) -> None:
    _tool_mutation_case(
        "decompose_task",
        {"task_id": "t6"},
        "POST",
        "/tasks/t6/decompose",
        None,
        tmp_path,
    )


def test_tool_nudge_dispatcher(tmp_path) -> None:
    rec = _Recorder()
    rec.respond("POST", "/dispatch", {"ok": True})
    stub = _StubLLM(
        [_tool_call_response("nudge_dispatcher", {"max": 5}, "c_n"), _final_response("ok")]
    )
    _app, client = _make_app(rec, stub, tmp_path)
    client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "n"}]})
    hits = rec.recorded("POST", "/dispatch")
    assert len(hits) >= 1
    assert hits[0]["params"]["max"] == "5"


# ── audit per tool call ─────────────────────────────────────────────────────


def test_audit_per_tool_call(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM(
        [
            _tool_call_response("move_task", {"task_id": "t1", "status": "done"}, "c1"),
            _final_response("ok"),
        ]
    )
    app, client = _make_app(rec, stub, tmp_path)
    client.post(
        "/api/board/chat",
        json={"messages": [{"role": "user", "content": "go"}]},
        headers={"X-hal0-Agent": "claude-dev"},
    )
    rows = app.state.audit.query(action="board.chat.turn")
    assert len(rows) == 1
    assert rows[0]["actor"] == "mcp:claude-dev"


def test_board_threading_in_tool_dispatch(tmp_path) -> None:
    rec = _Recorder()
    rec.respond("PATCH", "/tasks/t1", {"ok": True})
    stub = _StubLLM(
        [
            _tool_call_response("move_task", {"task_id": "t1", "status": "done"}, "c1"),
            _final_response("ok"),
        ]
    )
    _app, client = _make_app(rec, stub, tmp_path)
    client.post(
        "/api/board/chat",
        json={"board": "alpha", "messages": [{"role": "user", "content": "go"}]},
    )
    hits = rec.recorded("PATCH", "/tasks/t1")
    assert hits[0]["params"]["board"] == "alpha"


def test_multi_tool_one_response(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM(
        [
            _multi_tool_response(
                [
                    ("move_task", {"task_id": "t1", "status": "done"}, "c1"),
                    ("comment_task", {"task_id": "t1", "body": "hi"}, "c2"),
                ]
            ),
            _final_response("ok"),
        ]
    )
    app, client = _make_app(rec, stub, tmp_path)
    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "go"}]})
    events = _sse_events(resp.text)
    results = [e for e in events if e["type"] == "tool_result"]
    assert len(results) == 2
    rows = app.state.audit.query(action="board.chat.turn")
    assert len(rows) == 2


# ── loop termination + errors ───────────────────────────────────────────────


def test_loop_budget_exhausted(tmp_path) -> None:
    rec = _Recorder()

    class _InfiniteLLM:
        async def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
            return _tool_call_response("move_task", {"task_id": "t1", "status": "done"}, "c")

    _app, client = _make_app(rec, _InfiniteLLM(), tmp_path)
    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "go"}]})
    events = _sse_events(resp.text)
    assert any(e["type"] == "error" and "budget" in e["message"] for e in events)
    assert events[-1]["type"] == "done"


def test_llm_error_surfaces(tmp_path) -> None:
    rec = _Recorder()

    class _ErrLLM:
        async def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
            return {"error": "boom"}

    _app, client = _make_app(rec, _ErrLLM(), tmp_path)
    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "go"}]})
    events = _sse_events(resp.text)
    assert any(e["type"] == "error" and "boom" in e["message"] for e in events)
    assert events[-1]["type"] == "done"


def test_backend_not_configured(tmp_path) -> None:
    rec = _Recorder()
    _app, client = _make_app(rec, _StubLLM([]), tmp_path, no_client=True)
    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "go"}]})
    events = _sse_events(resp.text)
    assert any(e["type"] == "error" and "not configured" in e["message"] for e in events)


# ── unit: _resolve_tool 5-tuple (method, path, params, body, target) ────────


def test_resolve_tool_move_task() -> None:
    method, path, params, body, target = _resolve_tool(
        "move_task", {"task_id": "t1", "status": "done"}
    )
    assert method == "PATCH"
    assert path == "/tasks/t1"
    assert body == {"status": "done"}
    assert params == {}
    assert target == "t1"


def test_resolve_tool_remove_dependency() -> None:
    method, path, params, body, _target = _resolve_tool(
        "remove_dependency", {"parent_id": "p", "child_id": "c"}
    )
    assert method == "DELETE"
    assert path == "/links"
    assert params == {"parent_id": "p", "child_id": "c"}
    assert body is None


def test_resolve_tool_nudge_dispatcher() -> None:
    method, path, params, _body, _ = _resolve_tool("nudge_dispatcher", {"max": 5})
    assert method == "POST"
    assert path == "/dispatch"
    assert params == {"max": 5}


def test_resolve_tool_create_task_drops_none() -> None:
    _m, _p, _params, body, _t = _resolve_tool("create_task", {"title": "x", "body": None})
    assert body == {"title": "x"}


def test_resolve_tool_unknown() -> None:
    method, _path, _params, _body, _target = _resolve_tool("nope", {})
    assert method is None


def test_tool_schemas_complete() -> None:
    names = {s["function"]["name"] for s in _tool_schemas()}
    assert names == {
        "move_task",
        "assign_task",
        "create_task",
        "comment_task",
        "add_dependency",
        "remove_dependency",
        "block_task",
        "specify_task",
        "decompose_task",
        "nudge_dispatcher",
    }


def test_extract_tool_calls_parses_string_args() -> None:
    resp = _tool_call_response("move_task", {"task_id": "t1", "status": "done"}, "c1")
    calls = _extract_tool_calls(resp)
    assert calls[0]["name"] == "move_task"
    assert calls[0]["arguments"] == {"task_id": "t1", "status": "done"}


def test_extract_tool_calls_empty_when_none() -> None:
    assert _extract_tool_calls(_final_response("hi")) == []
