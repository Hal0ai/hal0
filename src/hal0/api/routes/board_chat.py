"""Platform assistant chat orchestrator — ``POST /api/board/chat`` (SSE).

SPEC §2.D grown into the platform surface: a hal0-NATIVE conversational agent
that administers the whole instance, not just the board. hal0-api runs a
client-side OpenAI tool-calling loop whose LLM is the hal0 ``agent`` slot
(reached via hal0-api's own ``/v1/chat/completions`` surface, mirroring
:class:`hal0.omni_router.OmniRouter`). Three tool families:

* **Board reads** (unaudited, via :class:`HermesKanbanClient` — mirrors the
  REST proxy's allowlisted GET rows): get_board · get_task · get_assignees ·
  get_orchestration.
* **Board mutations** (each an AUDITED ``board.chat.turn`` row through the
  same client the REST handlers use): move/assign · create · comment ·
  dep add/remove · block · specify · decompose · nudge · orchestration update.
* **Platform tools** (hal0-api's OWN surface, self-HTTP against
  ``app.state.self_api_base_url``): slots list/get/load/unload/restart,
  models, hardware stats, installed agents. Reads unaudited; slot mutations
  write ``platform.chat.turn`` rows.

A chat-driven board mutation surfaces on the board LIVE via the kanban events
WS — chat is NOT the board transport.

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
# slot (hal0/chat): board chat IS an agentic surface (it drives audited
# board mutations via tool-calls), so the agent model is the correct brain.
# (Named PRIMARY_SLOT_MODEL for back-compat; resolves via the resolver chains.)
PRIMARY_SLOT_MODEL = "hal0/agent"

# Injected as the leading system message when the client doesn't send one.
# Without it the LLM had ten write tools, no read tools, and no idea what the
# lanes mean — it could mutate the board but never see it, so "what's
# blocked?" or "move the auth task to review" was unanswerable.
_SYSTEM_PROMPT = """\
You are the hal0 platform assistant: a terse operations agent that
administers this hal0 instance via tools — the Operator Board (kanban),
inference slots, models, agent profiles, and orchestration settings.

Surfaces:
- Board: lanes (the `status` values) are triage (new, awaiting spec), todo
  (specified and queued), scheduled (time-triggered), ready (claimed for
  dispatch), running (worker active), blocked (waiting on input; carries
  block_reason), review (needs operator sign-off), done, archived.
- Slots: list_slots / get_slot report each inference slot's state (serving,
  ready, warming, idle, offline, error), model and throughput;
  slot_load / slot_unload / slot_restart change them.
- Catalogue: list_models (model library), list_agents (installed platform
  agents), hardware_stats (RAM/VRAM/GTT, GPU util, NPU status).
- Settings: get_orchestration / update_orchestration (board dispatcher knobs:
  orchestrator_profile, default_assignee, auto_decompose,
  auto_promote_children).

Rules:
- You cannot see state unless you look. Call the matching read tool first and
  resolve exact task ids / slot names before mutating. Never guess ids.
- Board mutations are audited and land on the live board immediately.
- Slot load/unload/restart are DISRUPTIVE (they can evict models and drop
  in-flight requests): do them only when the operator explicitly asks, and
  state what you did.
- Prefer a clarifying question over a destructive guess. Keep replies short.
"""

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
    """OpenAI ``tools`` array advertised to the LLM (reads first, then writes)."""
    return [
        _fn(
            "get_board",
            "Read the whole board: every task's id/title/status/assignee/"
            "priority. Call this FIRST to resolve task ids before mutating.",
            {},
            [],
        ),
        _fn(
            "get_task",
            "Read one task in full: body, comments, events, runs, links.",
            {"task_id": {"type": "string"}},
            ["task_id"],
        ),
        _fn(
            "get_assignees",
            "List the assignable actors (agent profiles) on this board.",
            {},
            [],
        ),
        _fn(
            "get_orchestration",
            "Read the board dispatcher settings (orchestrator_profile, "
            "default_assignee, auto_decompose, auto_promote_children).",
            {},
            [],
        ),
        _fn(
            "update_orchestration",
            "Update the board dispatcher settings. Only pass the knobs to change.",
            {
                "orchestrator_profile": {"type": "string"},
                "default_assignee": {"type": "string"},
                "auto_decompose": {"type": "boolean"},
                "auto_promote_children": {"type": "boolean"},
            },
            [],
        ),
        _fn(
            "list_slots",
            "List the inference slots: name, state (serving/ready/warming/idle/"
            "offline/error), model, backend, throughput.",
            {},
            [],
        ),
        _fn(
            "get_slot",
            "Read one inference slot in detail.",
            {"name": {"type": "string"}},
            ["name"],
        ),
        _fn(
            "slot_load",
            "Load a slot's model into memory. DISRUPTIVE: may evict other "
            "tenants — only on explicit operator request.",
            {"name": {"type": "string"}},
            ["name"],
        ),
        _fn(
            "slot_unload",
            "Unload a slot's model from memory. DISRUPTIVE — only on explicit "
            "operator request.",
            {"name": {"type": "string"}},
            ["name"],
        ),
        _fn(
            "slot_restart",
            "Restart a slot's backend process. DISRUPTIVE: drops in-flight "
            "requests — only on explicit operator request.",
            {"name": {"type": "string"}},
            ["name"],
        ),
        _fn(
            "list_models",
            "List the model library (installed + known models).",
            {},
            [],
        ),
        _fn(
            "hardware_stats",
            "Read hardware utilisation: RAM/VRAM/GTT, GPU util, NPU status.",
            {},
            [],
        ),
        _fn(
            "list_agents",
            "List the installed platform agents (e.g. hermes, pi-coder).",
            {},
            [],
        ),
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


# ── read tools (allowlisted GETs — mirror the UNAUDITED REST read rows) ─────


def _resolve_read_tool(name: str, args: dict[str, Any]) -> tuple[str | None, str]:
    """Map a read tool → (method, upstream sub-path); (None, "") if not a read.

    These hit the same allowlisted GET paths the table-driven REST proxy
    forwards, and like those reads they write NO audit rows.
    """
    if name == "get_board":
        return "GET", "/board"
    if name == "get_task":
        return "GET", f"/tasks/{args.get('task_id', '')}"
    if name == "get_assignees":
        return "GET", "/assignees"
    if name == "get_orchestration":
        return "GET", "/orchestration"
    return None, ""


# Fields kept when compacting a board row for the tool result. Full rows carry
# bodies/summaries per task; a big board would blow the loop's context budget.
_COMPACT_TASK_FIELDS = (
    "id",
    "title",
    "status",
    "assignee",
    "profile",
    "priority",
    "block_reason",
    "tenant",
)


def _compact_board(result: Any) -> Any:
    """Trim a GET /board payload to compact task rows.

    Accepts the same four wire shapes the dashboard normaliser handles —
    ``{columns:[{tasks}]}`` (what Hermes emits), ``{lanes:{status:[...]}}``,
    ``{tasks:[...]}``, or a bare list — and returns ``{"tasks": [...]}``.
    Anything unrecognised passes through untouched.
    """
    rows: list[dict[str, Any]] = []
    if isinstance(result, dict):
        if isinstance(result.get("columns"), list):
            for col in result["columns"]:
                if isinstance(col, dict) and isinstance(col.get("tasks"), list):
                    rows.extend(t for t in col["tasks"] if isinstance(t, dict))
        elif isinstance(result.get("lanes"), dict):
            for tasks in result["lanes"].values():
                if isinstance(tasks, list):
                    rows.extend(t for t in tasks if isinstance(t, dict))
        elif isinstance(result.get("tasks"), list):
            rows = [t for t in result["tasks"] if isinstance(t, dict)]
        else:
            return result
    elif isinstance(result, list):
        rows = [t for t in result if isinstance(t, dict)]
    else:
        return result
    return {
        "tasks": [
            {k: t[k] for k in _COMPACT_TASK_FIELDS if t.get(k) is not None} for t in rows
        ]
    }


# ── platform tools (hal0-api's OWN surface, via self-HTTP) ──────────────────
#
# These do not touch Hermes: they call hal0-api's existing slot/model/agent
# routes on the same instance (``app.state.self_api_base_url``), re-entering
# the normal dispatch chain. Tests inject ``app.state.platform_http`` (an
# httpx.AsyncClient over a MockTransport); production builds one per call.

_PLATFORM_READS = {
    "list_slots": "/api/slots",
    "list_models": "/api/models",
    "hardware_stats": "/api/stats/hardware",
    "list_agents": "/api/agents",
}

# Disruptive slot verbs — each is audited as a ``platform.chat.turn`` row.
_SLOT_MUTATIONS = {"slot_load": "load", "slot_unload": "unload", "slot_restart": "restart"}


def _resolve_platform_tool(
    name: str, args: dict[str, Any]
) -> tuple[str | None, str, bool]:
    """Map a platform tool → (method, hal0-api path, mutating)."""
    if name in _PLATFORM_READS:
        return "GET", _PLATFORM_READS[name], False
    if name == "get_slot":
        return "GET", f"/api/slots/{args.get('name', '')}", False
    if name in _SLOT_MUTATIONS:
        return "POST", f"/api/slots/{args.get('name', '')}/{_SLOT_MUTATIONS[name]}", True
    return None, "", False


async def _platform_request(http: httpx.AsyncClient, method: str, path: str) -> Any:
    """One self-HTTP call, mapped to a tool-result the loop can step against."""
    try:
        resp = await http.request(method, path)
    except httpx.HTTPError as exc:
        return {"error": f"platform API unreachable: {exc}"}
    if resp.status_code >= 400:
        return {"error": f"platform API HTTP {resp.status_code}: {resp.text[:300]}"}
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text[:500]}


async def _dispatch_platform_tool(
    request: Request,
    name: str,
    args: dict[str, Any],
    *,
    method: str,
    path: str,
    mutating: bool,
) -> Any:
    """Run one platform tool; slot mutations write a ``platform.chat.turn`` row."""
    http = getattr(request.app.state, "platform_http", None)
    owns = http is None
    if owns:
        base = getattr(request.app.state, "self_api_base_url", "http://127.0.0.1:8080")
        http = httpx.AsyncClient(base_url=base.rstrip("/"), timeout=60.0)
    try:
        if not mutating:
            return await _platform_request(http, method, path)
        async with record_action(
            request,
            category="platform",
            action="platform.chat.turn",
            target=args.get("name"),
            message=f"chat:{name}",
        ) as rec:
            result = await _platform_request(http, method, path)
            rec.after = result if isinstance(result, dict) else {"result": result}
            return result
    finally:
        if owns:
            await http.aclose()


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
    if name == "update_orchestration":
        # PUT /orchestration — partial update, only the knobs passed.
        body = {k: v for k, v in args.items() if v is not None}
        return "PUT", "/orchestration", {}, body, None
    return None, "", {}, None, None


async def _dispatch_tool(
    request: Request,
    client: Any,
    name: str,
    args: dict[str, Any],
    *,
    board: str | None,
) -> Any:
    """Run one board tool: reads pass straight through, mutations are audited.

    Returns the upstream JSON result (or an ``{"error": ...}`` envelope the
    loop can keep stepping against). Each MUTATION writes a ``board.chat.turn``
    audit row with ``rec.after`` = result; reads write none — matching the
    REST proxy's audited-mutations / unaudited-reads split.
    """
    read_method, read_path = _resolve_read_tool(name, args)
    if read_method is not None:
        params = {"board": board} if board else None
        result = await client.request_json(
            read_method,
            read_path,
            params=params,
            agent_id=request.headers.get("X-hal0-Agent"),
        )
        return _compact_board(result) if name == "get_board" else result

    p_method, p_path, p_mutating = _resolve_platform_tool(name, args)
    if p_method is not None:
        return await _dispatch_platform_tool(
            request, name, args, method=p_method, path=p_path, mutating=p_mutating
        )

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
    # Seed the system prompt unless the client sent its own — the model needs
    # the lane vocabulary and the read-before-write rule to act on the board.
    if not messages or not (
        isinstance(messages[0], dict) and messages[0].get("role") == "system"
    ):
        messages.insert(0, {"role": "system", "content": _SYSTEM_PROMPT})

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
    "_compact_board",
    "_dispatch_platform_tool",
    "_dispatch_tool",
    "_resolve_platform_tool",
    "_resolve_read_tool",
    "_resolve_tool",
    "_tool_schemas",
    "run_board_chat",
]
