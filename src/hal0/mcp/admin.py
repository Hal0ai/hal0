"""hal0 admin MCP server — slots / models / capabilities / config / hardware.

Secret redaction
----------------

``logs_tail`` proxies journald output back to the agent. Journald lines
routinely carry Bearer tokens (HAL0_BEARER_TOKEN= rows on slot startup,
``Authorization: Bearer ...`` debug-level traces, third-party-provider
keys in error breadcrumbs). The MCP-backend security review (MED-1)
flagged this as a potential exfiltration vector — an agent that can
gate a single ``logs_tail`` approval inherits every secret the log
redactor doesn't yet cover.

We compile a single regex covering the three highest-frequency leak
shapes and apply it to every line the tool returns to the client.
Redaction happens in :func:`_redact_logs_payload` after the REST call
returns and before the dispatch envelope ships to the agent — keep the
logic localised to this module so future patterns slot in next to the
existing ones without touching :mod:`hal0.api.routes.logs`.

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

    slot_list, slot_status, slot_metrics, slot_capacity,
    model_list, model_show, model_scan_preview,
    model_catalogue, model_update_check, model_pulls_list,
    model_pull_status, model_inspect, model_store,
    hardware_probe, capability_list, provider_list, version_info,
    stack_list, stack_status, profile_list, profile_status,
    profile_export, upstream_list,
    settings_get, settings_schema, settings_apply_plan,
    bench_runs, bench_run_status, bench_queue,
    # Host-introspection probes (issue #237)
    gpu_target_version, npu_status, env_report, model_store_probe

Autonomous write::

    model_swap, model_assign, model_edit, model_scan,
    model_pull_cancel,
    slot_load, slot_unload, slot_edit,
    settings_reload,
    memory_add, memory_search, memory_list,
    memory_delete (when len(ids) == 1)

Gated (destructive — enqueued for owner approval)::

    model_pull, model_delete, model_register, model_add,
    model_store_set, model_store_migrate, model_update,
    slot_create, slot_delete, slot_restart,
    capability_set, config_write, provider_credential_write,
    memory_delete (when len(ids) > 1),
    # Profile CRUD (create/update/import/delete)
    profile_create, profile_update, profile_import, profile_delete,
    # Stack CRUD (create/update/apply/import/export/snapshot/delete)
    stack_create, stack_update, stack_apply, stack_import,
    stack_export, stack_snapshot, stack_delete,
    # Benchmarks — enqueue/control load models onto slots (disruptive)
    bench_enqueue, bench_control,
    # Journald surfaces gated for security (MED-1)
    logs_tail, slot_logs

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
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog

from hal0.mcp.approval_queue import ApprovalQueue
from hal0.mcp.probes import PROBE_TOOLS, dispatch_probe

# ── logs_tail secret redactor (security review MED-1) ────────────────────────
#
# Compiled once at import time. Each alternative ends with a
# ``(?P<...>...)`` capture of just the secret token; the substitution
# function rewrites that token to ``***REDACTED***`` while leaving the
# surrounding ``Authorization:``, ``Bearer``, or ``HAL0_BEARER_TOKEN=``
# prefix in place. The case-insensitive flag covers the lowercase
# ``authorization:`` header style some clients emit, and the explicit
# alternatives are ordered most-to-least specific so the precise header
# form wins over the bare-``Bearer`` fallback. (Python's re alternation
# is leftmost-wins inside a single match.)
_LOG_SECRET_RE = re.compile(
    r"(?P<prefix_auth>Authorization:\s*Bearer\s+)(?P<auth_token>\S+)"
    r"|(?P<prefix_env>HAL0_BEARER_TOKEN=)(?P<env_token>\S+)"
    r"|(?P<prefix_bearer>Bearer\s+)(?P<bearer_token>[A-Za-z0-9_\-\.]+)",
    re.IGNORECASE,
)


def _redact_log_line(line: str) -> str:
    """Replace Bearer / HAL0_BEARER_TOKEN secrets in ``line`` with
    ``***REDACTED***``.

    The prefix is preserved so an operator reading a redacted log still
    sees that an Authorization header was present — only the token
    body is destroyed.
    """

    def _sub(match: re.Match[str]) -> str:
        groups = match.groupdict()
        if groups["prefix_auth"] is not None:
            return f"{groups['prefix_auth']}***REDACTED***"
        if groups["prefix_env"] is not None:
            return f"{groups['prefix_env']}***REDACTED***"
        return f"{groups['prefix_bearer']}***REDACTED***"

    return _LOG_SECRET_RE.sub(_sub, line)


# ── List-shaped REST responses → top-level dict ──────────────────────────────
#
# FastMCP derives a structured-output result model from each tool's
# ``-> dict[str, Any]`` return annotation, and the MCP SDK validates the
# tool's return value against it. A bare top-level JSON *array* fails
# that DictModel validation with
# ``Input should be a valid dictionary [type=dict_type, ...]``.
#
# Most admin tools are fine because their REST route already returns an
# object (``model_list`` → ``/api/models`` → ``{"object": "list",
# "data": [...]}``). But two read routes return a bare list:
#
#   slot_list     → GET /api/slots     → list[dict]
#   provider_list → GET /api/providers → list[dict]
#
# so those two tools raised the DictModel error at the wrapper boundary.
# We wrap the list in a top-level object here — mirroring model_list's
# ``{<key>: [...], "count": N}`` style — keeping the per-item dicts the
# REST layer produced untouched. Maps tool name → the list's container
# key in the wrapped object; every other tool falls back to ``items``
# so a newly-mapped bare-list route can never crash the result model.
_LIST_TOOL_WRAP_KEY: dict[str, str] = {
    "slot_list": "slots",
    "provider_list": "providers",
    "upstream_list": "upstreams",
}


def _wrap_list_payload(tool: str, payload: Any) -> Any:
    """Wrap a bare-list REST response in a top-level dict for ``tool``.

    Some REST routes return a bare JSON array; the MCP result model
    requires a top-level object. We wrap the list as
    ``{<key>: [...], "count": len(list)}`` mirroring ``model_list``'s
    shape, with ``items`` as the generic fallback key. Non-list payloads
    (e.g. the ``_call_rest`` error envelope, which is already a dict)
    round-trip unchanged so we never mask a transport error.
    """
    if not isinstance(payload, list):
        return payload
    key = _LIST_TOOL_WRAP_KEY.get(tool, "items")
    return {key: payload, "count": len(payload)}


def _redact_logs_payload(payload: Any) -> Any:
    """Redact secrets from a journald-shaped response before it ships.

    Covers both shapes the log surfaces return: ``logs_tail``'s
    ``{"lines": [...]}`` list and ``slot_logs``'s ``{"logs": "..."}``
    single string. Non-dict payloads (or shapes missing both keys)
    round-trip unchanged so a transport error or alternative-shape
    envelope still reaches the agent — we never swallow content, only
    mask known secret tokens.
    """
    if not isinstance(payload, dict):
        return payload
    lines = payload.get("lines")
    if isinstance(lines, list):
        payload["lines"] = [
            _redact_log_line(line) if isinstance(line, str) else line for line in lines
        ]
    logs = payload.get("logs")
    if isinstance(logs, str):
        payload["logs"] = _redact_log_line(logs)
    return payload


# ── Fail-fast SDK import ─────────────────────────────────────────────────────
#
# The mcp SDK is an optional dependency at the package level — only
# installed when Phase 8 is active. Importing this module without the
# SDK is a hard error: there is no degraded "no MCP" mode for the
# server module itself (the orchestrator decides whether to mount).
try:
    from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]
    from mcp.types import ToolAnnotations  # type: ignore[import-not-found]
except ImportError as _import_exc:  # pragma: no cover — exercised at install time
    raise ImportError(
        "hal0.mcp.admin requires the 'mcp' Python SDK. "
        "Install via 'pip install mcp' or the Memory-engine wave's pyproject extras."
    ) from _import_exc

audit_log = structlog.get_logger("hal0.mcp.audit")
log = structlog.get_logger(__name__)


# ── Tool classification ──────────────────────────────────────────────────────

# Read-only tools — execute immediately, no approval prompt.
AUTONOMOUS_READ_TOOLS: frozenset[str] = frozenset(
    {
        # Slots
        "slot_list",
        "slot_status",
        "slot_metrics",
        "slot_capacity",
        # Models. model_scan is NOT here — walking the roots registers new
        # files (a mutation); model_scan_preview is the read-shaped dry run.
        # model_inspect is a read-shaped POST: it fetches HF repo metadata
        # and returns detection rows without registering anything.
        "model_list",
        "model_show",
        "model_scan_preview",
        "model_catalogue",
        "model_update_check",
        "model_pulls_list",
        "model_pull_status",
        "model_inspect",
        "model_store",
        # Hardware / system. logs_tail / slot_logs are intentionally NOT
        # here — journald output can carry secrets, so they stay in
        # GATED_TOOLS until the logs.py redactor covers every key shape
        # (security review MED-1).
        "hardware_probe",
        "capability_list",
        "provider_list",
        "version_info",
        "upstream_list",
        # Stacks
        "stack_list",
        "stack_status",
        # Profiles
        "profile_list",
        "profile_status",
        # profile_export is a POST but performs no state change — it
        # builds a portable envelope from existing catalog data, so it
        # classifies read-shaped (autonomous) like the other reads.
        "profile_export",
        # Settings
        "settings_get",
        "settings_schema",
        "settings_apply_plan",
        # Benchmarks — run history + queue state (reads; enqueue/control
        # are gated because a run loads models onto slots).
        "bench_runs",
        "bench_run_status",
        "bench_queue",
        # Host-introspection probes (issue #237). Pure-read against
        # /sys/, /proc/, and lsmod — no REST round-trip, no mutation.
        "gpu_target_version",
        "npu_status",
        "env_report",
        "model_store_probe",
    }
)

# Mutating tools that are safe enough to run without approval
# (reversible, scoped, low blast radius). Per ADR-0004 §4.
AUTONOMOUS_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        # Model. model_scan only ADDS registry entries for files already
        # on disk (reversible via model_delete); model_pull_cancel stops
        # an in-flight download the agent (or operator) started.
        "model_swap",
        "model_assign",
        "model_edit",
        "model_scan",
        "model_pull_cancel",
        # Slot lifecycle
        "slot_load",
        "slot_unload",
        "slot_edit",
        # Settings
        "settings_reload",
        # Memory
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
        # Model
        "model_pull",
        "model_delete",
        "model_register",
        "model_add",
        "model_store_set",
        "model_store_migrate",
        "model_update",
        # Slot
        "slot_create",
        "slot_delete",
        "slot_restart",
        # Capability / config
        "capability_set",
        "config_write",
        "provider_credential_write",
        # Stacks: applying/importing reconfigures the whole inference surface
        # (loads/swaps/unloads slots) and deleting drops a saved bundle —
        # owner-approval gated, mirroring slot_create/capability_set.
        "stack_create",
        "stack_update",
        "stack_apply",
        "stack_import",
        "stack_export",
        "stack_snapshot",
        "stack_delete",
        # Profiles: importing creates a new catalog entry and deleting
        # drops a saved profile — owner-approval gated, mirroring
        # stack_import/stack_delete.
        "profile_create",
        "profile_update",
        "profile_import",
        "profile_delete",
        # Benchmarks: enqueue/control load models onto slots and can evict
        # what's currently serving — disruptive, so owner-approval gated.
        "bench_enqueue",
        "bench_control",
        # logs_tail / slot_logs are gated until the redactor in logs.py
        # covers Bearer + X-API-Key + provider keys (sk-/hf-/etc.) — see
        # docs/internal/phase-8-pending/mcp-backend.md §2.
        "logs_tail",
        "slot_logs",
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
    # ── Slots ──────────────────────────────────────────────────────────
    "slot_list": ("GET", "/api/slots"),
    "slot_status": ("GET", "/api/slots/{name}"),
    "slot_metrics": ("GET", "/api/slots/metrics"),
    "slot_capacity": ("GET", "/api/slots/capacity"),
    "slot_load": ("POST", "/api/slots/{name}/load"),
    "slot_unload": ("POST", "/api/slots/{name}/unload"),
    "slot_edit": ("PUT", "/api/slots/{name}/config"),
    "slot_logs": ("GET", "/api/slots/{name}/logs"),
    # ── Models ─────────────────────────────────────────────────────────
    "model_list": ("GET", "/api/models"),
    "model_show": ("GET", "/api/models/{model_id}"),
    "model_scan": ("POST", "/api/models/scan"),
    "model_scan_preview": ("POST", "/api/models/scan/preview"),
    "model_catalogue": ("GET", "/api/models/catalogue"),
    "model_update_check": ("GET", "/api/models/updates/check"),
    "model_pulls_list": ("GET", "/api/models/pulls"),
    "model_pull_status": ("GET", "/api/models/{model_id}/pull/status"),
    # model_inspect is a read-shaped POST — fetches HF repo metadata and
    # returns detection rows without touching the registry.
    "model_inspect": ("POST", "/api/models/inspect"),
    "model_store": ("GET", "/api/settings/models/store"),
    # ── Profiles ───────────────────────────────────────────────────────
    "profile_list": ("GET", "/api/profiles"),
    "profile_status": ("GET", "/api/profiles/{name}"),
    "profile_export": ("POST", "/api/profiles/{name}/export"),
    # ── Stacks ─────────────────────────────────────────────────────────
    "stack_list": ("GET", "/api/stacks"),
    "stack_status": ("GET", "/api/stacks/{slug}"),
    # ── Settings ───────────────────────────────────────────────────────
    "settings_get": ("GET", "/api/settings"),
    "settings_schema": ("GET", "/api/settings/schema"),
    "settings_apply_plan": ("GET", "/api/settings/apply-plan"),
    "settings_reload": ("POST", "/api/settings/reload"),
    # ── Benchmarks ─────────────────────────────────────────────────────
    "bench_runs": ("GET", "/api/benchmarks/runs"),
    "bench_run_status": ("GET", "/api/benchmarks/runs/{run_id}"),
    "bench_queue": ("GET", "/api/benchmarks/queue"),
    "bench_enqueue": ("POST", "/api/benchmarks/queue"),
    "bench_control": ("POST", "/api/benchmarks/control"),
    # ── System ─────────────────────────────────────────────────────────
    "version_info": ("GET", "/api/status"),
    "upstream_list": ("GET", "/api/upstreams"),
    "hardware_probe": ("GET", "/api/stats/hardware"),
    "logs_tail": ("GET", "/api/logs"),
    "capability_list": ("GET", "/api/capabilities"),
    "provider_list": ("GET", "/api/providers"),
    # ── Autonomous write ───────────────────────────────────────────────
    "model_swap": ("POST", "/api/slots/{name}/swap"),
    "model_assign": ("PUT", "/api/slots/{name}/config"),
    # model_edit is the metadata PUT (name/caps/tags/mmproj enrichment);
    # model_update (gated, below) is the in-place HF re-pull POST — the
    # two routes are easy to cross, keep them adjacent to the comment.
    "model_edit": ("PUT", "/api/models/{model_id}"),
    "model_pull_cancel": ("POST", "/api/models/{model_id}/pull/cancel"),
    # ── Gated write ────────────────────────────────────────────────────
    "model_pull": ("POST", "/api/models/{model_id}/pull"),
    "model_delete": ("DELETE", "/api/models/{model_id}"),
    "model_register": ("POST", "/api/models"),
    "model_add": ("POST", "/api/models/add-from-path"),
    "model_store_set": ("POST", "/api/settings/models/store"),
    "model_store_migrate": ("POST", "/api/settings/models/store/migrate"),
    "model_update": ("POST", "/api/models/{model_id}/update"),
    "slot_create": ("POST", "/api/slots"),
    "slot_delete": ("DELETE", "/api/slots/{name}"),
    "slot_restart": ("POST", "/api/slots/{name}/restart"),
    "capability_set": ("POST", "/api/capabilities/{slot}/{child}"),
    "config_write": ("PUT", "/api/settings"),
    "stack_create": ("POST", "/api/stacks"),
    "stack_update": ("PUT", "/api/stacks/{slug}"),
    "stack_apply": ("POST", "/api/stacks/{slug}/apply"),
    "stack_import": ("POST", "/api/stacks/import"),
    "stack_export": ("POST", "/api/stacks/{slug}/export"),
    "stack_snapshot": ("POST", "/api/stacks/snapshot"),
    "stack_delete": ("DELETE", "/api/stacks/{slug}"),
    "profile_create": ("POST", "/api/profiles"),
    "profile_update": ("PUT", "/api/profiles/{name}"),
    "profile_import": ("POST", "/api/profiles/import"),
    "profile_delete": ("DELETE", "/api/profiles/{name}"),
    "provider_credential_write": ("POST", "/api/providers/{name}/credentials"),
}


# Path-arg keys per tool — pulled out of ``args`` for URL substitution;
# the remainder become query string (GET) or JSON body (POST/PUT/DELETE).
_PATH_ARGS: dict[str, tuple[str, ...]] = {
    # Slots
    "slot_status": ("name",),
    "slot_load": ("name",),
    "slot_unload": ("name",),
    "slot_edit": ("name",),
    "slot_logs": ("name",),
    "slot_restart": ("name",),
    "slot_delete": ("name",),
    "model_swap": ("name",),
    "model_assign": ("name",),
    # Models
    "model_show": ("model_id",),
    "model_pull": ("model_id",),
    "model_pull_status": ("model_id",),
    "model_pull_cancel": ("model_id",),
    "model_delete": ("model_id",),
    "model_edit": ("model_id",),
    "model_update": ("model_id",),
    # Benchmarks
    "bench_run_status": ("run_id",),
    # Profiles
    "profile_status": ("name",),
    "profile_export": ("name",),
    "profile_update": ("name",),
    "profile_delete": ("name",),
    # Stacks
    "stack_status": ("slug",),
    "stack_apply": ("slug",),
    "stack_export": ("slug",),
    "stack_update": ("slug",),
    "stack_delete": ("slug",),
    # Misc
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

    # Host-introspection probes run in-process — no REST hop. See
    # :mod:`hal0.mcp.probes` for the per-probe implementations.
    if tool in PROBE_TOOLS:
        return await dispatch_probe(tool, args)

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
    result = await _call_rest(
        base_url=base_url,
        bearer=bearer,
        method=method,
        url=url,
        payload=payload,
    )
    # Redact every Bearer / HAL0_BEARER_TOKEN occurrence before a
    # journald-backed response leaves this process. The REST routes
    # themselves stay unredacted — REST consumers on the same host
    # already have credential access; the MCP transport is the spot
    # where a narrowly-scoped agent can otherwise siphon tokens out
    # (security review MED-1). slot_logs proxies the same journald
    # surface per-unit, so it gets the same treatment.
    if tool in ("logs_tail", "slot_logs"):
        result = _redact_logs_payload(result)
    # Wrap bare-list REST responses (slot_list / provider_list) into a
    # top-level dict so the FastMCP result model validates. No-op for
    # every other tool and for the already-dict error envelope.
    result = _wrap_list_payload(tool, result)
    return result


# ── Tool annotations (mcp-builder Phase 2.3) ─────────────────────────────────
#
# Per the MCP spec, every tool advertises four behavioural hints so the
# client can pick the right warning UX before invocation:
#
#   readOnlyHint     — call doesn't mutate hal0 state.
#   destructiveHint  — meaningful only when readOnly=False; true if the
#                      call removes data or deletes a resource.
#   idempotentHint   — repeated calls with the same args leave the same
#                      end state (true for "set X to Y" semantics).
#   openWorldHint    — call reaches outside hal0's own surface (e.g.
#                      pulling weights from HuggingFace).
#
# These are advisory — server-side gating in :func:`is_gated` is still
# the authoritative policy. The annotations exist so MCP clients render
# the right approval-prompt language without having to read ADR-0004.

_ANNOTATIONS: dict[str, ToolAnnotations] = {
    # ── Autonomous read — pure reads against the local REST surface. ─────
    # Slots
    "slot_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "slot_status": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "slot_metrics": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "slot_capacity": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # Models
    "model_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "model_show": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "model_scan_preview": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "model_catalogue": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "model_update_check": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "model_pulls_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "model_pull_status": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # Read-shaped POST that fetches HF repo metadata — open-world.
    "model_inspect": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
    "model_store": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # System. logs_tail / slot_logs read-only but server-gated (MED-1).
    "hardware_probe": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "logs_tail": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "slot_logs": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "capability_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "provider_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "version_info": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "upstream_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # Stacks
    "stack_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "stack_status": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # Profiles
    "profile_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "profile_status": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "profile_export": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # Settings
    "settings_get": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "settings_schema": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "settings_apply_plan": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # Benchmarks — run history + queue reads.
    "bench_runs": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "bench_run_status": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "bench_queue": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # Host-introspection probes — pure sysfs/procfs reads.
    "gpu_target_version": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "npu_status": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "env_report": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "model_store_probe": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # Read-shaped memory tools — surface a Cognee query, no writes.
    "memory_search": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "memory_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # ── Autonomous write — mutating, reversible, idempotent writes. ────
    "model_swap": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "model_assign": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "model_edit": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # Additive registration of files already on disk; re-scan converges.
    "model_scan": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "model_pull_cancel": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "slot_load": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "slot_unload": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "slot_edit": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "settings_reload": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "capability_set": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "config_write": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "provider_credential_write": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # ── Gated write — mutating, non-idempotent or destructive. ────────
    # Apply converges to a declared end-state (idempotent); import/create
    # are non-idempotent (re-import/create conflicts on slug/name).
    "stack_apply": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "stack_import": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "stack_create": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "stack_update": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "stack_export": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "stack_snapshot": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "profile_import": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "profile_create": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "profile_update": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # Mutating, reversible, non-idempotent (each call has additional effect).
    "memory_add": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "slot_create": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "slot_restart": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    # Reaches outside hal0 (HuggingFace / upstream registries).
    "model_pull": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
    "model_register": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "model_add": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "model_store_set": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "model_store_migrate": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # In-place HF re-pull — hits the open network like model_pull.
    "model_update": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
    # Benchmarks — enqueue adds a run (non-idempotent); control start/stop
    # converges the runner to the requested state.
    "bench_enqueue": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "bench_control": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # Destructive — re-delete is a no-op so idempotentHint stays true.
    "model_delete": ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    ),
    "slot_delete": ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    ),
    "stack_delete": ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    ),
    "profile_delete": ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    ),
    "memory_delete": ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    ),
}


# ── Catalog consistency guard ────────────────────────────────────────────────
#
# The tool surface is spread over four tables (classification frozensets,
# _REST_MAP, _PATH_ARGS, _ANNOTATIONS) plus the _register calls in
# build_server. A tool landing in some tables but not others fails at
# call time with an opaque envelope — validate coherence at import so
# drift surfaces in CI, not in an agent's chat.


def _validate_catalog() -> None:
    catalog = AUTONOMOUS_READ_TOOLS | AUTONOMOUS_WRITE_TOOLS | GATED_TOOLS
    problems: list[str] = []

    overlaps = (
        (AUTONOMOUS_READ_TOOLS & AUTONOMOUS_WRITE_TOOLS)
        | (AUTONOMOUS_READ_TOOLS & GATED_TOOLS)
        | (AUTONOMOUS_WRITE_TOOLS & GATED_TOOLS)
    )
    if overlaps:
        problems.append(f"tools in more than one classification: {sorted(overlaps)}")

    # memory_* dispatch in-process (or report unconfigured); probes never
    # touch REST. Everything else must route somewhere.
    routed = {t for t in catalog if not t.startswith("memory_")} - PROBE_TOOLS
    if unmapped := routed - set(_REST_MAP):
        problems.append(f"classified but missing from _REST_MAP: {sorted(unmapped)}")
    if unclassified := set(_REST_MAP) - catalog:
        problems.append(f"in _REST_MAP but never classified: {sorted(unclassified)}")
    if unannotated := catalog - set(_ANNOTATIONS):
        problems.append(f"classified but missing ToolAnnotations: {sorted(unannotated)}")

    for tool, (_method, template) in _REST_MAP.items():
        placeholders = set(re.findall(r"{(\w+)}", template))
        declared = set(_PATH_ARGS.get(tool, ()))
        if placeholders != declared:
            problems.append(
                f"{tool}: _PATH_ARGS {sorted(declared)} != template placeholders "
                f"{sorted(placeholders)}"
            )

    if problems:
        raise RuntimeError("hal0.mcp.admin catalog drift: " + " | ".join(problems))


_validate_catalog()


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
    registered: set[str] = set()

    def _register(tool_name: str, description: str) -> None:
        if tool_name in registered:
            raise RuntimeError(f"hal0.mcp.admin: duplicate tool registration {tool_name!r}")
        registered.add(tool_name)

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
        annotations = _ANNOTATIONS.get(tool_name)
        server.tool(name=tool_name, description=description, annotations=annotations)(_tool)

    # ── Autonomous read ────────────────────────────────────────────────
    # Slots
    _register("slot_list", "List every slot known to hal0 (local + remote).")
    _register("slot_status", "Get one slot's lifecycle state + metadata.")
    _register("slot_metrics", "Get per-slot performance metrics (tok/s, latency, queue depth).")
    _register("slot_capacity", "Get GPU/NPU memory capacity and per-slot allocation.")
    # Models
    _register("model_list", "Aggregate models from local registry + upstreams.")
    _register("model_show", "Show a single model's full metadata (registry + upstream).")
    _register("model_scan_preview", "Preview what a model scan would register (dry run).")
    _register("model_catalogue", "List the curated catalogue of blessed models.")
    _register("model_update_check", "Check which HF-backed models have updates available.")
    _register("model_pulls_list", "List all pull jobs (in-flight + terminal).")
    _register("model_pull_status", "Get one model's pull-job progress (state, %, speed, ETA).")
    _register(
        "model_inspect",
        "Inspect a HuggingFace repo — returns detected .gguf/.mmproj files, quants and "
        "capabilities WITHOUT registering anything. Use before model_register/model_pull.",
    )
    _register(
        "model_store",
        "Show the operator's configured model-store directory ([models].store) and "
        "candidate paths. Pulls always download into this store.",
    )
    # System
    _register("hardware_probe", "Live hardware probe — backends, memory, accelerators.")
    _register("capability_list", "Capability overlay state — backends + selections.")
    _register("provider_list", "List configured providers.")
    _register("version_info", "hal0 version + runtime status.")
    _register("upstream_list", "List all configured upstream LLM providers.")
    # Stacks
    _register("stack_list", "List every stack, with the active stack + drift status.")
    _register("stack_status", "Get one stack's detail, active flag, and drift status.")
    # Profiles
    _register("profile_list", "List every profile in the catalog (seed + custom).")
    _register(
        "profile_status",
        "Get one profile's resolved detail (image, flags, backend, used-by).",
    )
    # profile_export is a read-shaped POST — builds the envelope, no state change.
    _register(
        "profile_export",
        "Export a profile to a portable .hal0profile.json envelope (no secrets/host-paths).",
    )
    # Settings
    _register("settings_get", "Get the current hal0 settings (pydantic schema validated).")
    _register("settings_schema", "Get the JSON schema for hal0 settings validation.")
    _register("settings_apply_plan", "Preview what settings changes would take effect.")
    # Benchmarks
    _register("bench_runs", "List benchmark runs (history + in-flight).")
    _register("bench_run_status", "Get one benchmark run's detail + results.")
    _register("bench_queue", "Show the benchmark queue (pending cells).")
    # Host-introspection probes (issue #237)
    _register(
        "gpu_target_version",
        "Decode KFD's gfx_target_version to a gfxNNNN string (e.g. gfx1151).",
    )
    _register(
        "npu_status",
        "Report XDNA NPU presence + driver binding (LXC-correct, no modinfo).",
    )
    _register(
        "env_report",
        "Composite host snapshot — container, CPU, RAM, GPU, NPU, network, tooling.",
    )
    _register(
        "model_store_probe",
        "Probe a model-store path: fstype, free/total bytes, writable, UMA-aware.",
    )
    # ── Autonomous write ───────────────────────────────────────────────
    # Model
    _register("model_swap", "Hot-swap the primary slot to a new model.")
    _register(
        "model_assign",
        "Assign a model to a slot's default (does not load the slot).",
    )
    _register(
        "model_edit",
        "Update a model's metadata (name, capabilities, tags, mmproj, defaults).",
    )
    _register(
        "model_scan",
        "Walk the configured model roots + store and register newly-found files.",
    )
    _register(
        "model_pull_cancel",
        "Cancel an in-flight model pull job.",
    )
    # Slot lifecycle
    _register(
        "slot_load",
        "Load a slot (optionally assign a model first).",
    )
    _register(
        "slot_unload",
        "Unload a running slot gracefully.",
    )
    _register(
        "slot_edit",
        "Update one or more slot config fields (model, port, ctx-size, provider, hardware).",
    )
    # Settings
    _register(
        "settings_reload",
        "Ask the running hal0 daemon to reload configs (re-reads TOMLs).",
    )
    # Memory
    _register("memory_add", "Add an item to long-term memory.")
    _register("memory_search", "Search long-term memory.")
    _register("memory_list", "Page through long-term memory items.")
    _register(
        "memory_delete",
        "Delete one or more memory items (autonomous when len(ids)==1, gated otherwise).",
    )
    # ── Gated write ────────────────────────────────────────────────────
    # Model
    _register(
        "model_pull",
        "Download a model from HuggingFace into the operator's configured "
        "model store ([models].store — check model_store first). Accepts "
        "hf_repo/hf_filename/mmproj_filename body overrides for new models "
        "(gated).",
    )
    _register("model_delete", "Delete a model from the local registry (gated).")
    _register(
        "model_register",
        "Register a model that's already on disk into the local registry (gated).",
    )
    _register(
        "model_add",
        "Register an already-downloaded model file — capabilities auto-detected (gated).",
    )
    _register(
        "model_store_set",
        "Set or change the model-store directory — future pulls download "
        "there; existing files stay until model_store_migrate (gated).",
    )
    _register(
        "model_store_migrate",
        "Migrate model files from the current store to a new path (gated).",
    )
    _register(
        "model_update",
        "Re-pull a model's HF file over its installed bytes (in place) (gated).",
    )
    # Slot
    _register("slot_create", "Create a new slot (gated).")
    _register("slot_delete", "Delete a slot (gated).")
    _register("slot_restart", "Restart a slot's systemd unit (gated).")
    # Capability / config
    _register("capability_set", "Assign a capability child to a slot (gated).")
    _register("config_write", "Update hal0.toml top-level settings (gated).")
    _register(
        "provider_credential_write",
        "Write provider credentials (gated; secrets never echoed back).",
    )
    # Stacks
    _register(
        "stack_create",
        "Create a stack from a slug + full stack body (gated).",
    )
    _register(
        "stack_update",
        "Replace a custom stack's body wholesale (gated).",
    )
    _register(
        "stack_apply",
        "Apply a stack — commit its slot config and converge runtime to match (gated).",
    )
    _register("stack_import", "Import a stack from a .hal0stack.json envelope (gated).")
    _register(
        "stack_export",
        "Serialize a stack into its portable .hal0stack.json envelope (gated).",
    )
    _register(
        "stack_snapshot",
        "Build a stack from the current live slot + capability config (gated).",
    )
    _register("stack_delete", "Delete a custom stack from the catalog (gated).")
    # Profiles
    _register(
        "profile_create",
        "Create a custom runtime profile (image, flags, device class) — e.g. "
        "authored from a model card's requirements (gated).",
    )
    _register(
        "profile_update",
        "Update an existing custom profile (shallow merge) (gated).",
    )
    _register(
        "profile_import",
        "Import a profile from a .hal0profile.json envelope (gated).",
    )
    _register("profile_delete", "Delete a custom profile from the catalog (gated).")
    # Benchmarks — runs load models onto slots (disruptive)
    _register(
        "bench_enqueue",
        "Enqueue benchmark cells (model x slot x settings) for the runner (gated).",
    )
    _register(
        "bench_control",
        "Start/stop/pause the benchmark runner (gated).",
    )
    # Journald surfaces gated for security (MED-1)
    _register("logs_tail", "Tail journald for one systemd unit (gated).")
    _register("slot_logs", "Tail one slot's journal output (gated).")

    # Every classified tool must be discoverable via tools/list and vice
    # versa — a registration gap otherwise 404s at call time only.
    catalog = AUTONOMOUS_READ_TOOLS | AUTONOMOUS_WRITE_TOOLS | GATED_TOOLS
    if registered != catalog:
        raise RuntimeError(
            "hal0.mcp.admin: registration drift — "
            f"unregistered: {sorted(catalog - registered)}, "
            f"unclassified: {sorted(registered - catalog)}"
        )

    return server


__all__ = [
    "AUTONOMOUS_READ_TOOLS",
    "AUTONOMOUS_WRITE_TOOLS",
    "GATED_TOOLS",
    "_ANNOTATIONS",
    "build_server",
    "dispatch",
    "is_gated",
]
