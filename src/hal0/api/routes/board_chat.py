"""Operator Board chat orchestrator — ``POST /api/board/chat`` (SSE).

SPEC §2.D: a hal0-NATIVE conversational surface that can manage the board.
hal0-api runs a client-side OpenAI tool-calling loop whose LLM is the hal0
``primary`` chat slot (reached via hal0-api's own ``/v1/chat/completions``
surface, mirroring :class:`hal0.omni_router.OmniRouter`). The toolset maps 1:1
onto the AUDITED ``/api/board/*`` mutations:

    move/assign · create · comment · dep add/remove · block · specify ·
    decompose · nudge

Every tool the LLM calls is dispatched through the SAME audited mutation path
the REST handlers use (the :class:`HermesKanbanClient` + a ``board.chat.turn``
audit row per call), so a chat-driven mutation surfaces on the board LIVE via
the kanban events WS — chat is NOT the board transport.

Transport contract (kept stable so the LLM backend can later swap to the
Hermes agent via chat_proxy WITHOUT any UI change):

    SSE events, one JSON object per ``data:`` line:
      {"type": "token",  "text": "..."}            assistant token delta
      {"type": "tool_call",   "name": "...", "arguments": {...}, "id": "..."}
      {"type": "tool_result", "name": "...", "id": "...", "result": {...}}
      {"type": "done"}                              end of turn
      {"type": "error", "message": "..."}           fatal error

The LLM backend is injected as ``app.state.board_chat_llm`` — an async callable
``(body: dict) -> dict`` returning an OpenAI chat-completion response. Tests
inject a STUB to assert the tool loop drives the right board mutations. In
production it is wired to hal0-api's ``/v1/chat/completions`` against the
``primary`` slot.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import structlog
from fastapi import Request
from fastapi.responses import StreamingResponse

from hal0.api._audit import record_action

log = structlog.get_logger(__name__)

# Loop budget — terminate even against a pathological LLM emitting tool_calls
# forever (mirrors OmniRouter._MAX_LOOP_ROUNDS).
_MAX_ROUNDS = 8

# The slot the orchestrator drives. Points at the `agent` slot — the
# tool-calling orchestrator model — rather than the conversational `chat`
# slot (hal0/primary): board chat IS an agentic surface (it drives audited
# board mutations via tool-calls), so the agent model is the correct brain.
# (Named PRIMARY_SLOT_MODEL for back-compat; resolves via the hal0/ alias map.)
PRIMARY_SLOT_MODEL = "hal0/agent"

#: LLM backend signature: an OpenAI chat-completion request body in, the
#: parsed response dict out.
LlmFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


# ── tool definitions (1:1 with the audited board mutations) ─────────────────


def _fn(name: str, desc: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        },
    }


def _tool_schemas() -> list[dict[str, Any]]:
    """OpenAI ``tools`` array advertised to the LLM."""
    return [
        _fn(
            "move_task",
            "Move a task to a different lane / status (drag-drop equivalent).",
            {
                "task_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": [
                        "triage",
                        "todo",
                        "scheduled",
                        "ready",
                        "running",
                        "blocked",
                        "review",
                        "done",
                        "archived",
                    ],
                },
            },
            ["task_id", "status"],
        ),
        _fn(
            "assign_task",
            "Assign a task to a profile (assignee).",
            {"task_id": {"type": "string"}, "assignee": {"type": "string"}},
            ["task_id", "assignee"],
        ),
        _fn(
            "create_task",
            "Create a new task on the board.",
            {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "assignee": {"type": "string"},
                "priority": {"type": "integer"},
                "triage": {"type": "boolean"},
            },
            ["title"],
        ),
        _fn(
            "comment_task",
            "Add a comment to a task.",
            {"task_id": {"type": "string"}, "body": {"type": "string"}},
            ["task_id", "body"],
        ),
        _fn(
            "add_dependency",
            "Add a parent→child dependency link between two tasks.",
            {"parent_id": {"type": "string"}, "child_id": {"type": "string"}},
            ["parent_id", "child_id"],
        ),
        _fn(
            "remove_dependency",
            "Remove a parent→child dependency link.",
            {"parent_id": {"type": "string"}, "child_id": {"type": "string"}},
            ["parent_id", "child_id"],
        ),
        _fn(
            "block_task",
            "Move a task to the blocked lane with a reason.",
            {"task_id": {"type": "string"}, "block_reason": {"type": "string"}},
            ["task_id"],
        ),
        _fn(
            "specify_task",
            "Run the LLM 'specify' action to flesh out a triage task.",
            {"task_id": {"type": "string"}},
            ["task_id"],
        ),
        _fn(
            "decompose_task",
            "Run the LLM 'decompose' action to fan a task into children.",
            {"task_id": {"type": "string"}},
            ["task_id"],
        ),
        _fn(
            "nudge_dispatcher",
            "Nudge the dispatcher to run one tick (max N spawns).",
            {"max": {"type": "integer"}},
            [],
        ),
    ]


# ── tool dispatch → audited board mutation ──────────────────────────────────


def _resolve_tool(
    name: str, args: dict[str, Any]
) -> tuple[str | None, str, dict[str, Any], Any, str | None]:
    """Map a tool name + args → (method, upstream path, query params, body, target).

    Query params and body mirror the REST handlers / upstream contract exactly:
    ``DELETE /links`` and ``POST /dispatch?max=N`` take their args as QUERY
    params upstream; everything else takes a JSON body.
    """
    if name == "move_task":
        tid = args.get("task_id", "")
        return "PATCH", f"/tasks/{tid}", {}, {"status": args.get("status")}, tid
    if name == "assign_task":
        tid = args.get("task_id", "")
        return "PATCH", f"/tasks/{tid}", {}, {"assignee": args.get("assignee")}, tid
    if name == "create_task":
        body = {k: v for k, v in args.items() if v is not None}
        return "POST", "/tasks", {}, body, None
    if name == "comment_task":
        tid = args.get("task_id", "")
        return "POST", f"/tasks/{tid}/comments", {}, {"body": args.get("body")}, tid
    if name == "add_dependency":
        body = {"parent_id": args.get("parent_id"), "child_id": args.get("child_id")}
        return "POST", "/links", {}, body, f"{body['parent_id']}->{body['child_id']}"
    if name == "remove_dependency":
        # DELETE /links takes parent_id/child_id as QUERY params upstream
        # (SPEC §4) — matches the REST handler.
        params = {"parent_id": args.get("parent_id"), "child_id": args.get("child_id")}
        return "DELETE", "/links", params, None, f"{params['parent_id']}->{params['child_id']}"
    if name == "block_task":
        tid = args.get("task_id", "")
        patch: dict[str, Any] = {"status": "blocked"}
        if args.get("block_reason"):
            patch["block_reason"] = args["block_reason"]
        return "PATCH", f"/tasks/{tid}", {}, patch, tid
    if name == "specify_task":
        tid = args.get("task_id", "")
        return "POST", f"/tasks/{tid}/specify", {}, {}, tid
    if name == "decompose_task":
        tid = args.get("task_id", "")
        return "POST", f"/tasks/{tid}/decompose", {}, {}, tid
    if name == "nudge_dispatcher":
        # POST /dispatch?max=N — max is a QUERY param upstream (SPEC §4).
        params = {}
        if args.get("max") is not None:
            params["max"] = args["max"]
        return "POST", "/dispatch", params, {}, None
    return None, "", {}, None, None


async def _dispatch_tool(
    request: Request,
    client: Any,
    name: str,
    args: dict[str, Any],
    *,
    board: str | None,
) -> Any:
    """Run one board tool through the audited mutation path.

    Returns the upstream JSON result (or an ``{"error": ...}`` envelope the
    loop can keep stepping against). Each call writes a ``board.chat.turn``
    audit row with ``rec.after`` = result.
    """
    method, path, tool_params, body, target = _resolve_tool(name, args)
    if method is None:
        return {"error": f"unknown tool: {name}"}

    # Merge the board scope with any tool-specific query params (e.g.
    # DELETE /links parent_id/child_id, POST /dispatch max=N — these ride as
    # QUERY upstream, matching the REST handlers).
    params: dict[str, Any] = dict(tool_params)
    if board:
        params["board"] = board
    params = params or None  # type: ignore[assignment]
    agent = request.headers.get("X-hal0-Agent")
    async with record_action(
        request,
        category="board",
        action="board.chat.turn",
        target=target,
        message=f"chat:{name}",
    ) as rec:
        try:
            result = await client.request_json(
                method, path, params=params, json_body=body, agent_id=agent
            )
        except Exception as exc:
            # Surface as a tool_result the LLM can react to; still recorded as
            # an error audit row (record_action re-raises, so set after first
            # so the row is informative).
            rec.after = {"error": str(exc)}
            raise
        rec.after = result if isinstance(result, dict) else {"result": result}
        return result


# ── LLM backend resolution ──────────────────────────────────────────────────


def _resolve_llm(request: Request) -> LlmFn:
    """Return the injected LLM backend, or the default primary-slot caller.

    Tests inject ``app.state.board_chat_llm``. Production falls back to a
    closure that POSTs hal0-api's own ``/v1/chat/completions`` against the
    ``primary`` slot (re-entering the full dispatch chain).
    """
    injected = getattr(request.app.state, "board_chat_llm", None)
    if injected is not None:
        return injected

    base_url = getattr(request.app.state, "self_api_base_url", "http://127.0.0.1:8080")

    async def _primary_completion(body: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=300.0) as http:
            try:
                resp = await http.post(f"{base_url.rstrip('/')}/v1/chat/completions", json=body)
            except httpx.HTTPError as exc:
                return {"error": f"primary slot transport failure: {exc}"}
        if not (200 <= resp.status_code < 300):
            return {"error": f"primary slot HTTP {resp.status_code}: {resp.text[:300]}"}
        try:
            return resp.json()
        except ValueError:
            return {"error": "primary slot returned non-JSON"}

    return _primary_completion


# ── SSE framing helpers ─────────────────────────────────────────────────────


def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _extract_tool_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull + normalise tool_calls (arguments → dict) from a completion."""
    choices = response.get("choices") or []
    if not choices:
        return []
    msg = choices[0].get("message") or {}
    out: list[dict[str, Any]] = []
    for tc in msg.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments")
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
                if not isinstance(args, dict):
                    args = {}
            except ValueError:
                args = {}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            args = {}
        out.append({"id": tc.get("id", ""), "name": fn.get("name", ""), "arguments": args})
    return out


def _assistant_text(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    return content if isinstance(content, str) else ""


def _assistant_message(response: dict[str, Any]) -> dict[str, Any] | None:
    choices = response.get("choices") or []
    if not choices:
        return None
    msg = choices[0].get("message")
    return msg if isinstance(msg, dict) else None


# ── the loop ────────────────────────────────────────────────────────────────


async def _chat_stream(request: Request, payload: dict[str, Any]) -> AsyncIterator[str]:
    """Run the tool-calling loop, yielding SSE frames."""
    client = getattr(request.app.state, "hermes_kanban", None)
    if client is None:
        yield _sse({"type": "error", "message": "operator board backend not configured"})
        yield _sse({"type": "done"})
        return

    llm = _resolve_llm(request)
    board = payload.get("board")
    messages: list[dict[str, Any]] = list(payload.get("messages") or [])

    body: dict[str, Any] = {
        "model": payload.get("model") or PRIMARY_SLOT_MODEL,
        "messages": messages,
        "tools": _tool_schemas(),
        "stream": False,
    }

    try:
        for _round in range(_MAX_ROUNDS):
            response = await llm(body)
            if isinstance(response, dict) and response.get("error"):
                yield _sse({"type": "error", "message": str(response["error"])})
                yield _sse({"type": "done"})
                return

            text = _assistant_text(response)
            if text:
                yield _sse({"type": "token", "text": text})

            tool_calls = _extract_tool_calls(response)
            if not tool_calls:
                yield _sse({"type": "done"})
                return

            assistant_msg = _assistant_message(response)
            if assistant_msg is not None:
                messages.append(assistant_msg)

            for tc in tool_calls:
                yield _sse(
                    {
                        "type": "tool_call",
                        "id": tc["id"],
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                    }
                )
                try:
                    result = await _dispatch_tool(
                        request, client, tc["name"], tc["arguments"], board=board
                    )
                except Exception as exc:  # mutation failed — audited as error
                    result = {"error": str(exc)}
                yield _sse(
                    {"type": "tool_result", "id": tc["id"], "name": tc["name"], "result": result}
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": tc["name"],
                        "content": json.dumps(result),
                    }
                )
            body["messages"] = messages

        # Budget exhausted.
        yield _sse({"type": "error", "message": "chat loop budget exhausted"})
        yield _sse({"type": "done"})
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("hal0.board_chat.loop_error", error=str(exc))
        yield _sse({"type": "error", "message": str(exc)})
        yield _sse({"type": "done"})


async def run_board_chat(request: Request) -> StreamingResponse:
    """Entry point invoked by the ``/api/board/chat`` route."""
    raw = await request.body()
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return StreamingResponse(_chat_stream(request, payload), media_type="text/event-stream")


__all__ = [
    "PRIMARY_SLOT_MODEL",
    "_chat_stream",
    "_dispatch_tool",
    "_resolve_tool",
    "_tool_schemas",
    "run_board_chat",
]
