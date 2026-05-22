"""hal0 admin MCP server — slots / models / capabilities / config / hardware.

Transport
---------

This module builds a Streamable-HTTP MCP server using the upstream
``mcp`` Python SDK (``mcp.server.fastmcp.FastMCP``) and exposes it as an
ASGI sub-application. The orchestrator team mounts it on the main
FastAPI app via ``app.mount("/mcp/admin", admin.asgi_app())``.

**Mount vs include_router.** We pick ``app.mount()`` (not
``include_router``) because the MCP SDK delivers a complete Starlette
app — including its own session manager, SSE/HTTP transports, and
``/messages`` writer — that we want to expose unmodified. Wrapping it
in an APIRouter would force us to re-export the SDK's internal route
table by hand and re-implement its lifespan hooks, which is exactly
the brittleness ADR-0004 §7 warns against. ``app.mount()`` cleanly
delegates everything below the mount path to the sub-app.

Tool catalog (ADR-0004 §4)
--------------------------

Autonomous read::

    slot_list, slot_status, model_list, hardware_probe, logs_tail,
    capability_list, provider_list, version_info

Autonomous write::

    model_swap, memory_add, memory_search, memory_list,
    memory_delete (when len(ids) == 1)

Gated (destructive — enqueued for owner approval)::

    model_pull, model_delete, slot_create, slot_delete, slot_restart,
    capability_set, config_write, provider_credential_write,
    memory_delete (when len(ids) > 1)

The memory_* tools are delegates that forward into
:mod:`hal0.mcp.memory` so we have a single tool surface per server
(the admin server hosts every tool an agent might call; the memory
server is a focused alternative mount that an agent can use when it
only needs memory access).

Authentication
--------------

The agent presents its Bearer token through the MCP transport's HTTP
headers. The server extracts ``client_id`` from that token by hitting
``/api/auth/me`` (same identity the dashboard sees) and stamps every
audit row with it. Internal API calls re-attach the same Bearer so we
honour the "no new privileged surface" rule from ADR-0004 §7 — an
agent can only do what its token already permits via REST.

Fail-fast import
----------------

When the ``mcp`` SDK is not installed (Memory-engine wave installs it
through pyproject.toml), importing this module raises a clear
ImportError with installation instructions. The orchestrator's
``include_router`` site catches it and degrades gracefully so an
install missing the SDK still boots — the dashboard surfaces the
"MCP unavailable" state instead of 500.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog

# ── Fail-fast SDK import ─────────────────────────────────────────────────────
#
# The mcp SDK is an optional dependency at the package level — only
# installed when Phase 8 is active. Importing this module without the
# SDK is a hard error: there is no degraded "no MCP" mode for the
# server module itself (the orchestrator decides whether to mount).
try:
    from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]
except ImportError as _import_exc:  # pragma: no cover — exercised at install time
    raise ImportError(
        "hal0.mcp.admin requires the 'mcp' Python SDK. "
        "Install via 'pip install mcp' or the Memory-engine wave's pyproject extras."
    ) from _import_exc

from hal0.mcp.approval_queue import ApprovalQueue

audit_log = structlog.get_logger("hal0.mcp.audit")
log = structlog.get_logger(__name__)


# ── Tool classification ──────────────────────────────────────────────────────

# Read-only tools — execute immediately, no approval prompt.
AUTONOMOUS_READ_TOOLS: frozenset[str] = frozenset(
    {
        "slot_list",
        "slot_status",
        "model_list",
        "hardware_probe",
        # logs_tail is intentionally NOT here — moved to GATED_TOOLS
        # until the ADR-0004 §7 redaction lands in logs.py. Per
        # security review MED-1: an agent dumping raw journald is a
        # potential exfiltration vector for whatever secrets the log
        # redactor doesn't yet cover. Gating now is defensive-cheap;
        # demote back to autonomous-read once the redaction is in.
        "capability_list",
        "provider_list",
        "version_info",
    }
)

# Mutating tools that are safe enough to run without approval
# (reversible, scoped, low blast radius). Per ADR-0004 §4.
AUTONOMOUS_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "model_swap",
        "memory_add",
        "memory_search",
        "memory_list",
        # memory_delete with len(ids) == 1 is autonomous; bulk goes
        # gated. The dispatch helper applies that rule at call time.
        "memory_delete",
    }
)

# Tools that always require approval.
GATED_TOOLS: frozenset[str] = frozenset(
    {
        "model_pull",
        "model_delete",
        "slot_create",
        "slot_delete",
        "slot_restart",
        "capability_set",
        "config_write",
        "provider_credential_write",
        # logs_tail is gated until the redactor in logs.py covers
        # Bearer + X-API-Key + provider keys (sk-/hf-/etc.) — see
        # docs/internal/phase-8-pending/mcp-backend.md §2.
        "logs_tail",
        # memory_delete with len(ids) > 1 routes here at call time.
    }
)


# ── REST passthrough mapping ─────────────────────────────────────────────────
#
# Each autonomous-read tool maps to an existing /api/* route. The MCP
# server forwards through httpx with the agent's Bearer; the REST layer
# owns authorization + validation. We do NOT duplicate that logic here.

# (method, path-template). Path templates use ``{arg_name}`` placeholders
# that we resolve from the tool call's args dict.
# NOTE — drift between ADR-0004 §4 and live REST routes (2026-05-22):
#
# ADR-0004 §4 names a few routes that don't exist verbatim. Where the
# ADR's stated URL doesn't match what ``hal0.api.routes`` actually
# exposes, we route to the live URL and flag the divergence in
# WAVE1_MCP_PENDING.md. The tool catalog itself stays ADR-faithful so
# agents see the documented names; only the HTTP target moves.
#
#   ADR §4                              Live route                    Note
#   ──────────────────────────────────  ─────────────────────────────  ────────────────
#   model_swap → /api/slots/{n}/model   /api/slots/{n}/swap           name diff
#   model_pull → /api/models/pull       /api/models/{id}/pull         id-in-path
#   capability_set → /api/capabilities  /api/capabilities/{slot}/{c}  composite key
#   provider_credential_write → /api/providers/{n}/credentials  NO LIVE ROUTE
#   version_info → /api/version         /api/status                   name diff

_REST_MAP: dict[str, tuple[str, str]] = {
    # Read
    "slot_list": ("GET", "/api/slots"),
    "slot_status": ("GET", "/api/slots/{name}"),
    "model_list": ("GET", "/api/models"),
    "hardware_probe": ("GET", "/api/stats/hardware"),
    "logs_tail": ("GET", "/api/logs"),
    "capability_list": ("GET", "/api/capabilities"),
    "provider_list": ("GET", "/api/providers"),
    "version_info": ("GET", "/api/status"),
    # Autonomous write
    "model_swap": ("POST", "/api/slots/{name}/swap"),
    # Gated write
    "model_pull": ("POST", "/api/models/{model_id}/pull"),
    "model_delete": ("DELETE", "/api/models/{model_id}"),
    "slot_create": ("POST", "/api/slots"),
    "slot_delete": ("DELETE", "/api/slots/{name}"),
    "slot_restart": ("POST", "/api/slots/{name}/restart"),
    "capability_set": ("POST", "/api/capabilities/{slot}/{child}"),
    "config_write": ("PUT", "/api/settings"),
    # No live route yet — Memory-engine / Provider team must land the
    # endpoint. We register the tool anyway so the catalog matches the
    # ADR; calls land in a 404 surface until the route exists.
    "provider_credential_write": ("POST", "/api/providers/{name}/credentials"),
}


# Path-arg keys per tool — pulled out of ``args`` for URL substitution;
# the remainder become query string (GET) or JSON body (POST/PUT/DELETE).
_PATH_ARGS: dict[str, tuple[str, ...]] = {
    "slot_status": ("name",),
    "model_swap": ("name",),
    "model_pull": ("model_id",),
    "model_delete": ("model_id",),
    "slot_delete": ("name",),
    "slot_restart": ("name",),
    "capability_set": ("slot", "child"),
    "provider_credential_write": ("name",),
}


def _split_args(tool: str, args: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    """Separate path-substitution args from body/query args.

    Returns ``(path_args, remainder)``. Missing path args raise
    ``KeyError`` so the caller surfaces a 400-style error rather than
    silently routing to a malformed URL.
    """
    path_keys = _PATH_ARGS.get(tool, ())
    path_args: dict[str, str] = {}
    remainder = dict(args)
    for key in path_keys:
        if key not in remainder:
            raise KeyError(f"tool {tool!r} requires arg {key!r}")
        path_args[key] = str(remainder.pop(key))
    return path_args, remainder


def _format_url(base_url: str, template: str, path_args: dict[str, str]) -> str:
    """Substitute ``{name}`` placeholders in ``template`` from ``path_args``."""
    return base_url.rstrip("/") + template.format(**path_args)


async def _call_rest(
    *,
    base_url: str,
    bearer: str | None,
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Forward an MCP tool call into the local REST API and return JSON.

    The Bearer header is re-attached unchanged so the REST layer's auth
    middleware sees exactly the credential the agent presented — no
    privilege elevation. Non-2xx responses raise the body as a typed
    error dict so the MCP client sees structured failure info instead
    of a generic "tool failed".
    """
    headers: dict[str, str] = {"Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    # CSRF tripwire opt-in — MCP requests are programmatic; the API
    # treats X-Requested-With as proof the call isn't a cross-origin
    # form post. Bearer-only paths bypass this anyway, but setting it
    # keeps the cookie-auth path open for future MCP-over-cookie
    # transports without re-issuing tokens.
    headers["X-Requested-With"] = "XMLHttpRequest"

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_s) as client:
        if method == "GET":
            response = await client.get(url, params=payload or None, headers=headers)
        elif method == "DELETE":
            response = await client.delete(url, params=payload or None, headers=headers)
        elif method == "POST":
            response = await client.post(url, json=payload or {}, headers=headers)
        elif method == "PUT":
            response = await client.put(url, json=payload or {}, headers=headers)
        else:
            raise ValueError(f"unsupported HTTP method: {method}")

    if response.status_code >= 400:
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = {"text": response.text}
        return {
            "status": "error",
            "http_status": response.status_code,
            "error": body,
        }
    try:
        return response.json()
    except json.JSONDecodeError:
        return {"text": response.text}


# ── Audit ────────────────────────────────────────────────────────────────────


def _audit(*, client_id: str, tool: str, args: dict[str, Any], gated: bool, outcome: str) -> None:
    """Emit a structured audit row for one MCP tool invocation.

    Routes through the ``hal0.mcp.audit`` logger which inherits the
    structlog config installed by the main API. That config already
    feeds journald, so we get persisted audit history for free.
    """
    audit_log.info(
        "mcp.tool.invoked",
        client_id=client_id,
        tool=tool,
        args=args,
        gated=gated,
        outcome=outcome,
        timestamp=time.time(),
    )


# ── Dispatch core ────────────────────────────────────────────────────────────


def is_gated(tool: str, args: dict[str, Any]) -> bool:
    """Classify a tool invocation as gated (needs approval) or autonomous.

    ``memory_delete`` is the only tool whose gating depends on args —
    single-id deletes run autonomously, bulk deletes (>1 id) gate. Every
    other tool's classification is static.
    """
    if tool in GATED_TOOLS:
        return True
    if tool == "memory_delete":
        ids = args.get("ids") or []
        return len(ids) > 1
    return False


async def dispatch(
    *,
    tool: str,
    args: dict[str, Any],
    client_id: str,
    bearer: str | None,
    base_url: str,
    approval_queue: ApprovalQueue,
    memory_dispatcher: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Run one tool. Autonomous tools execute now; gated tools enqueue.

    Returns the tool's JSON result for autonomous calls or
    ``{"status": "pending_approval", "approval_id": "..."}`` for gated
    ones.

    ``memory_dispatcher`` is the in-process callable the memory server
    exposes for direct invocation (avoiding the HTTP round-trip for
    Cognee calls). When ``None``, memory tools route through REST like
    everything else, which is the safer default.
    """
    if tool not in (AUTONOMOUS_READ_TOOLS | AUTONOMOUS_WRITE_TOOLS | GATED_TOOLS):
        return {"status": "error", "error": {"code": "mcp.unknown_tool", "tool": tool}}

    gated = is_gated(tool, args)

    if gated:
        # Build the bound executor that runs when the owner approves.
        async def _executor(approved_args: dict[str, Any]) -> dict[str, Any]:
            return await _execute_tool(
                tool=tool,
                args=approved_args,
                bearer=bearer,
                base_url=base_url,
                memory_dispatcher=memory_dispatcher,
            )

        approval_id = await approval_queue.enqueue(
            tool=tool,
            args=args,
            client_id=client_id,
            executor=_executor,
        )
        _audit(client_id=client_id, tool=tool, args=args, gated=True, outcome="enqueued")
        return {"status": "pending_approval", "approval_id": approval_id}

    # Autonomous — run immediately.
    result = await _execute_tool(
        tool=tool,
        args=args,
        bearer=bearer,
        base_url=base_url,
        memory_dispatcher=memory_dispatcher,
    )
    outcome = result.get("status", "ok") if isinstance(result, dict) else "ok"
    _audit(client_id=client_id, tool=tool, args=args, gated=False, outcome=outcome)
    return result


async def _execute_tool(
    *,
    tool: str,
    args: dict[str, Any],
    bearer: str | None,
    base_url: str,
    memory_dispatcher: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None,
) -> dict[str, Any]:
    """Actually run a tool (no gating, no audit — caller handles both).

    Memory tools take the in-process dispatcher when available so we
    don't bounce through HTTP for a Cognee call that runs in the same
    process. All other tools go through REST so the API's auth +
    validation layer is the single source of truth for permissions.
    """
    if tool.startswith("memory_") and memory_dispatcher is not None:
        return await memory_dispatcher(tool, args)

    if tool not in _REST_MAP:
        # memory_* tools without a dispatcher fall through to REST,
        # but we don't have REST routes for them yet — return a
        # diagnostic instead of routing nowhere.
        if tool.startswith("memory_"):
            return {
                "status": "error",
                "error": {"code": "mcp.memory_unconfigured", "tool": tool},
            }
        return {"status": "error", "error": {"code": "mcp.unmapped_tool", "tool": tool}}

    method, template = _REST_MAP[tool]
    try:
        path_args, remainder = _split_args(tool, args)
    except KeyError as exc:
        return {
            "status": "error",
            "error": {"code": "mcp.missing_arg", "detail": str(exc)},
        }
    url = _format_url(base_url, template, path_args)
    payload: dict[str, Any] | None = remainder if remainder else None
    return await _call_rest(
        base_url=base_url,
        bearer=bearer,
        method=method,
        url=url,
        payload=payload,
    )


# ── FastMCP server builder ───────────────────────────────────────────────────


def build_server(
    *,
    name: str = "hal0-admin",
    approval_queue: ApprovalQueue,
    base_url: str = "http://127.0.0.1:8080",
    memory_dispatcher: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    bearer_resolver: Callable[[], tuple[str | None, str]] | None = None,
) -> FastMCP:
    """Construct the hal0-admin FastMCP server.

    ``bearer_resolver`` is a hook the surrounding orchestrator uses to
    pull ``(bearer, client_id)`` out of the active MCP session's HTTP
    headers. We accept it as a callable so this module stays
    transport-agnostic — tests inject a fixed-value resolver and
    production wiring injects one that reads the request context.

    Every tool is registered with FastMCP's ``@tool`` decorator pattern
    so the SDK's standard discovery surface (``tools/list``) reports
    them to the agent. The decorator wraps the underlying ``dispatch``
    call so the same gating + audit pipeline runs regardless of which
    tool the agent picked.
    """
    server = FastMCP(name)

    def _resolve() -> tuple[str | None, str]:
        if bearer_resolver is None:
            return None, "anonymous"
        return bearer_resolver()

    # Single tool factory — every tool in the catalog dispatches into
    # the same ``dispatch`` core. We register each tool name explicitly
    # so FastMCP's tool listing reports them as distinct entries (vs.
    # a single catch-all tool that opaquely dispatches).
    def _register(tool_name: str, description: str) -> None:
        async def _tool(args: dict[str, Any] | None = None) -> dict[str, Any]:
            bearer, client_id = _resolve()
            return await dispatch(
                tool=tool_name,
                args=args or {},
                client_id=client_id,
                bearer=bearer,
                base_url=base_url,
                approval_queue=approval_queue,
                memory_dispatcher=memory_dispatcher,
            )

        _tool.__name__ = tool_name
        _tool.__doc__ = description
        server.tool(name=tool_name, description=description)(_tool)

    # Autonomous read
    _register("slot_list", "List every slot known to hal0 (local + remote).")
    _register("slot_status", "Get one slot's lifecycle state + metadata.")
    _register("model_list", "Aggregate models from local registry + upstreams.")
    _register("hardware_probe", "Live hardware probe — backends, memory, accelerators.")
    _register("logs_tail", "Tail journald for one systemd unit.")
    _register("capability_list", "Capability overlay state — backends + selections.")
    _register("provider_list", "List configured providers.")
    _register("version_info", "hal0 version + runtime status.")
    # Autonomous write
    _register("model_swap", "Hot-swap the primary slot to a new model.")
    _register("memory_add", "Add an item to long-term memory.")
    _register("memory_search", "Search long-term memory.")
    _register("memory_list", "Page through long-term memory items.")
    _register(
        "memory_delete",
        "Delete one or more memory items (autonomous when len(ids)==1, gated otherwise).",
    )
    # Gated
    _register("model_pull", "Pull a model into the local registry (gated).")
    _register("model_delete", "Delete a model from the local registry (gated).")
    _register("slot_create", "Create a new slot (gated).")
    _register("slot_delete", "Delete a slot (gated).")
    _register("slot_restart", "Restart a slot's systemd unit (gated).")
    _register("capability_set", "Assign a capability child to a slot (gated).")
    _register("config_write", "Update hal0.toml top-level settings (gated).")
    _register(
        "provider_credential_write",
        "Write provider credentials (gated; secrets never echoed back).",
    )

    return server


__all__ = [
    "AUTONOMOUS_READ_TOOLS",
    "AUTONOMOUS_WRITE_TOOLS",
    "GATED_TOOLS",
    "build_server",
    "dispatch",
    "is_gated",
]
