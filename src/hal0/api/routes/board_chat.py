"""Platform assistant chat orchestrator — ``POST /api/board/chat`` (SSE).

SPEC §2.D grown into the platform surface: a hal0-NATIVE conversational agent
that administers the whole instance, not just the board. The surface embodies
the ``hal0-brain`` profile (separate memory namespace, hal0-heavy context;
persona TOML overrides the built-in prompt/model when present) and hal0-api
runs a client-side OpenAI tool-calling loop whose LLM is the hal0 ``brain``
slot, falling back to ``agent`` via the resolver chain (reached via hal0-api's
own ``/v1/chat/completions`` surface, mirroring
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
      {"type": "thinking", "text": "..."}           model reasoning (never shown inline)
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

import asyncio
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import structlog
from fastapi import Request
from fastapi.responses import StreamingResponse

from hal0.api._audit import record_action

log = structlog.get_logger(__name__)

# Loop budget and per-round transport timeout live in ``[brain_chat]``
# (max_rounds / completion_timeout_s), read per-request via
# _brain_chat_config(); the BrainChatConfig defaults (8 rounds, 300 s) are the
# single source of truth.

# Gated-tool pause. When a call parks on the ApprovalQueue the turn WAITS
# for the operator's decision instead of racing ahead — otherwise the model
# ends the turn with "let me know when approved" and the executed result
# lands nowhere, breaking the flow. Pings keep the SSE stream alive through
# proxies while paused; on timeout the turn continues with the pending
# result (the operator can still approve later via the bell).
_APPROVAL_WAIT_S = 300.0
_APPROVAL_POLL_S = 1.0
_APPROVAL_PING_EVERY_S = 15.0

# Per-round completion cap. The request is non-streaming with a bounded
# transport timeout ([brain_chat] completion_timeout_s), so an uncapped
# runaway generation (observed: ~25k tokens at ~84 tok/s on the agent-slot
# fallback) burns the whole window and surfaces as "primary slot transport
# failure" before the turn can emit a single tool call. 4096 tokens ≈ 50 s
# worst-case on the slowest resident model — plenty for a steward reply +
# tool calls.
_MAX_COMPLETION_TOKENS = 4096

# The slot the steward drives. Points at the dedicated `brain` slot (the
# hal0-brain profile's default model) — the resolver's generalized chain
# (`hal0/<slot>` → (<slot>, agent)) falls back to the `agent` slot when no
# brain slot is loaded, so the chat degrades to the orchestrator model
# instead of failing. (PRIMARY_SLOT_MODEL kept as a back-compat alias.)
BRAIN_SLOT_MODEL = "hal0/brain"
PRIMARY_SLOT_MODEL = BRAIN_SLOT_MODEL

# The agent profile (persona) this surface embodies. When a persona TOML with
# this id exists in the personas store, its system prompt / preferred model
# override the built-in defaults below — the operator edits ONE file and the
# slide-out chat follows.
BRAIN_PERSONA_ID = "hal0-brain"

# Injected as the leading system message when the client doesn't send one.
# Without it the LLM had ten write tools, no read tools, and no idea what the
# lanes mean — it could mutate the board but never see it, so "what's
# blocked?" or "move the auth task to review" was unanswerable.
_SYSTEM_PROMPT = """\
You are hal0-brain: the resident platform steward of this hal0 home AI box.
You run as the dashboard's agent chat and administer the whole instance via
tools — inference slots, the model library, benchmarks, hardware, the
Operator Board (kanban), and orchestration settings. You keep your own
memory, separate from the Hermes chat agent (namespace private:hal0-brain).

hal0 context:
- A *slot* is a named inference unit (systemd hal0-slot@<name>) binding one
  model to a backend (llama.cpp Vulkan/ROCm, FLM on the NPU) with device,
  context-length and throughput settings. Canonical slots: `agent` (the
  tool-calling anchor every fallback chain ends in), `brain` (you),
  `utility` (cheap helper), `npu` (NPU chat edge). Any enabled slot X is
  addressable as the virtual model `hal0/X` via /v1/chat/completions.
- Setting up a model means: find it in the library (list_models), download
  the artifact (GGUF or FLM package), then bind it to a slot and load it.
  Walk the operator through it step by step and confirm before each
  disruptive move.
- Benchmarking: hal0-bench drives a model on a slot and records throughput/
  latency runs (dash → Benchmarks). You can prepare the slot (right model
  loaded, others unloaded on request) and read hardware_stats to sanity-check
  headroom before a run.
- Board: lanes (the `status` values) are triage (new, awaiting spec), todo
  (specified and queued), scheduled (time-triggered), ready (claimed for
  dispatch), running (worker active), blocked (waiting on input; carries
  block_reason), review (needs operator sign-off), done, archived.

Tool surfaces:
- Slots: list_slots / get_slot report each slot's state (serving, ready,
  warming, idle, offline, error), model and throughput; slot_load /
  slot_unload / slot_restart change them.
- Catalogue: list_models (model library), list_agents (installed platform
  agents), hardware_stats (RAM/VRAM/GTT, GPU util, NPU status).
- Board reads + audited mutations (get_board, move/assign/create/comment,
  dependencies, specify/decompose, nudge_dispatcher).
- Settings: get_orchestration / update_orchestration (board dispatcher knobs:
  orchestrator_profile, default_assignee, auto_decompose,
  auto_promote_children).
- Full platform admin (hal0-admin catalog): model inspect/download/register/
  organize (pulls always land in the operator's configured model store —
  check model_store first), profile and stack CRUD, slot create/edit/delete,
  hal0 settings, benchmarks, logs. Tools marked (gated) do NOT run
  immediately: they return status=pending_approval with an approval_id and
  execute only after the operator approves them in the dashboard's Approvals
  panel — when that happens, say what you queued and why, then wait.

Rules:
- You cannot see state unless you look. Call the matching read tool first and
  resolve exact task ids / slot names before mutating. Never guess ids.
- Board mutations are audited and land on the live board immediately.
- Slot load/unload/restart are DISRUPTIVE (they can evict models and drop
  in-flight requests): do them only when the operator explicitly asks, and
  state what you did.
- For multi-step work (create a slot, set up a model, run a benchmark) lay
  out the short plan first, then execute step by step, reporting progress.
- Prefer a clarifying question over a destructive guess. Keep replies short.
"""


def _resolve_profile(request: Request) -> tuple[str, str]:
    """Resolve (system_prompt, default_model) from the hal0-brain profile.

    Reads the ``hal0-brain`` persona TOML from the personas store (root
    overridable via ``app.state.brain_persona_root`` for tests). A missing or
    malformed persona falls back to the built-in ``_SYSTEM_PROMPT`` /
    ``BRAIN_SLOT_MODEL`` so the chat never breaks on a half-provisioned box.
    """
    from hal0.agents.personas import PersonaError, load_persona

    root = getattr(request.app.state, "brain_persona_root", None)
    try:
        persona = load_persona(BRAIN_PERSONA_ID, root=root)
    except (FileNotFoundError, PersonaError, OSError):
        return _SYSTEM_PROMPT, BRAIN_SLOT_MODEL
    prompt = persona.system_prompt.strip() or _SYSTEM_PROMPT
    model = persona.preferred_model.strip() or BRAIN_SLOT_MODEL
    return prompt, model


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
            "Unload a slot's model from memory. DISRUPTIVE — only on explicit operator request.",
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


# ── admin-MCP tool surface (the platform steward's hands) ───────────────────
#
# The slide-out chat embodies the hal0-brain persona, whose remit is the
# whole platform — not just the board. Rather than hand-maintaining a
# second tool table here, the chat surfaces the hal0-admin MCP catalog
# (hal0.mcp.admin.TOOL_DESCRIPTIONS) as OpenAI tool schemas and routes
# calls through the SAME ``dispatch`` core the /mcp/admin mount uses:
# identical read/write/gated classification, the lifespan-scoped
# ApprovalQueue (gated tools come back ``pending_approval`` and execute
# only after the operator approves), and the same audit rows.
#
# Locals win on collision: the chat's own board tools and platform verbs
# (slot_load/slot_unload/slot_restart, compact list_slots/list_models
# reads) are purpose-tuned and already pinned by tests, so their admin
# twins are excluded from the surfaced schemas. Imports stay lazy — the
# ``mcp`` SDK is an optional dependency, and a box without it must still
# serve the board-only chat (schemas degrade to the local list).

_ADMIN_TOOL_EXCLUDES: frozenset[str] = frozenset(
    {
        # exact name collisions with the local platform verbs
        "slot_load",
        "slot_unload",
        "slot_restart",
        # semantic duplicates of the local compact reads
        "slot_list",
        "slot_status",
        "model_list",
        "hardware_probe",
        # memory_* ride the persona's own namespace (private:hal0-brain)
        # via Hindsight, not the agent memory engine's MCP dispatcher
        "memory_add",
        "memory_search",
        "memory_list",
        "memory_delete",
    }
)


def _admin_tool_names() -> frozenset[str]:
    """The admin catalog minus exclusions; empty when the SDK is absent."""
    try:
        from hal0.mcp.admin import (
            AUTONOMOUS_READ_TOOLS,
            AUTONOMOUS_WRITE_TOOLS,
            GATED_TOOLS,
        )
    except ImportError:
        return frozenset()
    return (AUTONOMOUS_READ_TOOLS | AUTONOMOUS_WRITE_TOOLS | GATED_TOOLS) - _ADMIN_TOOL_EXCLUDES


def _brain_tool_policy(request: Request) -> Any | None:
    """The hal0-brain persona's :class:`ToolPolicy` overlay, or ``None``.

    Loaded per request from the persona TOML (same store + fallback
    semantics as :func:`_resolve_profile`): missing/malformed persona or
    absent mcp SDK → ``None`` → the server classification stands. This
    is what makes the persona's ``tools_allowed`` / ``[persona.approval]``
    tables ENFORCED on the sidebar surface rather than decorative — the
    operator edits one file to hide tools, tighten an autonomous tool
    behind approval, or grant standing approval to a gated one
    (destructive tools excepted; see admin.POLICY_NO_LOOSEN).
    """
    try:
        from hal0.agents.personas import PersonaError, load_persona
        from hal0.mcp.admin import ToolPolicy
    except ImportError:
        return None
    root = getattr(request.app.state, "brain_persona_root", None)
    try:
        persona = load_persona(BRAIN_PERSONA_ID, root=root)
    except (FileNotFoundError, PersonaError, OSError):
        return None
    return ToolPolicy.from_persona(persona)


def _admin_tool_schemas(policy: Any | None = None) -> list[dict[str, Any]]:
    """OpenAI tool schemas for the surfaced admin catalog.

    The per-tool ``parameters`` come straight from
    :func:`hal0.mcp.admin.tool_param_schema` — the SAME schema the MCP
    server advertises (path args as required strings, curated body-field
    hints, ``additionalProperties`` open). Keeping the builder in
    ``admin`` means a hint authored once reaches both surfaces and can't
    drift. A ``policy`` narrows the surface to its ``tools_allowed`` globs
    — hidden tools never reach the LLM's tool list (dispatch still refuses
    them if guessed).
    """
    try:
        from hal0.mcp.admin import TOOL_DESCRIPTIONS, tool_param_schema
    except ImportError:
        return []
    schemas: list[dict[str, Any]] = []
    for name, description in TOOL_DESCRIPTIONS.items():
        if name in _ADMIN_TOOL_EXCLUDES:
            continue
        if policy is not None and not policy.allows(name):
            continue
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": tool_param_schema(name),
                },
            }
        )
    return schemas


def _surfaced_tool_schemas(request: Request) -> list[dict[str, Any]]:
    """The combined tool list the LLM sees, persona-policy filtered.

    Local board/platform schemas plus the admin catalog, both narrowed
    to the hal0-brain persona's ``tools_allowed`` globs when a policy
    resolves (no persona / default ``["*"]`` → everything surfaces).
    """
    policy = _brain_tool_policy(request)
    local = _tool_schemas()
    if policy is not None:
        local = [s for s in local if policy.allows(s["function"]["name"])]
    return local + _admin_tool_schemas(policy)


async def _dispatch_admin_tool(request: Request, name: str, args: dict[str, Any]) -> Any:
    """Route one admin-catalog call through the MCP dispatch core.

    Reuses the lifespan-scoped ApprovalQueue so a gated tool queued from
    the chat lands in the same dashboard Approvals panel as one queued
    over /mcp/admin, and the audit trail records the persona as the
    calling client.
    """
    try:
        from hal0.mcp import admin
    except ImportError:
        return {"error": f"{name}: admin tools unavailable (mcp SDK not installed)"}
    queue = getattr(request.app.state, "approval_queue", None)
    if queue is None:
        return {"error": f"{name}: admin tools unavailable (no approval queue)"}
    bearer = request.headers.get("Authorization", "").removeprefix("Bearer ").strip() or None
    base = getattr(request.app.state, "self_api_base_url", "http://127.0.0.1:8080")
    return await admin.dispatch(
        tool=name,
        args=args,
        client_id=BRAIN_PERSONA_ID,
        bearer=bearer,
        base_url=base,
        approval_queue=queue,
        memory_dispatcher=getattr(request.app.state, "memory_dispatcher", None),
        policy=_brain_tool_policy(request),
    )


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
        "tasks": [{k: t[k] for k in _COMPACT_TASK_FIELDS if t.get(k) is not None} for t in rows]
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


def _resolve_platform_tool(name: str, args: dict[str, Any]) -> tuple[str | None, str, bool]:
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


def _brain_chat_config(request: Request) -> Any:
    """The ``[brain_chat]`` config off app.state, or defaults.

    Falls back to a fresh ``BrainChatConfig()`` (enabled, not read-only, 8
    rounds, 300 s) when app.state carries no ``hal0_config`` — so bare test
    apps and older configs behave exactly as before.
    """
    from hal0.config.schema import BrainChatConfig

    cfg = getattr(request.app.state, "hal0_config", None)
    bc = getattr(cfg, "brain_chat", None)
    return bc if isinstance(bc, BrainChatConfig) else BrainChatConfig()


def _is_read_tool(name: str, args: dict[str, Any]) -> bool:
    """True when ``name`` only READS state (safe under read-only mode).

    Mirrors the branch order of :func:`_dispatch_tool`: board reads, then
    non-mutating platform tools, then GET-method local tools, then the admin
    catalog's autonomous-read set. An unknown tool is treated as NOT a read,
    so read-only fails closed.
    """
    if _resolve_read_tool(name, args)[0] is not None:
        return True
    p_method, _p_path, p_mutating = _resolve_platform_tool(name, args)
    if p_method is not None:
        return not p_mutating
    method, _path, _tool_params, _body, _target = _resolve_tool(name, args)
    if method is not None:
        return method.upper() == "GET"
    if name in _admin_tool_names():
        try:
            from hal0.mcp.admin import AUTONOMOUS_READ_TOOLS
        except ImportError:
            return False
        return name in AUTONOMOUS_READ_TOOLS
    return False


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
    # Persona surface filter applies to LOCAL tools too — the admin path
    # enforces it inside admin.dispatch, but a narrowed tools_allowed
    # must also hold against board/platform tools the model might guess
    # (they're filtered from its schema list, but never trust the list).
    policy = _brain_tool_policy(request)
    if policy is not None and not policy.allows(name):
        return {"error": f"tool {name!r} is outside the hal0-brain persona's tools_allowed"}

    # Read-only guardrail — refuses every mutating/admin-write tool regardless
    # of the persona's tools_allowed / approval policy ([brain_chat]
    # read_only=true). Reads still pass so the steward can answer questions.
    if _brain_chat_config(request).read_only and not _is_read_tool(name, args):
        return {
            "error": (
                f"tool {name!r} refused: the hal0-brain chat is in read-only mode "
                "([brain_chat] read_only=true) — mutating and admin-write tools are disabled"
            )
        }

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
        # Not a local tool — the rest of the surface is the admin-MCP
        # catalog, dispatched through the same gating/audit core as
        # /mcp/admin (gated tools return ``pending_approval``).
        if name in _admin_tool_names():
            return await _dispatch_admin_tool(request, name, args)
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
    timeout_s = _brain_chat_config(request).completion_timeout_s

    async def _primary_completion(body: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout_s) as http:
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


# Reasoning models interleave chain-of-thought with the reply, either as a
# separate message field (DeepSeek-style ``reasoning_content``) or inline
# ``<think>…</think>`` tags (Qwen-style). Both are split out and streamed as
# ``thinking`` frames so the UI can fold them away instead of rendering raw
# think-tags in the chat bubble.
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


def _split_thinking(content: str) -> tuple[str, str]:
    """Split ``<think>`` blocks out of assistant content → (thinking, visible)."""
    if "<think>" not in content:
        # DeepSeek-R1-style chat templates prefill the opening tag, so the
        # completion can start mid-reasoning and carry only a closing tag.
        if "</think>" in content:
            reasoning, _, visible = content.partition("</think>")
            return reasoning.strip(), visible.strip()
        return "", content
    thinking_parts = _THINK_RE.findall(content)
    visible = _THINK_RE.sub("", content)
    rest = visible.split("<think>", 1)
    if len(rest) == 2:  # unterminated trailing <think> — all of it is reasoning
        visible = rest[0]
        thinking_parts.append(rest[1])
    return "\n".join(p.strip() for p in thinking_parts if p.strip()), visible.strip()


def _assistant_thinking(response: dict[str, Any]) -> str:
    """Pull explicit reasoning fields off the assistant message."""
    choices = response.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    for key in ("reasoning_content", "reasoning"):
        value = msg.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


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

    cfg = _brain_chat_config(request)
    if not cfg.enabled:
        yield _sse(
            {
                "type": "error",
                "message": "the hal0-brain agent chat is disabled ([brain_chat] enabled=false)",
            }
        )
        yield _sse({"type": "done"})
        return

    llm = _resolve_llm(request)
    board = payload.get("board")
    system_prompt, default_model = _resolve_profile(request)
    messages: list[dict[str, Any]] = list(payload.get("messages") or [])
    # Seed the system prompt unless the client sent its own — the model needs
    # the lane vocabulary and the read-before-write rule to act on the board.
    if not messages or not (isinstance(messages[0], dict) and messages[0].get("role") == "system"):
        messages.insert(0, {"role": "system", "content": system_prompt})

    # Model precedence: an explicit per-request model wins, then the
    # [brain_chat] model override (e.g. hal0/npu to run on the NPU chat slot),
    # then the persona's preferred_model / built-in default.
    model = payload.get("model") or (cfg.model or None) or default_model
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": _surfaced_tool_schemas(request),
        "stream": False,
        "max_tokens": int(payload.get("max_tokens") or _MAX_COMPLETION_TOKENS),
    }

    try:
        for _round in range(cfg.max_rounds):
            response = await llm(body)
            if isinstance(response, dict) and response.get("error"):
                yield _sse({"type": "error", "message": str(response["error"])})
                yield _sse({"type": "done"})
                return

            explicit_thinking = _assistant_thinking(response)
            inline_thinking, text = _split_thinking(_assistant_text(response))
            thinking = "\n".join(t for t in (explicit_thinking, inline_thinking) if t)
            if thinking:
                yield _sse({"type": "thinking", "text": thinking})
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
                if isinstance(result, dict) and result.get("status") == "pending_approval":
                    # Surface the gate in the chat thread itself — the top-bar
                    # bell polls /api/agent/approvals, but without this frame
                    # the thread shows a generic tool card and the operator
                    # has no in-chat cue that the call is parked on them.
                    approval_id = str(result.get("approval_id") or "")
                    yield _sse(
                        {
                            "type": "approval_required",
                            "id": tc["id"],
                            "name": tc["name"],
                            "approval_id": approval_id,
                        }
                    )
                    # Pause the turn until the operator decides (or timeout).
                    queue = getattr(request.app.state, "approval_queue", None)
                    decided: dict[str, Any] | None = None
                    waited = 0.0
                    since_ping = 0.0
                    while queue is not None and approval_id and waited < _APPROVAL_WAIT_S:
                        entry = queue.get(approval_id)
                        if entry is None:
                            break
                        if entry.state == "executed":
                            raw = entry.result
                            decided = (
                                raw
                                if isinstance(raw, dict)
                                else {"status": "executed", "result": raw}
                            )
                            break
                        if entry.state == "failed":
                            decided = {
                                "status": "error",
                                "error": entry.error or "approved call failed",
                            }
                            break
                        if entry.state == "denied":
                            decided = {
                                "status": "denied",
                                "detail": (
                                    "the operator denied this call — do not retry it; "
                                    "ask what they want instead"
                                ),
                            }
                            break
                        await asyncio.sleep(_APPROVAL_POLL_S)
                        waited += _APPROVAL_POLL_S
                        since_ping += _APPROVAL_POLL_S
                        if since_ping >= _APPROVAL_PING_EVERY_S:
                            since_ping = 0.0
                            yield _sse({"type": "ping"})
                    if decided is not None:
                        result = decided
                        yield _sse(
                            {
                                "type": "tool_result",
                                "id": tc["id"],
                                "name": tc["name"],
                                "result": result,
                            }
                        )
                    else:
                        result = {
                            **result,
                            "detail": (
                                "queued for operator approval — no decision arrived while "
                                "the turn waited. Tell the operator it is still pending "
                                "(chat card or top-bar bell); once approved they can ask "
                                "you to re-check the outcome."
                            ),
                        }
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
    "BRAIN_PERSONA_ID",
    "BRAIN_SLOT_MODEL",
    "PRIMARY_SLOT_MODEL",
    "_admin_tool_names",
    "_admin_tool_schemas",
    "_brain_chat_config",
    "_brain_tool_policy",
    "_chat_stream",
    "_compact_board",
    "_dispatch_admin_tool",
    "_dispatch_platform_tool",
    "_dispatch_tool",
    "_is_read_tool",
    "_resolve_platform_tool",
    "_resolve_read_tool",
    "_resolve_tool",
    "_surfaced_tool_schemas",
    "_tool_schemas",
    "run_board_chat",
]
