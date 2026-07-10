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
from hal0.api.routes.board_chat import (
    _SYSTEM_PROMPT,
    BRAIN_SLOT_MODEL,
    _compact_board,
    _extract_tool_calls,
    _resolve_platform_tool,
    _resolve_read_tool,
    _resolve_tool,
    _split_thinking,
    _tool_schemas,
)
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


class _PlatformRecorder:
    """Like _Recorder but for hal0-api's OWN routes (no kanban prefix)."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses: dict[tuple[str, str], httpx.Response] = {}

    def respond(self, method: str, path: str, body: Any, status: int = 200) -> None:
        self.responses[(method, path)] = httpx.Response(status, json=body)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({"method": request.method, "path": request.url.path})
        return self.responses.get(
            (request.method, request.url.path), httpx.Response(200, json={"ok": True})
        )

    def recorded(self, method: str, path: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["method"] == method and r["path"] == path]


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


def _make_app(
    recorder: _Recorder,
    stub: Any,
    tmp_path,
    *,
    no_client: bool = False,
    platform: _PlatformRecorder | None = None,
) -> tuple:
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
    if platform is not None:
        app.state.platform_http = httpx.AsyncClient(
            transport=httpx.MockTransport(platform.handler),
            base_url="http://127.0.0.1:8080",
        )
    store = AuditStore(tmp_path / "audit.db")
    store.init_schema()
    app.state.audit = store
    app.state.board_chat_llm = stub
    # Isolate from any real hal0-brain persona on the test box — an empty
    # root makes _resolve_profile fall back to the built-in prompt/model.
    app.state.brain_persona_root = tmp_path / "personas"
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


# ── read tools (unaudited, board-scoped) ────────────────────────────────────


def test_read_tool_get_board_forwards_and_is_not_audited(tmp_path) -> None:
    rec = _Recorder()
    rec.respond(
        "GET",
        "/board",
        {
            "columns": [
                {
                    "name": "todo",
                    "tasks": [
                        {
                            "id": "t1",
                            "title": "fix",
                            "status": "todo",
                            "assignee": "bob",
                            "body": "a very long body that must be trimmed",
                        }
                    ],
                }
            ]
        },
    )
    stub = _StubLLM([_tool_call_response("get_board", {}, "c_gb"), _final_response("ok")])
    app, client = _make_app(rec, stub, tmp_path)
    resp = client.post(
        "/api/board/chat",
        json={"board": "alpha", "messages": [{"role": "user", "content": "what's up"}]},
    )
    assert resp.status_code == 200
    hits = rec.recorded("GET", "/board")
    assert len(hits) == 1
    assert hits[0]["params"]["board"] == "alpha"  # board scope threads through
    # tool_result carries the COMPACTED rows (no body field)
    events = _sse_events(resp.text)
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["result"] == {
        "tasks": [{"id": "t1", "title": "fix", "status": "todo", "assignee": "bob"}]
    }
    # reads write NO audit rows — matches the REST proxy's split
    assert app.state.audit.query(action="board.chat.turn") == []


def test_read_tool_get_task_forwards(tmp_path) -> None:
    rec = _Recorder()
    rec.respond("GET", "/tasks/t9", {"task": {"id": "t9"}, "comments": []})
    stub = _StubLLM(
        [_tool_call_response("get_task", {"task_id": "t9"}, "c_gt"), _final_response("ok")]
    )
    app, client = _make_app(rec, stub, tmp_path)
    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert len(rec.recorded("GET", "/tasks/t9")) == 1
    events = _sse_events(resp.text)
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["result"]["task"]["id"] == "t9"  # detail is NOT compacted
    assert app.state.audit.query(action="board.chat.turn") == []


def test_read_tool_get_assignees_forwards(tmp_path) -> None:
    rec = _Recorder()
    rec.respond("GET", "/assignees", [{"name": "scout", "on_disk": True}])
    stub = _StubLLM([_tool_call_response("get_assignees", {}, "c_ga"), _final_response("ok")])
    _app, client = _make_app(rec, stub, tmp_path)
    client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert len(rec.recorded("GET", "/assignees")) == 1


# ── platform tools (slots/models/agents/stats via self-HTTP) ────────────────


def test_platform_list_slots_forwards_and_is_not_audited(tmp_path) -> None:
    rec = _Recorder()
    plat = _PlatformRecorder()
    plat.respond("GET", "/api/slots", [{"name": "agent", "state": "serving"}])
    stub = _StubLLM([_tool_call_response("list_slots", {}, "c_ls"), _final_response("ok")])
    app, client = _make_app(rec, stub, tmp_path, platform=plat)
    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert len(plat.recorded("GET", "/api/slots")) == 1
    events = _sse_events(resp.text)
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["result"] == [{"name": "agent", "state": "serving"}]
    assert app.state.audit.query(action="platform.chat.turn") == []


def test_platform_slot_restart_forwards_and_audits(tmp_path) -> None:
    rec = _Recorder()
    plat = _PlatformRecorder()
    plat.respond("POST", "/api/slots/img/restart", {"ok": True})
    stub = _StubLLM(
        [_tool_call_response("slot_restart", {"name": "img"}, "c_sr"), _final_response("ok")]
    )
    app, client = _make_app(rec, stub, tmp_path, platform=plat)
    client.post(
        "/api/board/chat",
        json={"messages": [{"role": "user", "content": "restart img"}]},
        headers={"X-hal0-Agent": "op"},
    )
    assert len(plat.recorded("POST", "/api/slots/img/restart")) == 1
    rows = app.state.audit.query(action="platform.chat.turn")
    assert len(rows) == 1
    assert rows[0]["target"] == "img"
    # No Hermes traffic for a platform tool.
    assert rec.requests == []


def test_platform_error_becomes_tool_result(tmp_path) -> None:
    rec = _Recorder()
    plat = _PlatformRecorder()
    plat.respond("GET", "/api/slots", {"error": "nope"}, status=500)
    stub = _StubLLM([_tool_call_response("list_slots", {}, "c_e"), _final_response("ok")])
    _app, client = _make_app(rec, stub, tmp_path, platform=plat)
    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    events = _sse_events(resp.text)
    result = next(e for e in events if e["type"] == "tool_result")
    assert "HTTP 500" in result["result"]["error"]
    # The loop keeps stepping — the turn still terminates cleanly.
    assert events[-1]["type"] == "done"


def test_orchestration_tools_route_via_kanban_client(tmp_path) -> None:
    rec = _Recorder()
    rec.respond("GET", "/orchestration", {"orchestrator_profile": "admin"})
    rec.respond("PUT", "/orchestration", {"ok": True})
    stub = _StubLLM(
        [
            _tool_call_response("get_orchestration", {}, "c_go"),
            _tool_call_response("update_orchestration", {"auto_decompose": True}, "c_uo"),
            _final_response("ok"),
        ]
    )
    app, client = _make_app(rec, stub, tmp_path)
    client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert len(rec.recorded("GET", "/orchestration")) == 1
    puts = rec.recorded("PUT", "/orchestration")
    assert len(puts) == 1
    assert json.loads(puts[0]["body"]) == {"auto_decompose": True}
    # read unaudited, update audited as a board mutation
    rows = app.state.audit.query(action="board.chat.turn")
    assert len(rows) == 1


# ── system prompt injection ─────────────────────────────────────────────────


def test_system_prompt_injected_when_absent(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM([_final_response("hi")])
    _app, client = _make_app(rec, stub, tmp_path)
    client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "hello"}]})
    sent = stub.calls[0]["messages"]
    assert sent[0]["role"] == "system"
    assert sent[0]["content"] == _SYSTEM_PROMPT
    assert sent[1] == {"role": "user", "content": "hello"}


def test_system_prompt_not_duplicated_when_client_sends_one(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM([_final_response("hi")])
    _app, client = _make_app(rec, stub, tmp_path)
    client.post(
        "/api/board/chat",
        json={
            "messages": [
                {"role": "system", "content": "custom"},
                {"role": "user", "content": "hello"},
            ]
        },
    )
    sent = stub.calls[0]["messages"]
    assert sent[0] == {"role": "system", "content": "custom"}
    assert sum(1 for m in sent if m.get("role") == "system") == 1


# ── hal0-brain profile (model default + persona override) ───────────────────


def test_default_model_is_brain_slot(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM([_final_response("hi")])
    _app, client = _make_app(rec, stub, tmp_path)
    client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "hello"}]})
    assert stub.calls[0]["model"] == BRAIN_SLOT_MODEL == "hal0/brain"


def test_payload_model_overrides_default(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM([_final_response("hi")])
    _app, client = _make_app(rec, stub, tmp_path)
    client.post(
        "/api/board/chat",
        json={"messages": [{"role": "user", "content": "hello"}], "model": "hal0/utility"},
    )
    assert stub.calls[0]["model"] == "hal0/utility"


def test_hal0_brain_persona_overrides_prompt_and_model(tmp_path) -> None:
    from hal0.agents.personas import Persona, save_persona

    rec = _Recorder()
    stub = _StubLLM([_final_response("hi")])
    app, client = _make_app(rec, stub, tmp_path)
    save_persona(
        Persona(
            id="hal0-brain",
            display_name="hal0 Brain",
            system_prompt="operator-tuned steward prompt",
            preferred_model="hal0/custom-brain",
        ),
        root=app.state.brain_persona_root,
    )
    client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "hello"}]})
    sent = stub.calls[0]
    assert sent["model"] == "hal0/custom-brain"
    assert sent["messages"][0] == {"role": "system", "content": "operator-tuned steward prompt"}


# ── thinking frames (reasoning models) ──────────────────────────────────────


def test_reasoning_content_streams_as_thinking_frame(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "the answer",
                            "reasoning_content": "let me check the slots",
                        }
                    }
                ]
            }
        ]
    )
    _app, client = _make_app(rec, stub, tmp_path)
    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    events = _sse_events(resp.text)
    thinking = next(e for e in events if e["type"] == "thinking")
    assert thinking["text"] == "let me check the slots"
    token = next(e for e in events if e["type"] == "token")
    assert token["text"] == "the answer"
    assert events.index(thinking) < events.index(token)


def test_inline_think_tags_split_into_thinking_frame(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM([_final_response("<think>ponder ponder</think>the reply")])
    _app, client = _make_app(rec, stub, tmp_path)
    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    events = _sse_events(resp.text)
    thinking = next(e for e in events if e["type"] == "thinking")
    assert thinking["text"] == "ponder ponder"
    token = next(e for e in events if e["type"] == "token")
    assert token["text"] == "the reply"


def test_split_thinking_unterminated_tag() -> None:
    thinking, visible = _split_thinking("<think>still going")
    assert thinking == "still going"
    assert visible == ""


def test_split_thinking_passthrough_without_tags() -> None:
    thinking, visible = _split_thinking("plain reply")
    assert thinking == ""
    assert visible == "plain reply"


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


def test_resolve_read_tool_paths() -> None:
    assert _resolve_read_tool("get_board", {}) == ("GET", "/board")
    assert _resolve_read_tool("get_task", {"task_id": "t1"}) == ("GET", "/tasks/t1")
    assert _resolve_read_tool("get_assignees", {}) == ("GET", "/assignees")
    assert _resolve_read_tool("get_orchestration", {}) == ("GET", "/orchestration")
    assert _resolve_read_tool("move_task", {}) == (None, "")


def test_resolve_platform_tool_paths() -> None:
    assert _resolve_platform_tool("list_slots", {}) == ("GET", "/api/slots", False)
    assert _resolve_platform_tool("get_slot", {"name": "img"}) == (
        "GET",
        "/api/slots/img",
        False,
    )
    assert _resolve_platform_tool("slot_load", {"name": "img"}) == (
        "POST",
        "/api/slots/img/load",
        True,
    )
    assert _resolve_platform_tool("slot_unload", {"name": "img"}) == (
        "POST",
        "/api/slots/img/unload",
        True,
    )
    assert _resolve_platform_tool("slot_restart", {"name": "img"}) == (
        "POST",
        "/api/slots/img/restart",
        True,
    )
    assert _resolve_platform_tool("list_models", {}) == ("GET", "/api/models", False)
    assert _resolve_platform_tool("hardware_stats", {}) == (
        "GET",
        "/api/stats/hardware",
        False,
    )
    assert _resolve_platform_tool("list_agents", {}) == ("GET", "/api/agents", False)
    assert _resolve_platform_tool("move_task", {}) == (None, "", False)


def test_compact_board_shapes() -> None:
    row = {"id": "t1", "title": "x", "status": "todo", "body": "long", "priority": 2}
    compacted = {"id": "t1", "title": "x", "status": "todo", "priority": 2}
    assert _compact_board({"columns": [{"tasks": [row]}]}) == {"tasks": [compacted]}
    assert _compact_board({"lanes": {"todo": [row]}}) == {"tasks": [compacted]}
    assert _compact_board({"tasks": [row]}) == {"tasks": [compacted]}
    assert _compact_board([row]) == {"tasks": [compacted]}
    # unrecognised shapes pass through untouched
    assert _compact_board({"weird": True}) == {"weird": True}
    assert _compact_board("raw") == "raw"


def test_tool_schemas_complete() -> None:
    names = {s["function"]["name"] for s in _tool_schemas()}
    assert names == {
        # board reads (unaudited — the model's eyes on the board)
        "get_board",
        "get_task",
        "get_assignees",
        "get_orchestration",
        # audited board mutations
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
        "update_orchestration",
        # platform surface (slots/models/agents/stats via self-HTTP)
        "list_slots",
        "get_slot",
        "slot_load",
        "slot_unload",
        "slot_restart",
        "list_models",
        "hardware_stats",
        "list_agents",
    }


def test_extract_tool_calls_parses_string_args() -> None:
    resp = _tool_call_response("move_task", {"task_id": "t1", "status": "done"}, "c1")
    calls = _extract_tool_calls(resp)
    assert calls[0]["name"] == "move_task"
    assert calls[0]["arguments"] == {"task_id": "t1", "status": "done"}


def test_extract_tool_calls_empty_when_none() -> None:
    assert _extract_tool_calls(_final_response("hi")) == []
