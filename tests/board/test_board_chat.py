"""Tests for the board chat orchestrator — src/hal0/api/routes/board_chat.py.

The LLM backend is injected via app.state.board_chat_llm (a stub). Board
tools run against a recording hal0 BoardStore (the SAME store /api/board/*
serves from — #1829). Asserts SSE framing, tool→mutation mapping, per-tool audit, ?board
threading, loop termination, and error handling.

Run targeted:
    PYTHONPATH=src .venv/bin/python -m pytest tests/board/test_board_chat.py -q
"""

from __future__ import annotations

import asyncio
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
    _brain_chat_config,
    _compact_board,
    _extract_tool_calls,
    _is_read_tool,
    _resolve_platform_tool,
    _resolve_tool,
    _split_thinking,
    _tool_schemas,
)
from hal0.board.store import BoardStore
from hal0.config.schema import BrainChatConfig, Hal0Config

# ── harness ─────────────────────────────────────────────────────────────────


class _Recorder:
    """Ordered log of every BoardStore call the chat makes.

    Board tools run against the hal0-OWNED store now (#1829), not a Hermes
    forward, so the double is a recording :class:`BoardStore` subclass
    (:class:`_SpyStore`) rather than an httpx mock — the store really executes,
    so these tests see real board state and a tool that stopped touching the
    store would be caught here, not just upstream-path-shaped.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def recorded(self, name: str) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
        return [(args, kwargs) for n, args, kwargs in self.calls if n == name]

    def names(self) -> list[str]:
        return [n for n, _a, _k in self.calls]


def _spy_store(recorder: _Recorder, db_path) -> BoardStore:
    """A real BoardStore that logs every call it serves into ``recorder``."""

    class _SpyStore(BoardStore):
        def __getattribute__(self, name: str) -> Any:
            attr = super().__getattribute__(name)
            if name.startswith("_") or not callable(attr):
                return attr

            def _logged(*args: Any, **kwargs: Any) -> Any:
                recorder.calls.append((name, args, kwargs))
                return attr(*args, **kwargs)

            return _logged

    store = _SpyStore(db_path)
    asyncio.run(store.ensure_initialized(None))
    recorder.calls.clear()  # the seed is harness noise, not a chat call
    return store


def _seed_card(store: BoardStore, **fields: Any) -> str:
    """Put one card on the board out-of-band and return its id."""
    body = {"title": "seeded", **fields}
    return str(store.create_task(body)["task"]["id"])


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
    platform: _PlatformRecorder | None = None,
) -> tuple:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(board.router, prefix="/api/board")
    # hal0 owns the board: the chat's board tools and /api/board/* share ONE
    # store, and no Hermes client is wired at all (#1829).
    app.state.board_store = _spy_store(recorder, tmp_path / "board.db")
    app.state.hermes_kanban = None
    if platform is not None:
        app.state.platform_http = httpx.AsyncClient(
            transport=httpx.MockTransport(platform.handler),
            base_url="http://127.0.0.1:8080",
        )
    store = AuditStore(tmp_path / "audit.db")
    store.init_schema()
    app.state.audit = store
    app.state.board_chat_llm = stub
    # This harness exercises the mutation tools directly, so it opts out of
    # the shipped read-only default (spec-kb23 §4b). Tests that probe the
    # guardrail itself override this via _set_brain_chat_config.
    app.state.hal0_config = Hal0Config(brain_chat=BrainChatConfig(read_only=False))
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
    expected_call: str,
    expected_args: tuple,
    tmp_path,
    *,
    expected_kwargs: dict | None = None,
) -> None:
    """Drive one mutation tool and assert it called the STORE method the
    matching /api/board/* handler calls, with the same payload."""
    rec = _Recorder()
    stub = _StubLLM([_tool_call_response(tool, args, "c_mut"), _final_response("ok")])
    _app, client = _make_app(rec, stub, tmp_path)
    rec.calls.clear()
    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "do"}]})
    assert resp.status_code == 200
    hits = rec.recorded(expected_call)
    assert len(hits) >= 1, f"Expected store.{expected_call}(), got {rec.names()}"
    call_args, call_kwargs = hits[0]
    assert call_args == expected_args, f"args mismatch: {call_args}"
    if expected_kwargs is not None:
        for k, v in expected_kwargs.items():
            assert call_kwargs.get(k) == v, f"kwarg mismatch on {k}: {call_kwargs}"


def test_tool_move_task(tmp_path) -> None:
    _tool_mutation_case(
        "move_task",
        {"task_id": "t1", "status": "done"},
        "update_task",
        ("t1", {"status": "done"}),
        tmp_path,
    )


def test_tool_assign_task(tmp_path) -> None:
    _tool_mutation_case(
        "assign_task",
        {"task_id": "t2", "assignee": "bob"},
        "update_task",
        ("t2", {"assignee": "bob"}),
        tmp_path,
    )


def test_tool_create_task(tmp_path) -> None:
    _tool_mutation_case(
        "create_task",
        {"title": "foo"},
        "create_task",
        ({"title": "foo"},),
        tmp_path,
        expected_kwargs={"board": None},
    )


def test_tool_comment_task(tmp_path) -> None:
    _tool_mutation_case(
        "comment_task",
        {"task_id": "t3", "body": "lgtm"},
        "comment_task",
        ("t3", {"body": "lgtm"}),
        tmp_path,
    )


def test_tool_add_dependency(tmp_path) -> None:
    _tool_mutation_case(
        "add_dependency",
        {"parent_id": "p", "child_id": "c"},
        "add_link",
        ({"parent_id": "p", "child_id": "c"},),
        tmp_path,
    )


def test_tool_remove_dependency(tmp_path) -> None:
    # remove_link takes the two ids positionally (the REST handler reads them
    # off the query string and does the same).
    _tool_mutation_case(
        "remove_dependency",
        {"parent_id": "p1", "child_id": "c1"},
        "remove_link",
        ("p1", "c1"),
        tmp_path,
    )


def test_tool_block_task(tmp_path) -> None:
    _tool_mutation_case(
        "block_task",
        {"task_id": "t4", "block_reason": "waiting"},
        "update_task",
        ("t4", {"status": "blocked", "block_reason": "waiting"}),
        tmp_path,
    )


def test_tool_specify_task(tmp_path) -> None:
    _tool_mutation_case(
        "specify_task",
        {"task_id": "t5"},
        "specify",
        ("t5", {}),
        tmp_path,
    )


def test_tool_decompose_task(tmp_path) -> None:
    _tool_mutation_case(
        "decompose_task",
        {"task_id": "t6"},
        "decompose",
        ("t6", {}),
        tmp_path,
    )


def test_tool_nudge_dispatcher(tmp_path) -> None:
    _tool_mutation_case(
        "nudge_dispatcher",
        {"max": 5},
        "dispatch_nudge",
        (),
        tmp_path,
        expected_kwargs={"max_dispatch": 5},
    )


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
    """``?board=`` threads into the board-scoped store calls (create + reads),
    exactly as ``/api/board/*?board=`` does."""
    rec = _Recorder()
    stub = _StubLLM(
        [
            _tool_call_response("create_task", {"title": "scoped"}, "c1"),
            _tool_call_response("get_board", {}, "c2"),
            _final_response("ok"),
        ]
    )
    _app, client = _make_app(rec, stub, tmp_path)
    client.post(
        "/api/board/chat",
        json={"board": "alpha", "messages": [{"role": "user", "content": "go"}]},
    )
    assert rec.recorded("create_task")[0][1]["board"] == "alpha"
    assert rec.recorded("get_board")[0][1]["board"] == "alpha"


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


def test_read_tool_get_board_reads_the_store_and_is_not_audited(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM([_tool_call_response("get_board", {}, "c_gb"), _final_response("ok")])
    app, client = _make_app(rec, stub, tmp_path)
    task_id = _seed_card(
        app.state.board_store,
        title="fix",
        status="todo",
        assignee="bob",
        body="a very long body that must be trimmed",
    )
    rec.calls.clear()

    resp = client.post(
        "/api/board/chat",
        json={"messages": [{"role": "user", "content": "what's up"}]},
    )
    assert resp.status_code == 200
    assert len(rec.recorded("get_board")) == 1
    # tool_result carries the COMPACTED rows (no body field)
    events = _sse_events(resp.text)
    result = next(e for e in events if e["type"] == "tool_result")
    (row,) = result["result"]["tasks"]
    assert row["id"] == task_id
    assert (row["title"], row["status"], row["assignee"]) == ("fix", "todo", "bob")
    assert "body" not in row  # compacted: the long body never reaches the loop
    # reads write NO audit rows — matches the REST router's split
    assert app.state.audit.query(action="board.chat.turn") == []


def test_read_tool_get_task_reads_the_store(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM([_final_response("ok")])
    app, client = _make_app(rec, stub, tmp_path)
    task_id = _seed_card(app.state.board_store, title="deep", body="the full body")
    rec.calls.clear()
    app.state.board_chat_llm = _StubLLM(
        [_tool_call_response("get_task", {"task_id": task_id}, "c_gt"), _final_response("ok")]
    )

    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert rec.recorded("get_task")[0][0] == (task_id,)
    events = _sse_events(resp.text)
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["result"]["id"] == task_id
    assert result["result"]["body"] == "the full body"  # detail is NOT compacted
    assert app.state.audit.query(action="board.chat.turn") == []


def test_read_tool_get_assignees_reads_the_store(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM([_tool_call_response("get_assignees", {}, "c_ga"), _final_response("ok")])
    app, client = _make_app(rec, stub, tmp_path)
    _seed_card(app.state.board_store, title="assigned", assignee="scout")
    rec.calls.clear()

    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert len(rec.recorded("list_assignees")) == 1
    events = _sse_events(resp.text)
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["result"] == [{"id": "scout", "label": "scout"}]


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
    # No board-store traffic for a platform tool.
    assert rec.calls == []


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


def test_orchestration_tools_route_through_the_store(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM(
        [
            _tool_call_response("get_orchestration", {}, "c_go"),
            _tool_call_response("update_orchestration", {"auto_decompose": True}, "c_uo"),
            _final_response("ok"),
        ]
    )
    app, client = _make_app(rec, stub, tmp_path)
    client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert len(rec.recorded("get_orchestration")) >= 1
    updates = rec.recorded("update_orchestration")
    assert len(updates) == 1
    assert updates[0][0] == ({"auto_decompose": True},)
    # The knob really moved — the REST surface agrees.
    assert client.get("/api/board/orchestration").json()["auto_decompose"] is True
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


def test_split_thinking_closing_tag_only() -> None:
    """R1-style chat templates prefill the opening <think>, so the completion
    can carry only the closing tag — everything before it is reasoning."""
    thinking, visible = _split_thinking("chain of thought here</think>the reply")
    assert thinking == "chain of thought here"
    assert visible == "the reply"


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


def test_chat_serves_with_no_hermes_client(tmp_path) -> None:
    """hal0 owns the board (KB-4/#1829): with NO Hermes client wired the chat
    still runs and its board reads answer from the local store — the old
    "operator board backend not configured" precondition is gone."""
    rec = _Recorder()
    stub = _StubLLM([_tool_call_response("get_board", {}, "c_gb"), _final_response("all clear")])
    app, client = _make_app(rec, stub, tmp_path)
    assert app.state.hermes_kanban is None
    task_id = _seed_card(app.state.board_store, title="local card")
    rec.calls.clear()

    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "go"}]})
    events = _sse_events(resp.text)
    assert [e for e in events if e["type"] == "error"] == []
    result = next(e for e in events if e["type"] == "tool_result")
    assert [t["id"] for t in result["result"]["tasks"]] == [task_id]


# ── unit: _resolve_tool → (store operation, audit target) ───────────────────


class _OpSpy:
    """Captures the store call a resolved operation makes."""

    def __init__(self) -> None:
        self.call: tuple[str, tuple, dict] | None = None

    def __getattr__(self, name: str):
        def _record(*args, **kwargs):
            self.call = (name, args, kwargs)
            return {"ok": True}

        return _record


def _resolved(name: str, args: dict, board: str | None = None):
    op, target = _resolve_tool(name, args, board)
    assert op is not None
    spy = _OpSpy()
    op(spy)
    return spy.call, target


def test_resolve_tool_move_task() -> None:
    call, target = _resolved("move_task", {"task_id": "t1", "status": "done"})
    assert call == ("update_task", ("t1", {"status": "done"}), {})
    assert target == "t1"


def test_resolve_tool_remove_dependency() -> None:
    call, target = _resolved("remove_dependency", {"parent_id": "p", "child_id": "c"})
    assert call == ("remove_link", ("p", "c"), {})
    assert target == "p->c"


def test_resolve_tool_nudge_dispatcher() -> None:
    call, _target = _resolved("nudge_dispatcher", {"max": 5})
    assert call == ("dispatch_nudge", (), {"max_dispatch": 5})
    # A junk / absent max degrades to "no cap", never a crash.
    assert _resolved("nudge_dispatcher", {})[0][2] == {"max_dispatch": None}
    assert _resolved("nudge_dispatcher", {"max": "lots"})[0][2] == {"max_dispatch": None}


def test_resolve_tool_create_task_drops_none_and_threads_board() -> None:
    call, _target = _resolved("create_task", {"title": "x", "body": None}, "alpha")
    assert call == ("create_task", ({"title": "x"},), {"board": "alpha"})


def test_resolve_tool_unknown() -> None:
    assert _resolve_tool("nope", {}) == (None, None)


def test_resolve_tool_ignores_read_tools() -> None:
    """Reads are NOT mutations — they must not resolve to a store write."""
    for read in ("get_board", "get_task", "get_assignees", "get_orchestration"):
        assert _resolve_tool(read, {})[0] is None


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


# ── [brain_chat] guardrails: kill switch, read-only, config-backed knobs ─────


class _FakeRequest:
    def __init__(self, app) -> None:
        self.app = app


def _set_brain_chat_config(app, **kwargs) -> None:
    """Attach a Hal0Config carrying a [brain_chat] override to app.state."""
    app.state.hal0_config = Hal0Config(brain_chat=BrainChatConfig(**kwargs))


def test_config_accessor_defaults_when_absent(tmp_path) -> None:
    # With no hal0_config on app.state the accessor returns the SHIPPED
    # defaults — read_only=True since KB-2/3 (safe-by-default steward).
    rec = _Recorder()
    app, _client = _make_app(rec, _StubLLM([]), tmp_path)
    app.state.hal0_config = None  # the harness opts mutation tests in; undo that here
    cfg = _brain_chat_config(_FakeRequest(app))
    assert (cfg.enabled, cfg.read_only, cfg.max_rounds) == (True, True, 8)


def test_kill_switch_disables_chat_before_any_llm_call(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM([_final_response("should never run")])
    app, client = _make_app(rec, stub, tmp_path)
    _set_brain_chat_config(app, enabled=False)

    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "go"}]})
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    err = next(e for e in events if e["type"] == "error")
    assert "disabled" in err["message"]
    assert events[-1]["type"] == "done"
    # The LLM was never invoked and no board-store call went out.
    assert stub.calls == []
    assert rec.calls == []


def test_read_only_refuses_board_mutation_no_request_sent(tmp_path) -> None:
    rec = _Recorder()
    # The model tries to mutate; read-only must refuse before the PATCH.
    stub = _StubLLM(
        [
            _tool_call_response("move_task", {"task_id": "t1", "status": "done"}, "c1"),
            _final_response("ok"),
        ]
    )
    app, client = _make_app(rec, stub, tmp_path)
    _set_brain_chat_config(app, read_only=True)

    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "do"}]})
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    result = next(e for e in events if e["type"] == "tool_result")
    assert "read-only mode" in result["result"]["error"]
    # No store write ever happened, and nothing was audited.
    assert rec.recorded("update_task") == []
    assert app.state.audit.query(action="board.chat.turn") == []


def test_read_only_still_allows_reads(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM([_tool_call_response("get_board", {}, "c_gb"), _final_response("ok")])
    app, client = _make_app(rec, stub, tmp_path)
    _set_brain_chat_config(app, read_only=True)

    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert resp.status_code == 200
    # The read passed straight through — one store read, no refusal on it.
    assert len(rec.recorded("get_board")) == 1
    events = _sse_events(resp.text)
    result = next(e for e in events if e["type"] == "tool_result")
    assert "read-only mode" not in json.dumps(result["result"])


def test_max_rounds_from_config_bounds_the_loop(tmp_path) -> None:
    rec = _Recorder()
    # A pathological model that ALWAYS emits a tool call and never terminates.
    never_ends = [_tool_call_response("get_board", {}, f"c{i}") for i in range(10)]
    stub = _StubLLM(never_ends)
    app, client = _make_app(rec, stub, tmp_path)
    _set_brain_chat_config(app, max_rounds=2)

    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert resp.status_code == 200
    # The loop ran exactly max_rounds times, not the stub's 10.
    assert len(stub.calls) == 2


def test_is_read_tool_classification() -> None:
    # Board reads and non-mutating platform reads are safe.
    assert _is_read_tool("get_board", {}) is True
    assert _is_read_tool("list_slots", {}) is True
    # Board and platform mutations are not.
    assert _is_read_tool("move_task", {"task_id": "t1", "status": "done"}) is False
    assert _is_read_tool("slot_load", {"name": "img"}) is False
    # An unknown tool fails closed (treated as NOT a read).
    assert _is_read_tool("definitely_not_a_tool", {}) is False


def test_model_override_from_config_drives_target_slot(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM([_final_response("hi")])
    app, client = _make_app(rec, stub, tmp_path)
    # Operator points the steward at the NPU chat slot.
    _set_brain_chat_config(app, model="hal0/npu")

    client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert stub.calls[0]["model"] == "hal0/npu"


def test_empty_model_override_keeps_default(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM([_final_response("hi")])
    app, client = _make_app(rec, stub, tmp_path)
    _set_brain_chat_config(app, model="")  # explicit empty → persona/default

    client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert stub.calls[0]["model"] == BRAIN_SLOT_MODEL == "hal0/brain"


def test_explicit_request_model_wins_over_config_override(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM([_final_response("hi")])
    app, client = _make_app(rec, stub, tmp_path)
    _set_brain_chat_config(app, model="hal0/npu")

    client.post(
        "/api/board/chat",
        json={"model": "hal0/utility", "messages": [{"role": "user", "content": "x"}]},
    )
    assert stub.calls[0]["model"] == "hal0/utility"


class _ModelTracingLLM(_StubLLM):
    """_StubLLM that records the model of EVERY round.

    ``_StubLLM`` appends the live ``body`` dict, which the tool loop mutates in
    place across rounds — so all its entries alias the last round. Fine for the
    single-round tests above; useless for asserting per-round routing.
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        super().__init__(responses)
        self.models: list[str] = []

    async def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        self.models.append(body["model"])
        return await super().__call__(body)


def test_plain_chat_stays_on_the_brain_model(tmp_path) -> None:
    # Half of the tool_model contract: a turn that never needs a tool never
    # leaves the fast 1B. (This assertion is what the old
    # "brain routes tool turns internally now" test actually pinned — it used a
    # plain-chat response, so it never exercised the tool path at all.)
    rec = _Recorder()
    stub = _ModelTracingLLM([_final_response("hi")])
    app, client = _make_app(rec, stub, tmp_path)
    _set_brain_chat_config(app, read_only=False, model="hal0/brain", tool_model="hal0/agent")

    client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert stub.models == ["hal0/brain"]


def test_a_tool_round_routes_to_the_tool_model(tmp_path) -> None:
    # The other half, and the contract the old test asserted the INVERSE of:
    # the brain no longer "handles tool calls internally" — it can't (the ~1.1B
    # emits no parseable call on this runtime), so the tool round and every
    # continuation of it run on [brain_chat] tool_model.
    rec = _Recorder()
    stub = _ModelTracingLLM(
        [_tool_call_response("get_board", {}, "c1"), _final_response("all clear")]
    )
    app, client = _make_app(rec, stub, tmp_path)
    _set_brain_chat_config(app, read_only=False, model="hal0/brain", tool_model="hal0/agent")

    resp = client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert resp.status_code == 200
    # Round 0 decides on the brain; the round that READS the tool result runs
    # on the tool model.
    assert stub.models == ["hal0/brain", "hal0/agent"]
    events = _sse_events(resp.text)
    assert next(e for e in events if e["type"] == "tool_call")["name"] == "get_board"


def test_explicit_request_model_wins_over_tool_model(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM([_final_response("hi")])
    app, client = _make_app(rec, stub, tmp_path)
    _set_brain_chat_config(app, tool_model="hal0/agent")

    client.post(
        "/api/board/chat",
        json={"model": "hal0/utility", "messages": [{"role": "user", "content": "x"}]},
    )
    assert stub.calls[0]["model"] == "hal0/utility"


def test_no_tool_model_keeps_brain_model(tmp_path) -> None:
    rec = _Recorder()
    stub = _StubLLM([_final_response("hi")])
    app, client = _make_app(rec, stub, tmp_path)
    # "off" is the explicit no-reroute spelling. An empty string is NOT — it
    # now normalises back to the "hal0/agent" default with a warning, because a
    # bare "" on disk is indistinguishable from a key nobody set (see
    # tests/config/test_brain_tool_model_empty.py).
    _set_brain_chat_config(app, model="hal0/brain", tool_model="off")

    client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert stub.calls[0]["model"] == "hal0/brain"
