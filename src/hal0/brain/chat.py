"""First-class hal0-brain chat engine — the ``POST /api/brain/chat`` core.

SPEC §G / R4: the brain is a FIRST-CLASS module that consumes the shared
tool-loop engine (:mod:`hal0.toolloop.engine`) and works WITHOUT the board
backend or any agent plugin — it has ZERO import dependency on Hermes. The
board's operator-board client and the platform self-API are reached only
through ``app.state`` handles injected at runtime; nothing here imports them.

The brain is the resident platform steward: a hal0-NATIVE conversational agent
that administers the whole instance, not just the board. It embodies the
``hal0-brain`` profile (separate memory namespace, hal0-heavy context; persona
TOML overrides the built-in prompt/model when present) and runs a client-side
OpenAI tool-calling loop whose LLM is the hal0 ``brain`` slot, falling back to
``agent`` via the resolver chain (reached via hal0-api's own
``/v1/chat/completions`` surface, mirroring :class:`hal0.omni_router.OmniRouter`).

``/api/brain/chat`` is the PRIMARY route; ``/api/board/chat`` is a thin
back-compat alias (see :mod:`hal0.api.routes.board_chat`). Three tool families:

* **Board reads** (unaudited, via the injected operator-board client
  ``app.state.hermes_kanban`` — mirrors the REST proxy's allowlisted GET
  rows): get_board · get_task · get_assignees · get_orchestration. When no
  board backend is wired the chat surfaces a "not configured" notice; the
  brain module itself imports nothing board-specific.
* **Board mutations** (each an AUDITED ``board.chat.turn`` row through the
  same client the REST handlers use): move/assign · create · comment ·
  dep add/remove · block · specify · decompose · nudge · orchestration update.
* **Platform tools** (hal0-api's OWN surface, self-HTTP against
  ``app.state.self_api_base_url``): slots list/get/load/unload/restart,
  models, hardware stats, installed agents. Reads unaudited; slot mutations
  write ``platform.chat.turn`` rows.

A chat-driven board mutation surfaces on the board LIVE via the kanban events
WS — chat is NOT the board transport.

Transport contract (kept stable so the LLM backend can later swap to a
different agent backend via chat_proxy WITHOUT any UI change):

    SSE events, one JSON object per ``data:`` line:
      {"type": "token",  "text": "..."}            assistant token delta
      {"type": "thinking", "text": "..."}           model reasoning (collapsed detail,
                                                    never shown inline). The brain
                                                    model's `reasoning_content` is
                                                    surfaced HERE, not discarded —
                                                    see `_completion_budget` for why
                                                    the completion budget has a floor.
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
import functools
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import structlog
from fastapi import Request
from fastapi.responses import StreamingResponse

from hal0.api._audit import record_action
from hal0.toolloop.engine import (
    assistant_message as _assistant_message,
)
from hal0.toolloop.engine import (
    assistant_text as _assistant_text,
)
from hal0.toolloop.engine import (
    assistant_thinking as _assistant_thinking,
)
from hal0.toolloop.engine import (
    extract_tool_calls as _extract_tool_calls,
)
from hal0.toolloop.engine import (
    openai_tool_schema,
    run_tool_loop,
)
from hal0.toolloop.engine import (
    parse_text_tool_calls as _parse_text_tool_calls,
)
from hal0.toolloop.engine import (
    split_thinking as _split_thinking,
)

# The names above are re-exported unchanged from the shared tool-loop core
# (hal0.toolloop.engine) purely so existing test imports
# (``from hal0.api.routes.board_chat import _extract_tool_calls, ...``)
# keep working post-extraction — they're not called directly in this
# module anymore (the loop now delegates to ``run_tool_loop``), which is
# why they're listed in ``__all__`` below rather than looking unused.

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

# Per-round completion FLOOR. The other end of the same dial, and the one that
# actually bites.
#
# The brain model is a thinking model: it splits its output into `content` and
# `reasoning_content`, and the reasoning is emitted FIRST and drawn from the
# SAME completion budget. Measured on a GPU box with `max_tokens: 80`: the
# entire budget went to reasoning, `content` came back as the empty string, and
# `finish_reason` was "stop" — i.e. a well-formed, successful, COMPLETELY BLANK
# steward reply. Nothing downstream can recover from that: `run_tool_loop`
# only emits a `token` frame when the visible text is non-empty
# (toolloop/engine.py), so the user sees a collapsed "thinking" disclosure and
# then silence, which reads as the steward being broken.
#
# So a caller-supplied `max_tokens` is treated as a REQUEST, not a contract:
# anything below this floor is raised to it. 512 tokens is enough for a short
# chain of thought AND a real answer on the shipped brain model, and it is far
# below `_MAX_COMPLETION_TOKENS`, so this narrows nothing an operator can
# usefully ask for — it only rules out budgets that can only produce a blank.
# Raising `max_tokens` still works exactly as before, including above the
# default cap.
#
# Keep this a floor rather than "reserve N tokens for content": llama-server
# has no split-budget knob, so the only lever available is the total.
_MIN_COMPLETION_TOKENS = 512

# Pre-flight context estimate: ~4 chars/token (the standard GPT-family rule of
# thumb) plus a small per-message overhead for the role/formatting tokens the
# chat template adds. Deliberately rough — it only gates the pre-flight check
# (which fires BEFORE a guaranteed 400 exceed_context), so an approximate
# ceiling beats an exact tokenizer round-trip on every turn.
_CHARS_PER_TOKEN = 4
_MSG_TOKEN_OVERHEAD = 4

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
memory, separate from the Hermes chat agent (namespace private:hermes__hal0-brain).

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


# ── internal self-HTTP auth (O17) ───────────────────────────────────────────
#
# Every call the steward makes back to the box's OWN API (/v1 completion, the
# platform self-API, the admin-MCP hops) must carry a bearer on an auth-enabled
# box (the default on non-loopback binds) — otherwise `/v1` and `/api/*` reject
# it with `auth.required` and the steward is dead out of the box. Precedence:
# forward the CALLER's inbound Authorization when the request carries one; else
# present the box service identity from `hal0.service_identity` (env → api.env,
# the SAME seam the CLI uses). `prefer` picks the least-privilege tier for the
# surface — "client" for the /v1 inference call, "admin" for the platform/admin
# surfaces that include slot mutations and per-slot reads. Key values are never
# logged or echoed.


def _inbound_bearer(request: Request) -> str | None:
    """The caller's bearer token (sans ``Bearer `` prefix), or ``None``."""
    raw = request.headers.get("Authorization", "") or ""
    return raw.removeprefix("Bearer ").strip() or None


def _self_call_headers(request: Request, *, prefer: str) -> dict[str, str]:
    """Authorization for one internal self-HTTP call (forward-caller, else service)."""
    raw = request.headers.get("Authorization", "") or ""
    if raw.strip():
        return {"Authorization": raw}
    from hal0.service_identity import service_auth_headers

    return service_auth_headers(prefer=prefer)


def _self_call_bearer(request: Request, *, prefer: str) -> str | None:
    """Bearer token for an internal call that takes a bare token (admin dispatch).

    Forwards the caller's inbound token when present; else the box service key.
    """
    inbound = _inbound_bearer(request)
    if inbound is not None:
        return inbound
    from hal0.service_identity import service_key

    return service_key(prefer=prefer)


# ── tool definitions (1:1 with the audited board mutations) ─────────────────


def _fn(name: str, desc: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return openai_tool_schema(
        name, desc, {"type": "object", "properties": props, "required": required}
    )


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
            "List the installed platform agents (e.g. hermes).",
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
        # memory_* (all 26 — the original 5 plus the Hindsight 0.8.4 parity
        # buildout's reflect/curate/history, mental-model CRUD+refresh,
        # directive CRUD, operations, tags/bank-stats/consolidate) ride the
        # profile's own namespace (private:hermes__hal0-brain) via Hindsight,
        # not the agent memory engine's MCP dispatcher. Listed explicitly
        # so this stays a plain frozenset — a static attribute other test
        # modules read directly (e.g. tests/board/test_board_chat_admin_
        # tools.py's ``catalog - bc._ADMIN_TOOL_EXCLUDES``) — while
        # :func:`_is_admin_tool_excluded` below is the actual prefix-based
        # check the exclusion logic runs, so a 27th memory_* tool is
        # excluded automatically without this list needing a touch.
        "memory_add",
        "memory_search",
        "memory_list",
        "memory_delete",
        "memory_recall",
        "memory_reflect",
        "memory_curate",
        "memory_history",
        "memory_mental_model_list",
        "memory_mental_model_get",
        "memory_mental_model_create",
        "memory_mental_model_update",
        "memory_mental_model_delete",
        "memory_mental_model_refresh",
        "memory_directive_list",
        "memory_directive_get",
        "memory_directive_create",
        "memory_directive_update",
        "memory_directive_delete",
        "memory_operation_list",
        "memory_operation_get",
        "memory_operation_cancel",
        "memory_operation_retry",
        "memory_tags_list",
        "memory_bank_stats",
        "memory_bank_consolidate",
    }
)


def _is_admin_tool_excluded(name: str) -> bool:
    """True when ``name`` must not reach the brain-chat surfaced schema.

    memory_* is excluded by PREFIX — the brain handles memory through the
    hal0-brain persona's own namespace via Hindsight directly (see
    ``_ADMIN_TOOL_EXCLUDES`` above), not the agent memory engine's MCP
    dispatcher, and that rationale applies to every memory_* tool
    uniformly regardless of how many hal0.mcp.memory ends up exposing.
    The remaining exclusions are the literal name collisions / semantic
    duplicates with the local board-chat platform verbs.
    """
    return name.startswith("memory_") or name in _ADMIN_TOOL_EXCLUDES


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
    return frozenset(
        t
        for t in (AUTONOMOUS_READ_TOOLS | AUTONOMOUS_WRITE_TOOLS | GATED_TOOLS)
        if not _is_admin_tool_excluded(t)
    )


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
        if _is_admin_tool_excluded(name):
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
    # O17: forward the caller's bearer; else fall back to the box admin
    # identity so the admin-MCP self-hops authenticate on an auth-on box.
    bearer = _self_call_bearer(request, prefer="admin")
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


async def _platform_request(
    http: httpx.AsyncClient,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
) -> Any:
    """One self-HTTP call, mapped to a tool-result the loop can step against."""
    try:
        resp = await http.request(method, path, headers=headers or None)
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
    # O17: the platform surface spans CLIENT reads (list_slots) AND ADMIN ops
    # (get_slot, slot load/unload/restart) — present the admin identity so the
    # whole surface authenticates; the caller's own bearer wins when inbound.
    headers = _self_call_headers(request, prefer="admin")
    try:
        if not mutating:
            return await _platform_request(http, method, path, headers)
        async with record_action(
            request,
            category="platform",
            action="platform.chat.turn",
            target=args.get("name"),
            message=f"chat:{name}",
        ) as rec:
            result = await _platform_request(http, method, path, headers)
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


def _completion_budget(requested: Any) -> int:
    """Resolve one round's ``max_tokens``, never below the blank-reply floor.

    ``requested`` is whatever arrived in the request body — an int, a numeric
    string, ``None``, or junk. Resolution:

    * absent / zero / negative / unparseable → :data:`_MAX_COMPLETION_TOKENS`
      (unchanged behaviour: the default cap);
    * below :data:`_MIN_COMPLETION_TOKENS` → raised to the floor, and logged.
      The brain model spends its budget on ``reasoning_content`` first, so a
      tiny budget yields a well-formed, ``finish_reason: stop``, completely
      EMPTY ``content`` — a blank steward reply. See the constant's comment;
    * anything else → honoured verbatim, including values above the default
      cap. A caller asking for MORE room is never second-guessed.
    """
    try:
        n = int(requested)
    except (TypeError, ValueError):
        return _MAX_COMPLETION_TOKENS
    if n <= 0:
        return _MAX_COMPLETION_TOKENS
    if n < _MIN_COMPLETION_TOKENS:
        log.info(
            "hal0.brain_chat.completion_budget_raised",
            requested=n,
            floor=_MIN_COMPLETION_TOKENS,
            reason="reasoning_content consumes the budget before content is emitted",
        )
        return _MIN_COMPLETION_TOKENS
    return n


def _brain_chat_config(request: Request) -> Any:
    """The ``[brain_chat]`` config off app.state, or defaults.

    Falls back to a fresh ``BrainChatConfig()`` (enabled, READ-ONLY, 8
    rounds, 300 s) when app.state carries no ``hal0_config`` — a bare app
    gets the shipped safe default; mutation harnesses opt in explicitly
    with ``read_only=False`` (spec-kb23 §4b).
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
    # O17: /v1/chat/completions is CLIENT-gated when auth is on (the default on
    # non-loopback binds). Without a bearer the slot 401s `auth.required` and
    # the steward is dead out of the box. Forward the caller's inbound bearer;
    # else present the box service identity at the least-privilege CLIENT tier.
    headers = _self_call_headers(request, prefer="client")

    async def _primary_completion(body: dict[str, Any]) -> dict[str, Any]:
        tried_model = body.get("model")
        if not isinstance(tried_model, str) or not tried_model:
            # Nothing to dispatch to — short-circuit before the self-call so an
            # empty resolution doesn't burn a network round trip just to fail.
            return {"error": _unrouteable_model_error(tried_model or "(no model set)")}
        async with httpx.AsyncClient(timeout=timeout_s) as http:
            try:
                resp = await http.post(
                    f"{base_url.rstrip('/')}/v1/chat/completions",
                    json=body,
                    headers=headers or None,
                )
            except httpx.HTTPError as exc:
                # Genuine transport failure (connection refused, DNS, timeout —
                # the box's own /v1 never answered). Kept distinguishable from
                # the unrouteable-model case below: this text always says
                # "transport failure", the other never does.
                return {"error": f"primary slot transport failure: {exc}"}
            if resp.status_code == 404:
                # Unrouteable: the resolver chain (hal0/<slot> -> agent) found
                # no loaded slot to serve `tried_model`, so the self /v1 call
                # 404s with dispatch.no_route — a CONFIGURATION gap, not a
                # transport failure. Surface actionable guidance instead of
                # the raw dispatch envelope (finding: docs/rework/
                # r4-stage-validation.md "steward config note" — a fresh box
                # with [brain_chat] model="" 404s with no path forward).
                return {"error": _unrouteable_model_error(tried_model)}
        if not (200 <= resp.status_code < 300):
            return {"error": f"primary slot HTTP {resp.status_code}: {resp.text[:300]}"}
        try:
            return resp.json()
        except ValueError:
            return {"error": "primary slot returned non-JSON"}

    return _primary_completion


def _unrouteable_model_error(tried_model: str) -> str:
    """Actionable text for a 404/no-slot dispatch failure (unrouteable model).

    Replaces the raw ``dispatch.no_route`` transport envelope with guidance
    an operator can act on directly: which model id the steward tried, and
    the two ways to fix it (load a slot, or point the config at one that IS
    loaded). See the fresh-box finding this closes: docs/rework/
    r4-stage-validation.md "steward config note" — ``[brain_chat] model=""``
    still drives the ``hal0/brain`` -> ``agent`` resolver chain (see
    :mod:`hal0.normalize.resolver`), but when neither slot is loaded the
    chat dead-ends with no indication of what to do.
    """
    return (
        f"the hal0-brain chat could not route to a model — {tried_model!r} has no "
        "loaded slot behind it. Fix: load a `brain` or `agent` inference slot "
        "from the dashboard's Slots panel (either backs the steward via the "
        "resolver's hal0/brain -> agent chain), or set [brain_chat] model in "
        "hal0.toml to a slot that IS loaded. A slot serving the steward needs "
        "at least 8k context — the hal0-brain system prompt alone is ~7.3k "
        "tokens."
    )


# ── tool-round rerouting (ADR-0023 / spec-p3-brain.final.md §5a) ────────────
#
# WHY THIS EXISTS. The shipped brain model is a ~1.1B SFT that CANNOT emit tool
# calls the local runtime parses. Measured on a GPU box against the published
# `hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf` in the pinned ROCmFPX runner:
#
#   * WITH `--jinja` + native OpenAI `tools`: HTTP 200, `finish_reason: stop`,
#     `tool_calls: null`, and `content` came back as the literal string
#     ' name="slot_list">'. The model emitted `<function name="slot_list">`;
#     llama.cpp's jinja tool parser ate the `<function` prefix as a tool-call
#     start marker, failed to parse the rest, and dumped the REMNANT into
#     content. That remnant is signal (a) below.
#   * WITHOUT `--jinja`, using the `hal0-function-xml` contract in the system
#     prompt: `content` came back EMPTY with `reasoning_content` ending
#     "I'll call the slot_list() function to retrieve the information." — it
#     stated intent and stopped. That is signal (b) below.
#   * WITH `--jinja` + `--chat-template-file hal0-brain-sft.jinja` (the bundled
#     template, post-#1434) + native `tools`: HTTP 500 "The model produced
#     output that does not match the expected peg-native format" on EVERY
#     tools-attached completion, tool-related prompt or not. llama.cpp builds
#     a tool-format parser from the template and rejects the whole request
#     when the output doesn't match. This is why `_routed` strips `tools`
#     from the brain round below — no response ever arrives to run signal
#     detection on otherwise.
#
# So the 1B genuinely cannot drive tools on this runtime. `[brain_chat]
# tool_model` (default "hal0/agent") is the documented mitigation, and this is
# its implementation.
#
# ROUTING SEMANTICS — per ROUND, not per conversation.
#
#   The brain's own 1B is the steward's VOICE. Keeping plain chat on it is the
#   entire point of running a 1B, so every turn starts there and a turn that
#   never needs a tool never touches `tool_model` at all — no extra latency, no
#   35B wake-up for "hi".
#
#   A round becomes a TOOL round in exactly two ways:
#     1. the brain produced a usable tool call (native `tool_calls`, or a
#        text-embedded one the shared fallback parses). Nothing is re-run —
#        that call is dispatched as-is;
#     2. the brain produced a tool-call ARTEFACT it could not express
#        (:func:`_tool_intent_artefact`). Its output is DISCARDED and the round
#        is re-run against `tool_model`, whose reply is what reaches the user.
#        The user must never see ' name="slot_list">'.
#
#   Once a turn has entered a tool chain by either route, every SUBSEQUENT
#   round of that turn runs on `tool_model` — the tool-capable model continues;
#   the 1B does NOT summarise tool results back. Four reasons, in order:
#     * the `role: tool` payloads are raw JSON from an 82-tool admin catalogue.
#       Reading them is precisely the reasoning the 1B already failed at;
#     * a chain usually needs a SECOND call (read the slot, then load it).
#       Handing back to the 1B mid-chain breaks it at the first continuation;
#     * OpenAI wire semantics: the assistant message carrying `tool_calls` and
#       its matching `role: tool` replies belong to ONE assistant turn-owner.
#       Swapping models mid-chain produces shapes strict chat templates reject
#       (the qwen3.5 `multi_step_tool` guard already bit this module — see
#       `_frame_messages` / O18);
#     * consistency: one model owns a tool chain end to end.
#
#   The chain is scoped to ONE turn. The next user message re-enters
#   `_chat_stream` with fresh state and lands back on the 1B, so the steward's
#   voice returns as soon as the tool work is done. State is tracked LOCALLY
#   per turn and deliberately NOT inferred from the inbound `messages` — the
#   dashboard replays prior `role: tool` turns as history (ui/src/api/hooks/
#   useBoard.ts), so scanning them would pin every later turn to `tool_model`.
#
# NOTE ON `model` PRECEDENCE. An explicit per-request `model` sets the turn's
# CHAT model; it does NOT suppress the reroute. It cannot: the dashboard used
# to send `model: "hal0/brain"` on EVERY send (useBoard.ts BOARD_CHAT_MODEL,
# removed by #1469 so `[brain_chat].model` / persona `preferred_model` can
# actually take effect), so treating a per-request model as "pin the whole
# turn" would have made this feature dead in the only UI that used it — and
# any client that still pins a model explicitly must not lose tool calling.
#
# NOTE ON THE ENGINE. None of this touches `hal0.toolloop.engine`. The engine
# owns `body` and mutates it in place between rounds; the per-round model hook
# is just this wrapper writing `body["model"]` before delegating. OmniRouter's
# use of `run_tool_loop` is therefore untouched by construction.

#: The remnant llama.cpp's jinja tool parser leaves in `content` when it eats a
#: `<function name="X">` opener it then fails to parse: `name="X"`. `/` is in
#: the name class because the brain reaches for slash-qualified names too —
#: measured live: ` name="hal0/slot_list">` (virtual-model spelling).
_ARTEFACT_NAME_RE = re.compile(r"""name\s*=\s*["']?\s*([A-Za-z0-9_./\-]+)""")

#: An unterminated `<function=X` opener — the terminated form is already handled
#: by the shared text-call fallback (`parse_text_tool_calls`).
_ARTEFACT_FN_RE = re.compile(r"<\s*function\s*=\s*([A-Za-z0-9_./\-]+)", re.IGNORECASE)

#: Call-tag syntax of any shape. Reaching this module at all means neither
#: extractor could make a call out of it, so a surviving `<function` /
#: `<tool_call` opener is by definition a FAILED call attempt.
_ARTEFACT_TAG_RE = re.compile(r"<\s*/?\s*(?:function|tool_call)\b", re.IGNORECASE)

#: A stated call in prose: `slot_list(` / `slot_list()`. The paren form is what
#: separates "I will call X()" from a reply that merely mentions X.
_CALL_FORM_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_.\-]*)\s*\(")

#: Debris left over once a `name="X"` fragment is lifted out of a mangled tag.
_TAG_DEBRIS = " \t\r\n<>/\"'="


def _tool_intent_artefact(response: dict[str, Any], known_tool_names: frozenset[str]) -> str | None:
    """The tool the brain *tried* to call but could not express, or ``None``.

    Called ONLY after both the native and the text-embedded extractors came up
    empty — this is the "the model wanted a tool and produced garbage instead"
    detector, not a third parser. Returns the name it was reaching for (for the
    log line); ``"(unnamed)"`` when the artefact carries no readable name.

    DELIBERATELY NOT GATED ON THE NAME BEING A REAL TOOL. The measured leak was
    ``<function name="slot_list">`` and there is no ``slot_list`` in the
    catalogue — the real names are ``list_slots`` / ``slot_state`` — so a
    membership check would have missed the exact failure this exists to catch.
    A 1B reaching for a tool it half-remembers is *more* reason to hand the
    round to the capable model, not less: that model then picks the right tool.
    ``known_tool_names`` is therefore only the "are any tools surfaced at all"
    guard (with none, there is nothing to reroute for) and a preference for
    which name to report.

    Two signals, both narrow enough that ordinary prose cannot trip them:

    (a) call-tag syntax surviving in the VISIBLE text. Either a whole
        ``<function`` / ``<tool_call`` opener, or the bare ``name="X">``
        REMNANT the jinja parser leaves after eating the opener — recognised
        only when the fragment is essentially the entire reply, so a sentence
        that happens to contain ``name="ops"`` is not an artefact.

    (b) a stated call in the reasoning with NOTHING to show for it — the
        no-``--jinja`` shape, where ``content`` is empty and
        ``reasoning_content`` says "I'll call the slot_list() function". Gated
        on the visible text being empty: a real reply is never empty, so this
        can only fire on a turn that already produced nothing usable.
    """
    if not known_tool_names:
        return None

    inline_thinking, visible = _split_thinking(_assistant_text(response))

    def _name_in(text: str) -> str | None:
        """Prefer a real tool name; otherwise report whatever was written."""
        found = [m.group(1) for p in (_ARTEFACT_FN_RE, _ARTEFACT_NAME_RE) for m in p.finditer(text)]
        for name in found:
            if name in known_tool_names:
                return name
        return found[0] if found else None

    # (a1) a surviving call-tag opener — unambiguous failed-call syntax.
    if _ARTEFACT_TAG_RE.search(visible):
        return _name_in(visible) or "(unnamed)"

    # (a2) the measured remnant: content IS a `name="X">` fragment and little
    # else. Removing the fragment must leave nothing but tag punctuation.
    if _ARTEFACT_NAME_RE.search(visible) and not _ARTEFACT_NAME_RE.sub("", visible).strip(
        _TAG_DEBRIS
    ):
        return _name_in(visible) or "(unnamed)"

    # (b) stated intent, nothing to show for it. A non-empty reply is a reply.
    if visible.strip():
        return None
    reasoning = "\n".join(t for t in (_assistant_thinking(response), inline_thinking) if t)
    matches = [m.group(1) for m in _CALL_FORM_RE.finditer(reasoning)]
    for name in matches:
        if name in known_tool_names:
            return name
    return matches[0] if matches else None


def _tool_reroute_unavailable_message(tool_model: str, error: str) -> str:
    """What the steward SAYS when the reroute target has no model behind it.

    The single most likely real-world path, not an error branch: per the v1.0
    ruling the agent anchor is an opt-in that DEFAULTS TO SKIP at install time,
    so on a fresh box ``hal0/agent`` has no model bound and this fires on the
    operator's first "list my slots". It must read as the steward explaining a
    gap it can see, with the exact fix — never a stack trace, never a raw
    dispatch envelope, and above all never an empty reply.
    """
    slot = tool_model.split("/", 1)[1] if tool_model.startswith("hal0/") else tool_model
    return (
        "I can't run tools right now. I answer chat on the brain model, but it can't "
        f"emit tool calls on this runtime, so tool turns route to {tool_model!r} — and "
        "that has no model behind it yet.\n\n"
        f"To fix it: open the Models page, pull a tool-capable model, bind it to the "
        f"`{slot}` slot, and load the slot. Alternatively point `[brain_chat] tool_model` "
        'in hal0.toml at a slot that IS loaded, or set it to "off" if you\'d rather I '
        "never offer tools.\n\n"
        f"Plain chat keeps working meanwhile. (Underlying error: {error})"
    )


def _synthetic_reply(text: str) -> dict[str, Any]:
    """An OpenAI-shaped completion carrying ``text`` as the assistant reply.

    Used to turn a failed reroute into a normal, terminating steward turn: the
    loop core sees an assistant message with no ``tool_calls``, emits one
    ``token`` frame and ``done``. No crash, no 500, no empty message.
    """
    return {
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]
    }


def _tool_routing_llm(
    llm: LlmFn,
    *,
    chat_model: str,
    tool_model: str,
    known_tool_names: frozenset[str],
) -> LlmFn:
    """Wrap ``llm`` so TOOL rounds run on ``tool_model`` and chat stays on the brain.

    The per-round model hook, implemented entirely on this side of the seam:
    ``body`` is the engine-owned request dict, so setting ``body["model"]``
    before delegating is all a "route this round elsewhere" hook needs. The
    shared :func:`hal0.toolloop.engine.run_tool_loop` — and therefore
    OmniRouter — is not modified.

    An empty ``tool_model`` (the explicit ``"off"``/``"none"``/``"disabled"``
    spellings, normalised by :meth:`BrainChatConfig._normalise_tool_model`) or a
    ``tool_model`` equal to the chat model returns ``llm`` behaviourally
    unchanged: exactly the pre-reroute code path, one call per round.

    See the module comment above this function for the full routing semantics
    and why the tool-capable model — not the 1B — finishes a tool chain.
    """
    # Nothing to reroute TO, or nowhere to reroute FROM. Note this branch is
    # also what "no reroute" costs: a plain pass-through, not a wrapper that
    # inspects every response.
    if not tool_model or tool_model == chat_model:

        async def _plain(body: dict[str, Any]) -> dict[str, Any]:
            body["model"] = chat_model
            return await llm(body)

        return _plain

    # Per-TURN, not per-round: once this turn is in a tool chain it stays on
    # tool_model for the rest of the turn. A new turn builds a new wrapper.
    in_tool_chain = False

    async def _degrade(response: dict[str, Any]) -> dict[str, Any]:
        """A tool-model round that failed becomes an honest steward sentence."""
        if isinstance(response, dict) and response.get("error"):
            log.warning(
                "hal0.brain_chat.tool_model_unavailable",
                tool_model=tool_model,
                error=str(response["error"]),
            )
            return _synthetic_reply(
                _tool_reroute_unavailable_message(tool_model, str(response["error"]))
            )
        return response

    async def _routed(body: dict[str, Any]) -> dict[str, Any]:
        nonlocal in_tool_chain

        if in_tool_chain:
            body["model"] = tool_model
            return await _degrade(await llm(body))

        body["model"] = chat_model
        # The brain round goes out WITHOUT native `tools`. The runtime builds
        # a tool-format parser from the chat template and hard-fails the WHOLE
        # completion when the model's output doesn't match it — measured on
        # lxc105 (image c077206, bundled hal0-brain-sft.jinja): every
        # tools-attached request, even "just say hello", returns HTTP 500
        # "The model produced output that does not match the expected
        # peg-native format". The 1B cannot match that format by design (the
        # reason this reroute exists), so attaching `tools` buys nothing and
        # costs the entire turn. Detection is unaffected: the text fallback
        # and _tool_intent_artefact read the reply, not the request, and the
        # steward knows its tool surface from the system prompt. Popped per
        # round and restored — the engine owns `body` and the tool-model
        # rounds need the schemas back.
        native_tools = body.pop("tools", None)
        try:
            response = await llm(body)
        finally:
            if native_tools is not None:
                body["tools"] = native_tools
        if not isinstance(response, dict) or response.get("error"):
            # A BRAIN-side failure keeps its documented contract exactly — the
            # loop core turns it into the error+done frames tests already pin.
            return response

        # Usable call? Dispatch it as-is and hand the CONTINUATION to the tool
        # model. Re-running a round the brain got right would only burn a
        # second completion for the same answer.
        calls = _extract_tool_calls(response)
        if not calls:
            calls, _cleaned = _parse_text_tool_calls(_assistant_text(response), known_tool_names)
        if calls:
            in_tool_chain = True
            return response

        wanted = _tool_intent_artefact(response, known_tool_names)
        if wanted is None:
            return response  # plain chat — the steward's own reply stands.

        # The brain tried and produced garbage. Discard it and re-run THIS
        # round on the tool-capable model. This is one extra completion inside
        # one engine round, so `[brain_chat] max_rounds` still bounds the turn.
        log.info(
            "hal0.brain_chat.tool_round_rerouted",
            tool=wanted,
            chat_model=chat_model,
            tool_model=tool_model,
        )
        in_tool_chain = True
        body["model"] = tool_model
        return await _degrade(await llm(body))

    return _routed


# ── SSE framing helpers ─────────────────────────────────────────────────────


def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj)}\n\n"


# ── pre-flight context guard ─────────────────────────────────────────────────
#
# The steward's system prompt alone is ~7.3k tokens; a brain slot loaded at a
# small context window (e.g. the on-box `chat@4096` incident, or a slot whose
# ctx drifted below the config) 400s the completion with `exceed_context`
# AFTER the round-trip has been paid for. Estimate the assembled prompt against
# the resolved slot's context_length and emit the documented `error` frame with
# an actionable fix instead of burning the round-trip.


def _estimate_prompt_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token estimate for the assembled prompt (chars/4 + per-msg overhead)."""
    total = 0
    for m in messages:
        content = m.get("content") if isinstance(m, dict) else None
        if isinstance(content, str):
            total += len(content) // _CHARS_PER_TOKEN
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    total += len(part["text"]) // _CHARS_PER_TOKEN
        total += _MSG_TOKEN_OVERHEAD
    return total


async def _resolved_context_length(request: Request, model: Any) -> int | None:
    """Context window of the slot ``model`` resolves to, or None when unknown.

    Best-effort: reuses the same ``LiveSlotResolver`` inputs the /v1 path uses
    (``hal0/<slot>`` → live slot). Returns None for a non-virtual model, an
    unresolvable name, or any lookup failure — the pre-check then simply does
    not fire (never blocks a valid chat).
    """
    if not isinstance(model, str) or not model:
        return None
    try:
        from hal0.api.routes.v1 import _normalize_loaded_models, _normalize_slot_views
        from hal0.normalize.resolver import LiveSlotResolver

        views = await _normalize_slot_views(request)
        resolver = LiveSlotResolver(
            slot_views_provider=lambda: views,
            loaded_models_provider=lambda: _normalize_loaded_models(request),
        )
        res = await resolver.resolve(model)
    except Exception:
        return None
    if res is None or not res.context_length:
        return None
    return int(res.context_length)


def _context_exceeded_error(prompt_tokens: int, context_length: int, model: Any) -> str:
    """Actionable text for the pre-flight context-overflow guard."""
    return (
        f"the assembled prompt (~{prompt_tokens} tokens) exceeds the resolved slot's "
        f"context window ({context_length} tokens) for {model!r} — the completion would "
        "400 with exceed_context. Fix: raise [model].context_size on the backing slot and "
        "reload it, or point [brain_chat] model at a slot with a larger context window."
    )


# ── outbound message framing (O18) ──────────────────────────────────────────
#
# Chat templates with a user-query guard (e.g. qwen3.5's `multi_step_tool`,
# which 500s with "No user query found in messages") reject a completion
# request that carries no user-role turn. Two shapes tripped this:
#   1) a client POSTing the singular `{"message": "..."}` convenience field,
#      which the old framing dropped — leaving a SYSTEM-ONLY turn; and
#   2) any `messages` list that arrives without a user turn.
# `_frame_messages` reconciles both into a template-safe first round: system
# prompt first (seeded only when the client didn't send its own), the singular
# `message` folded in as a trailing user turn, and a guaranteed user-role
# entry. The tool loop only ever APPENDS assistant + `role: tool` messages
# between rounds (see toolloop/engine.run_tool_loop), so this user turn
# persists — no continuation round can regress to zero user-role entries, and
# the trailing `role: tool` shape continuation rounds carry is what such
# templates expect after a tool result.


def _frame_messages(payload: dict[str, Any], system_prompt: str) -> list[dict[str, Any]]:
    """Build the first-round messages list, guaranteed template-safe (O18)."""
    messages: list[dict[str, Any]] = [
        m for m in (payload.get("messages") or []) if isinstance(m, dict)
    ]
    # Accept the singular `message` field as a trailing user turn — the
    # dashboard sends `messages`, but curl/tests/other clients POST
    # {"message": "..."} and it must not be silently dropped.
    singular = payload.get("message")
    if isinstance(singular, str) and singular.strip():
        messages.append({"role": "user", "content": singular})
    # Seed the system prompt unless the client leads with its own.
    if not messages or messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": system_prompt})
    # Guarantee at least one user-role turn so the request never lands as a
    # system-only (or assistant/tool-tail-only) turn that the template rejects.
    if not any(m.get("role") == "user" for m in messages):
        messages.append({"role": "user", "content": ""})
    return messages


def _surfaced_tool_names(request: Request) -> frozenset[str]:
    """Names of the tools currently surfaced to the brain (for text-call gating)."""
    return frozenset(s["function"]["name"] for s in _surfaced_tool_schemas(request))


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
    # Frame the messages template-safely (system seed + a guaranteed user turn,
    # accepting the singular `message` field) — see _frame_messages / O18.
    messages = _frame_messages(payload, system_prompt)

    tools = _surfaced_tool_schemas(request)
    known_tool_names = _surfaced_tool_names(request)

    # CHAT model precedence: an explicit per-request model wins, then the
    # [brain_chat] model override (e.g. hal0/npu), then the persona's
    # preferred_model / BRAIN_SLOT_MODEL. This is the model the steward SPEAKS
    # with, and it is what every plain-chat round runs on.
    model = payload.get("model") or cfg.model or default_model

    # TOOL model: the round that needs a tool reroutes here, because the ~1.1B
    # brain cannot emit tool calls this runtime parses. Default "hal0/agent"
    # (ADR-0023's always-on anchor, resolved through the normal virtual-model
    # chain in hal0.normalize.resolver — no slot lookup is hardcoded here).
    # Already normalised by BrainChatConfig: "" / whitespace fell back to the
    # default with a warning, and "off"/"none"/"disabled" arrive here as "",
    # which _tool_routing_llm treats as no reroute. See the routing-semantics
    # comment above _tool_intent_artefact.
    tool_model = (cfg.tool_model or "").strip()
    llm = _tool_routing_llm(
        llm,
        chat_model=model,
        tool_model=tool_model,
        known_tool_names=known_tool_names,
    )

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": _completion_budget(payload.get("max_tokens")),
    }

    # Pre-flight context guard: if the assembled prompt already exceeds the
    # resolved slot's context window, the completion is a guaranteed 400
    # exceed_context — surface the actionable error frame instead of burning
    # the round-trip (the steward system prompt alone is ~7.3k tokens).
    ctx_len = await _resolved_context_length(request, model)
    if ctx_len:
        prompt_tokens = _estimate_prompt_tokens(messages)
        if prompt_tokens > ctx_len:
            yield _sse(
                {"type": "error", "message": _context_exceeded_error(prompt_tokens, ctx_len, model)}
            )
            yield _sse({"type": "done"})
            return

    dispatch_fn = functools.partial(_dispatch_round, request, client, board)
    try:
        async for event in run_tool_loop(
            llm,
            tools,
            dispatch_fn,
            body=body,
            max_rounds=cfg.max_rounds,
            known_tool_names=known_tool_names,
        ):
            if event.get("type") == "response":
                continue  # internal marker — never part of the documented SSE contract
            yield _sse(event)
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("hal0.board_chat.loop_error", error=str(exc))
        yield _sse({"type": "error", "message": str(exc)})
        yield _sse({"type": "done"})


async def _dispatch_round(
    request: Request,
    client: Any,
    board: str | None,
    tool_calls: list[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    """Sequentially dispatch one round's tool calls, pausing on approval gates.

    An async generator (not a plain callback) — the loop core relays each
    yielded event onward in real time, which is what lets a gated call's
    keepalive pings reach the SSE stream while the turn is paused (see
    :mod:`hal0.toolloop.engine` for why this must be generator delegation).
    """
    for tc in tool_calls:
        yield {
            "type": "tool_call",
            "id": tc["id"],
            "name": tc["name"],
            "arguments": tc["arguments"],
        }
        try:
            result = await _dispatch_tool(request, client, tc["name"], tc["arguments"], board=board)
        except Exception as exc:  # mutation failed — audited as error
            result = {"error": str(exc)}
        yield {"type": "tool_result", "id": tc["id"], "name": tc["name"], "result": result}
        if isinstance(result, dict) and result.get("status") == "pending_approval":
            # Surface the gate in the chat thread itself — the top-bar
            # bell polls /api/agent/approvals, but without this frame
            # the thread shows a generic tool card and the operator
            # has no in-chat cue that the call is parked on them.
            approval_id = str(result.get("approval_id") or "")
            yield {
                "type": "approval_required",
                "id": tc["id"],
                "name": tc["name"],
                "approval_id": approval_id,
            }
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
                        raw if isinstance(raw, dict) else {"status": "executed", "result": raw}
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
                    yield {"type": "ping"}
            if decided is not None:
                result = decided
                yield {
                    "type": "tool_result",
                    "id": tc["id"],
                    "name": tc["name"],
                    "result": result,
                }
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
                # Not re-forwarded as its own SSE frame (matching pre-refactor
                # behaviour) — the loop core still folds this updated
                # ``result`` into the tool message the LLM sees next round.
                yield {
                    "type": "tool_result",
                    "id": tc["id"],
                    "name": tc["name"],
                    "result": result,
                    "_engine_only": True,
                }


async def run_brain_chat(request: Request) -> StreamingResponse:
    """Entry point invoked by the ``/api/brain/chat`` route (and its board alias)."""
    raw = await request.body()
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return StreamingResponse(_chat_stream(request, payload), media_type="text/event-stream")


#: Back-compat alias for the historical ``/api/board/chat`` entry-point name.
#: ``hal0.api.routes.board.board_chat`` and older imports still call this.
run_board_chat = run_brain_chat


__all__ = [
    "BRAIN_PERSONA_ID",
    "BRAIN_SLOT_MODEL",
    "PRIMARY_SLOT_MODEL",
    "_admin_tool_names",
    "_admin_tool_schemas",
    "_assistant_message",
    "_assistant_text",
    "_assistant_thinking",
    "_brain_chat_config",
    "_brain_tool_policy",
    "_chat_stream",
    "_compact_board",
    "_completion_budget",
    "_dispatch_admin_tool",
    "_dispatch_platform_tool",
    "_dispatch_tool",
    "_extract_tool_calls",
    "_frame_messages",
    "_is_admin_tool_excluded",
    "_is_read_tool",
    "_parse_text_tool_calls",
    "_resolve_platform_tool",
    "_resolve_read_tool",
    "_resolve_tool",
    "_split_thinking",
    "_surfaced_tool_names",
    "_surfaced_tool_schemas",
    "_synthetic_reply",
    "_tool_intent_artefact",
    "_tool_reroute_unavailable_message",
    "_tool_routing_llm",
    "_tool_schemas",
    "_unrouteable_model_error",
    "run_board_chat",
    "run_brain_chat",
]
