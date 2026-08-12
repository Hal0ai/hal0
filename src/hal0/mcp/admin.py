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

A single regex (:data:`hal0.api._redact.LOG_SECRET_RE`) covers the
highest-frequency leak shapes and is applied to every line the tool
returns to the client. Redaction happens in
:func:`_redact_logs_payload` after the REST call returns and before
the dispatch envelope ships to the agent.

api-logs-redact (Phase 1) found a second, independent leak path: the
plain REST ``GET /api/logs`` / ``GET /api/logs/stream`` surface in
:mod:`hal0.api.routes.logs` streamed the same journald output with
zero redaction. Rather than duplicate the regex there, the redactor
moved to :mod:`hal0.api._redact` (dependency-free) so both surfaces
import the one true implementation — see that module for the pattern
and :func:`hal0.api.routes.logs.journalctl_sse` / ``list_logs`` for
the REST-side wiring.

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
the kind of brittleness we want to avoid. ``app.mount()`` cleanly
delegates everything below the mount path to the sub-app.

Tool catalog
------------

Autonomous read::

    slot_list, slot_status, slot_metrics, slot_capacity,
    slot_by_name, slot_by_id, slot_resolved, slot_state,
    model_list, model_show, model_scan_preview,
    model_catalogue, model_update_check, model_pulls_list,
    model_pull_status, model_inspect, model_store,
    hardware_probe, system_info, capability_list, provider_list, version_info,
    stack_list, stack_status, profile_list, profile_status,
    profile_export, upstream_list, upstream_get, upstream_test,
    settings_get, settings_schema, settings_apply_plan,
    bench_runs, bench_run_status, bench_queue,
    # Host-introspection probes (issue #237)
    gpu_target_version, npu_status, env_report, model_store_probe,
    # Memory reads — readOnly per their MCP annotations (moved out of
    # autonomous-write in the §4.3 buildout; they never mutate state).
    # memory_reflect/history/mental_model_{list,get}/directive_{list,get}/
    # operation_{list,get}/tags_list/bank_stats are the Hindsight 0.8.4
    # parity buildout's other 10 reads — see TOOL_DESCRIPTIONS.
    memory_search, memory_list, memory_recall

Autonomous write::

    model_swap, model_assign, model_edit, model_scan,
    model_pull_cancel, model_pull_delete,
    model_set_default,
    slot_load, slot_unload, slot_edit, slot_set_defaults,
    settings_reload,
    memory_add,
    memory_delete (single id, default/own-namespace dataset only),
    # Hindsight 0.8.4 parity buildout's other 9 writes — reversible,
    # non-destructive (curate/mental-model/directive CRUD minus delete,
    # operation cancel/retry, bank consolidate) — see TOOL_DESCRIPTIONS.
    memory_curate, memory_mental_model_create, memory_mental_model_update,
    memory_mental_model_refresh, memory_directive_create,
    memory_directive_update, memory_operation_cancel, memory_operation_retry,
    memory_bank_consolidate

Gated (destructive — enqueued for owner approval)::

    model_pull, model_delete, model_register, model_add,
    model_store_set, model_store_migrate, model_update,
    slot_create, slot_delete, slot_restart, slot_rename,
    capability_set, config_write, provider_credential_write,
    memory_delete (bulk ids, OR a list/foreign-namespace dataset — #8),
    memory_mental_model_delete, memory_directive_delete,
    # Profile CRUD (create/update/import/delete)
    profile_create, profile_update, profile_import, profile_delete,
    # Stack CRUD (create/update/apply/import/export/snapshot/delete)
    stack_create, stack_update, stack_apply, stack_import,
    stack_export, stack_snapshot, stack_delete,
    # Upstream provider CRUD (create/update/delete; test is a read)
    upstream_create, upstream_update, upstream_delete,
    # Benchmarks — enqueue/control load models onto slots (disruptive);
    # queue-item delete drops a pending cell before it runs
    bench_enqueue, bench_control, bench_queue_delete,
    # Journald surfaces gated for security (MED-1)
    logs_tail, slot_logs

Platform-management expansion
------------------------------

The lists above are the original ADR-0004 §4 catalog; a later buildout
widened coverage to every major management domain: services, ComfyUI,
updater/doctor/health/features/metrics (reads only), hardware/telemetry,
the slots/models/benchmarks long-tails, journal/activity, approvals
(read-only), runner images, backends (incl. dynamic NPU slots), MCP
self-management, and profile_generate. Rather than hand-duplicate ~65
more tool names here (guaranteed to drift), see :data:`TOOL_DESCRIPTIONS`
for the single source of truth, or grep this module for "Platform-
management expansion" section markers in each table below.

The memory_* tools (26 total — the original 5 plus the Hindsight 0.8.4
parity buildout's reflect/curate/history, mental-model CRUD+refresh,
directive CRUD, operation list/get/cancel/retry, and tags/bank-stats/
bank-consolidate) are delegates that forward into :mod:`hal0.mcp.memory`
so we have a single tool surface per server (the admin server hosts
every tool an agent might call; the memory server is a focused
alternative mount that an agent can use when it only needs memory
access). Descriptions and ToolAnnotations for all 26 are owned by
:mod:`hal0.mcp.memory` and imported verbatim (``_MEMORY_TOOL_ANNOTATIONS``)
rather than hand-copied — only the read/write/gated classification and
the agent-facing :data:`TOOL_DESCRIPTIONS` summaries live here.

Deliberate exclusions
----------------------

Some REST/CLI-level capabilities are intentionally NOT surfaced as admin
MCP tools (policy decisions, not coverage gaps) — see
:data:`EXCLUDED_TOOLS` for the full name → reason table (board CRUD,
brain chat, the updater surface, auth rotation, credential reads, agent
session administration).

Authentication
--------------

The agent presents its Bearer token through the MCP transport's HTTP
headers. The server extracts ``client_id`` from that token by hitting
``/api/auth/me`` (same identity the dashboard sees) and stamps every
audit row with it. Internal API calls re-attach the same Bearer so we
honour the "no new privileged surface" rule — an
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

import fnmatch
import json
import re
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from hal0.api._redact import redact_log_line as _redact_log_line
from hal0.mcp.approval_queue import ApprovalQueue
from hal0.mcp.memory import _ANNOTATIONS as _MEMORY_TOOL_ANNOTATIONS
from hal0.mcp.probes import PROBE_TOOLS, dispatch_probe
from hal0.memory.namespace import is_known_namespace
from hal0.slot_lifecycle_budget import slot_lifecycle_timeout_s

# ── logs_tail secret redactor (security review MED-1) ────────────────────────
#
# Moved to :mod:`hal0.api._redact` (as ``redact_log_line`` /
# ``LOG_SECRET_RE``) so ``hal0.api.routes.logs`` — the plain REST
# ``/api/logs`` + ``/api/logs/stream`` surface, mounted on every install
# — can reuse the exact same secret-scrubbing logic without importing
# this module, which hard-fails at import time when the optional
# ``mcp`` SDK isn't installed (see the "Fail-fast import" section
# below). Re-imported here under the original private name so the rest
# of this module (and the existing test suite, which pokes
# ``admin._redact_log_line`` directly) is unchanged (api-logs-redact).


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
    # GET /api/runner-images/pulls/list -> list[dict] (all pull jobs).
    "runner_image_pulls": "pulls",
    # GET /api/backends -> list[dict] (one row per available backend).
    "backend_list": "backends",
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
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[import-not-found]
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
        # Canonical name/id-keyed lookups (rework §11.1) — identical
        # payload shape to slot_status, just a different key.
        "slot_by_name",
        "slot_by_id",
        # Auditable resolved-argv view + the lightweight state-machine
        # poll — both pure reads, no REST mutation.
        "slot_resolved",
        "slot_state",
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
        "system_info",
        "port_list",
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
        # Upstream provider reads (upstream_list is with the other list
        # reads above); test probes the provider but changes no state.
        "upstream_get",
        "upstream_test",
        # Memory reads — moved out of AUTONOMOUS_WRITE_TOOLS (§4.3): their
        # ToolAnnotations have always said readOnlyHint=True, the
        # classification bucket just hadn't caught up. memory_recall is
        # new here — the handler already exists in hal0.mcp.memory's
        # _MEMORY_HANDLERS, this catalog just didn't advertise it yet.
        "memory_search",
        "memory_list",
        "memory_recall",
        # Memory — Hindsight 0.8.4 parity buildout (26-tool catalog). These
        # 10 are read-only per hal0.mcp.memory's own ToolAnnotations
        # (imported verbatim as _MEMORY_TOOL_ANNOTATIONS below).
        "memory_reflect",
        "memory_history",
        "memory_mental_model_list",
        "memory_mental_model_get",
        "memory_directive_list",
        "memory_directive_get",
        "memory_operation_list",
        "memory_operation_get",
        "memory_tags_list",
        "memory_bank_stats",
        # Services
        "service_list",
        "service_health",
        # ComfyUI
        "comfyui_status",
        "comfyui_workflows",
        # Updater / doctor / health / features / metrics
        "updater_state",
        "updater_check",
        "updater_channel_get",
        "updater_slot_drift",
        "updater_job_status",
        "doctor_report",
        "health_system",
        "feature_list",
        "metrics_snapshot",
        # Hardware / telemetry
        "hardware_snapshot",
        "slot_stats",
        "stats_overview",
        "request_metrics",
        "system_stats",
        "power_stats",
        "throughput_history",
        "npu_occupancy",
        "npu_swap_status",
        "model_health_check",
        # Slots long-tail
        "slot_config",
        "slot_voices",
        "flm_model_list",
        "slot_pull_status",
        # Models long-tail. model_validate is a pure dry-run screen (never
        # writes — confirmed against models.py:427's implementation).
        "model_validate",
        "hf_search",
        "model_config_read",
        # Benchmarks long-tail
        "bench_roster",
        "bench_plan",
        "bench_cells",
        "bench_history",
        "bench_evals",
        # Journal / activity
        "journal_snapshot",
        "activity_list",
        "activity_export",
        # Approvals — list only; approve/deny are excluded (operator-only).
        "approval_list",
        # Runner images / backends
        "runner_image_list",
        "runner_image_downloaded",
        "runner_image_pulls",
        "backend_list",
        "backend_detail",
        # MCP self-management
        "mcp_server_list",
        "mcp_client_list",
        "mcp_catalog",
        # Profiles — read-shaped POST (compute-only, like model_inspect).
        "profile_generate",
    }
)

# Mutating tools that are safe enough to run without approval
# (reversible, scoped, low blast radius).
AUTONOMOUS_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        # Model. model_scan ADDS registry entries for files on disk
        # (reversible via model_delete); with prune=true it also removes
        # rows whose file is missing on disk — but slot/stack-referenced
        # rows are protected and only reported, never auto-deleted, so the
        # blast radius stays low. model_pull_cancel stops an in-flight
        # download the agent (or operator) started.
        "model_swap",
        "model_assign",
        "model_edit",
        "model_scan",
        "model_pull_cancel",
        # Clears a TERMINAL pull job's bookkeeping only (409s if still
        # queued/running) — no bytes on disk are touched, and the job can
        # always be re-started via model_pull, so this is low-blast-radius
        # like model_pull_cancel above, not gated like model_delete.
        "model_pull_delete",
        # Promotion is atomic + idempotent (single-holder invariant,
        # re-promoting a no-op) — reversible via model_edit.
        "model_set_default",
        # Slot lifecycle
        "slot_load",
        "slot_unload",
        "slot_edit",
        # PATCH convenience wrapper over slot_edit's PUT — same blast
        # radius, just a narrower [model] sub-table merge.
        "slot_set_defaults",
        # Settings
        "settings_reload",
        # Memory
        "memory_add",
        # memory_delete with len(ids) == 1 AND a default/own-namespace
        # dataset is autonomous; bulk ids or a list/foreign-namespace
        # dataset gate. The dispatch helper applies that rule at call
        # time (see is_gated, #8).
        "memory_delete",
        # Memory — Hindsight 0.8.4 parity buildout: mutating, reversible
        # writes. memory_mental_model_delete/memory_directive_delete are
        # destructive and live in GATED_TOOLS instead (below).
        "memory_curate",
        "memory_mental_model_create",
        "memory_mental_model_update",
        "memory_mental_model_refresh",
        "memory_directive_create",
        "memory_directive_update",
        "memory_operation_cancel",
        "memory_operation_retry",
        "memory_bank_consolidate",
        # Live hardware re-probe — reversible, scoped, persists to
        # /etc/hal0/hardware.json (same blast radius as model_scan).
        "hardware_reprobe",
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
        # Rename touches the display label an operator/agent uses to
        # target the slot everywhere else — gated so a rename can't
        # silently redirect a subsequent autonomous call (spec §4.3).
        "slot_rename",
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
        # Queue-item delete drops a pending cell outright — destructive.
        "bench_enqueue",
        "bench_control",
        "bench_queue_delete",
        # logs_tail / slot_logs are gated until the redactor in logs.py
        # covers Bearer + X-API-Key + provider keys (sk-/hf-/etc.) — see
        # docs/internal/phase-8-pending/mcp-backend.md §2.
        "logs_tail",
        "slot_logs",
        # Upstream provider CRUD — config-surface writes (delete is
        # destructive; the credential itself goes through the separately
        # gated provider_credential_write).
        "upstream_create",
        "upstream_update",
        "upstream_delete",
        # memory_delete with len(ids) > 1, or a list/foreign-namespace
        # dataset even for a single id, routes here at call time (#8).
        # Memory — mental-model/directive deletion is destructive, same
        # tier as memory_delete's bulk path. hal0.mcp.memory carried these
        # in its own _LOCAL_GATED_TOOLS fallback (its module docstring
        # #1302) pending this catalog picking up the classification —
        # this is that follow-up; admin.is_gated is now the single owner.
        "memory_mental_model_delete",
        "memory_directive_delete",
        # Services — start/stop/restart a systemd unit; destructive-adjacent.
        "service_action",
        # ComfyUI — mode flips, model downloads, render cancel, container
        # restart, prompt submission. comfyui_logs joins logs_tail/slot_logs
        # under the MED-1 journald-secret precedent (redacted the same way).
        "comfyui_switchover",
        "comfyui_pin",
        "comfyui_models_fetch",
        "comfyui_render_cancel",
        "comfyui_restart",
        "comfyui_workflow_launch",
        "comfyui_logs",
        # Container image pull for a slot — open-world network fetch, same
        # gating tier as model_pull.
        "slot_pull_image",
        # Full [models] config-section overwrite.
        "model_config_write",
        # Enqueues a benchmark suite run (back-compat POST /run wrapper
        # around bench_enqueue) — same tier as bench_enqueue.
        "bench_run",
        # Runner-image discovery pass — reaches GHCR.
        "runner_image_sync",
        # Dynamic NPU slot lifecycle — creates/loads or unloads+deletes a
        # slot outright.
        "npu_backend_load",
        "npu_backend_unload",
        # MCP self-management writes — install/uninstall/config-patch/action
        # on OTHER MCP servers this box hosts.
        "mcp_server_install",
        "mcp_server_uninstall",
        "mcp_server_config_write",
        "mcp_server_action",
        # Server audit-row tail — may echo prior tool-call args (including a
        # provider_credential_write's raw value before any redaction pass
        # exists for this surface). Gated pending its own MED-1-style
        # redactor, mirroring logs_tail/slot_logs/comfyui_logs.
        "mcp_server_logs",
    }
)


# ── REST passthrough mapping ─────────────────────────────────────────────────
#
# Each tool maps to an existing /api/* route. The MCP server forwards
# through httpx with the agent's Bearer; the REST layer owns authorization
# + validation. We do NOT duplicate that logic here. The map is no longer
# hand-maintained: it is derived from the live route table below, so the
# tool name may differ from its HTTP target (e.g. ``model_swap`` ->
# ``POST /api/slots/{name}/swap``, ``version_info`` -> ``GET /api/status``)
# — the divergence lives in the ``route_id`` an alias points at, not a
# separate table that could drift.

# ── Route-map autogen (spec §4.4 — deny-by-default, route-id keyed) ───────────
#
# ``_REST_MAP`` / ``_PATH_ARGS`` are no longer hand-authored. They are
# DERIVED at boot from the live FastAPI route table by
# :func:`build_admin_route_map`, then re-keyed onto stable tool names via
# the hand-authored :data:`TOOL_NAME_ALIASES` overlay. Three ratified
# invariants (spec-mcp-autogen-addendum.final.md):
#
#   Gap 1 — deny-by-default. Autogen emits route SCAFFOLDING only; a route
#     with no ``TOOL_NAME_ALIASES`` entry is HIDDEN from tools/list (never an
#     MCP tool), NOT fatal, and surfaced in the unclassified-routes report
#     (:data:`_UNCLASSIFIED_ROUTES`). The classification frozensets stay the
#     single source of exposure truth.
#   Gap 2 — transport exclusion. Streaming/SSE/WS routes (log tails,
#     pull-progress, events, board WS) are not request/response tools and are
#     skipped (:func:`_is_transport_excluded`); they are NOT counted as
#     "unclassified". PATCH joins the supported verb set.
#   Gap 3 — route-id re-key. The canonical identity is
#     ``route_id = "<METHOD>:<path-template>"``. ``TOOL_NAME_ALIASES`` maps
#     route_id -> stable tool name(s) so tools/list names never churn (agents
#     cache schemas). A collision is explicit: ``slot_edit`` + ``model_assign``
#     both alias ``PUT:/api/slots/{name}/config``.

# route_id -> stable tool name(s). Hand-authored: the ONLY place a route
# becomes an agent-visible tool and the ONLY place a tool name is pinned.
# Adding a FastAPI route does NOT add a tool until it is named here AND
# classified in a security frozenset (both guarded by _validate_catalog).
TOOL_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "GET:/api/slots": ("slot_list",),
    "GET:/api/slots/{name}": ("slot_status",),
    "GET:/api/slots/metrics": ("slot_metrics",),
    "GET:/api/slots/capacity": ("slot_capacity",),
    "GET:/api/ports": ("port_list",),
    "GET:/api/slots/by-name/{name}": ("slot_by_name",),
    "GET:/api/slots/by-id/{slot_id}": ("slot_by_id",),
    "GET:/api/slots/{name}/resolved": ("slot_resolved",),
    "GET:/api/slots/{name}/state": ("slot_state",),
    "POST:/api/slots/{name}/rename": ("slot_rename",),
    "PATCH:/api/slots/{name}/defaults": ("slot_set_defaults",),
    "POST:/api/slots/{name}/load": ("slot_load",),
    "POST:/api/slots/{name}/unload": ("slot_unload",),
    "PUT:/api/slots/{name}/config": (
        "slot_edit",
        "model_assign",
    ),  # collision — 2 tool names, 1 route_id
    "GET:/api/slots/{name}/logs": ("slot_logs",),
    "GET:/api/models": ("model_list",),
    "GET:/api/models/{model_id}": ("model_show",),
    "POST:/api/models/scan": ("model_scan",),
    "POST:/api/models/scan/preview": ("model_scan_preview",),
    "GET:/api/models/catalogue": ("model_catalogue",),
    "GET:/api/models/updates/check": ("model_update_check",),
    "GET:/api/models/pulls": ("model_pulls_list",),
    "GET:/api/models/{model_id}/pull/status": ("model_pull_status",),
    "POST:/api/models/inspect": ("model_inspect",),
    "GET:/api/settings/models/store": ("model_store",),
    "DELETE:/api/models/pulls/{model_id}": ("model_pull_delete",),
    "POST:/api/models/{model_id}/default": ("model_set_default",),
    "GET:/api/profiles": ("profile_list",),
    "GET:/api/profiles/{name}": ("profile_status",),
    "POST:/api/profiles/{name}/export": ("profile_export",),
    "GET:/api/stacks": ("stack_list",),
    "GET:/api/stacks/{slug}": ("stack_status",),
    "GET:/api/settings": ("settings_get",),
    "GET:/api/settings/schema": ("settings_schema",),
    "GET:/api/settings/apply-plan": ("settings_apply_plan",),
    "POST:/api/settings/reload": ("settings_reload",),
    "GET:/api/benchmarks/runs": ("bench_runs",),
    "GET:/api/benchmarks/runs/{run_id}": ("bench_run_status",),
    "GET:/api/benchmarks/queue": ("bench_queue",),
    "POST:/api/benchmarks/queue": ("bench_enqueue",),
    "POST:/api/benchmarks/control": ("bench_control",),
    "DELETE:/api/benchmarks/queue/{item_id}": ("bench_queue_delete",),
    "GET:/api/status": ("version_info",),
    "GET:/api/system-info": ("system_info",),
    "GET:/api/upstreams": ("upstream_list",),
    "GET:/api/stats/hardware": ("hardware_probe",),
    "GET:/api/logs": ("logs_tail",),
    "GET:/api/capabilities": ("capability_list",),
    "GET:/api/providers": ("provider_list",),
    "POST:/api/slots/{name}/swap": ("model_swap",),
    "PUT:/api/models/{model_id}": ("model_edit",),
    "POST:/api/models/{model_id}/pull/cancel": ("model_pull_cancel",),
    "POST:/api/models/{model_id}/pull": ("model_pull",),
    "DELETE:/api/models/{model_id}": ("model_delete",),
    "POST:/api/models": ("model_register",),
    "POST:/api/models/add-from-path": ("model_add",),
    "POST:/api/settings/models/store": ("model_store_set",),
    "POST:/api/settings/models/store/migrate": ("model_store_migrate",),
    "POST:/api/models/{model_id}/update": ("model_update",),
    "POST:/api/slots": ("slot_create",),
    "DELETE:/api/slots/{name}": ("slot_delete",),
    "POST:/api/slots/{name}/restart": ("slot_restart",),
    "POST:/api/capabilities/{slot}/{child}": ("capability_set",),
    "PUT:/api/settings": ("config_write",),
    "POST:/api/stacks": ("stack_create",),
    "PUT:/api/stacks/{slug}": ("stack_update",),
    "POST:/api/stacks/{slug}/apply": ("stack_apply",),
    "POST:/api/stacks/import": ("stack_import",),
    "POST:/api/stacks/{slug}/export": ("stack_export",),
    "POST:/api/stacks/snapshot": ("stack_snapshot",),
    "DELETE:/api/stacks/{slug}": ("stack_delete",),
    "POST:/api/profiles": ("profile_create",),
    "PUT:/api/profiles/{name}": ("profile_update",),
    "POST:/api/profiles/import": ("profile_import",),
    "DELETE:/api/profiles/{name}": ("profile_delete",),
    "POST:/api/providers/{name}/credentials": ("provider_credential_write",),
    "GET:/api/upstreams/{name}": ("upstream_get",),
    "POST:/api/upstreams": ("upstream_create",),
    "PATCH:/api/upstreams/{name}": ("upstream_update",),
    "DELETE:/api/upstreams/{name}": ("upstream_delete",),
    "POST:/api/upstreams/{name}/test": ("upstream_test",),
    # ── Services (platform-management expansion) ───────────────────────────
    "GET:/api/services": ("service_list",),
    "GET:/api/services/health": ("service_health",),
    "POST:/api/services/{service_id}/action": ("service_action",),
    # ── ComfyUI ──────────────────────────────────────────────────────────────
    "GET:/api/comfyui/status": ("comfyui_status",),
    "GET:/api/comfyui/workflows": ("comfyui_workflows",),
    "GET:/api/comfyui/logs": ("comfyui_logs",),
    "POST:/api/comfyui/switchover": ("comfyui_switchover",),
    "POST:/api/comfyui/pin": ("comfyui_pin",),
    "POST:/api/comfyui/models/fetch": ("comfyui_models_fetch",),
    "POST:/api/comfyui/render/cancel": ("comfyui_render_cancel",),
    "POST:/api/comfyui/restart": ("comfyui_restart",),
    "POST:/api/comfyui/workflows/{name}/launch": ("comfyui_workflow_launch",),
    # comfyui_preview (GET /api/comfyui/preview) deliberately NOT aliased — it
    # proxies raw image bytes (a PNG Response), not JSON; _call_rest assumes a
    # JSON body and would hand the agent mangled/undecodable "text".
    # ── Updater / doctor / health / features / metrics — READS ONLY. The
    # apply/commit/rollback/prepare/restart-slots/channel-PUT routes stay
    # unaliased (see EXCLUDED_TOOLS["updater_apply"]). ───────────────────────
    "GET:/api/updates/state": ("updater_state",),
    "GET:/api/updates/check": ("updater_check",),
    "GET:/api/updates/channel": ("updater_channel_get",),
    "GET:/api/updates/slot-drift": ("updater_slot_drift",),
    "GET:/api/updates/status/{job_id}": ("updater_job_status",),
    "GET:/api/doctor": ("doctor_report",),
    "GET:/api/health/system": ("health_system",),
    "GET:/api/features": ("feature_list",),
    "GET:/api/metrics": ("metrics_snapshot",),
    # ── Hardware / telemetry ───────────────────────────────────────────────
    "GET:/api/hardware": ("hardware_snapshot",),
    "POST:/api/hardware/probe": ("hardware_reprobe",),
    "GET:/api/stats/slots": ("slot_stats",),
    "GET:/api/stats": ("stats_overview",),
    "GET:/api/stats/requests": ("request_metrics",),
    "GET:/api/system-stats": ("system_stats",),
    "GET:/api/stats/power": ("power_stats",),
    "GET:/api/stats/throughput/history": ("throughput_history",),
    "GET:/api/npu/occupancy": ("npu_occupancy",),
    "GET:/api/npu/swap-status": ("npu_swap_status",),
    "GET:/api/models/health": ("model_health_check",),
    # ── Slots long-tail ──────────────────────────────────────────────────────
    "GET:/api/slots/{name}/config": ("slot_config",),
    "GET:/api/slots/{name}/voices": ("slot_voices",),
    "GET:/api/slots/flm/models": ("flm_model_list",),
    "POST:/api/slots/{name}/pull": ("slot_pull_image",),
    "GET:/api/slots/{name}/pull/status": ("slot_pull_status",),
    # ── Models long-tail ─────────────────────────────────────────────────────
    "POST:/api/models/validate": ("model_validate",),
    "GET:/api/hf/search": ("hf_search",),
    "GET:/api/config/models": ("model_config_read",),
    "PUT:/api/config/models": ("model_config_write",),
    # profile_generate — compute-only POST (builds a draft profile + warnings
    # from a model_id/hf_repo, persists nothing), classified AUTONOMOUS_READ
    # like model_inspect's read-shaped-POST treatment.
    "POST:/api/profiles/generate": ("profile_generate",),
    # ── Benchmarks long-tail ─────────────────────────────────────────────────
    "GET:/api/benchmarks/roster": ("bench_roster",),
    "GET:/api/benchmarks/plan": ("bench_plan",),
    "GET:/api/benchmarks/cells": ("bench_cells",),
    "GET:/api/benchmarks/history": ("bench_history",),
    "GET:/api/benchmarks/evals": ("bench_evals",),
    "POST:/api/benchmarks/run": ("bench_run",),
    # ── Journal / activity ───────────────────────────────────────────────────
    "GET:/api/journal": ("journal_snapshot",),
    "GET:/api/activity": ("activity_list",),
    "GET:/api/activity/export": ("activity_export",),
    # ── Approvals — READ ONLY. approve/deny stay operator-only, see
    # EXCLUDED_TOOLS["approval_approve"/"approval_deny"]. ────────────────────
    "GET:/api/agent/approvals": ("approval_list",),
    # ── Runner images / backends / NPU dynamic slots ─────────────────────────
    "GET:/api/runner-images": ("runner_image_list",),
    "GET:/api/runner-images/downloaded": ("runner_image_downloaded",),
    "GET:/api/runner-images/pulls/list": ("runner_image_pulls",),
    "POST:/api/runner-images/sync": ("runner_image_sync",),
    # runner_image_pull / pull/status / pull/cancel / detail (all keyed by
    # {image_id:path}) deliberately NOT aliased — catalogue ids embed the
    # GHCR repo path (e.g. "hal0ai/hal0-toolbox-cpu"), and _split_args
    # rejects any path-arg value containing "/" (deliberate guard against
    # path-segment splicing — see its docstring). Wiring these needs a
    # slash-safe path-arg encoder; out of scope for this pass.
    "GET:/api/backends": ("backend_list",),
    "GET:/api/backends/{backend_id}": ("backend_detail",),
    "POST:/api/backends/npu/load": ("npu_backend_load",),
    "POST:/api/backends/npu/unload": ("npu_backend_unload",),
    # ── MCP self-management (routes/mcp.py) ──────────────────────────────────
    "GET:/api/mcp/servers": ("mcp_server_list",),
    "GET:/api/mcp/clients": ("mcp_client_list",),
    "GET:/api/mcp/catalog": ("mcp_catalog",),
    "GET:/api/mcp/{server_id}/logs": ("mcp_server_logs",),
    "POST:/api/mcp/install": ("mcp_server_install",),
    "DELETE:/api/mcp/{server_id}": ("mcp_server_uninstall",),
    "PATCH:/api/mcp/{server_id}/config": ("mcp_server_config_write",),
    "POST:/api/mcp/{server_id}/{action}": ("mcp_server_action",),
}


# HTTP verbs the autogen forwards (Gap 2 added PATCH — §4.1 once shipped it
# unforwardable via upstream_update). HEAD/OPTIONS are Starlette auto-adds.
_SUPPORTED_VERBS: frozenset[str] = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

# Path prefixes never walked for tool generation: the MCP mounts, the
# OpenAPI/doc surfaces, and the dashboard-plugin static server. (The SPA
# catch-all is matched structurally by :func:`_is_spa_catchall`.)
_SKIP_PATH_PREFIXES: tuple[str, ...] = (
    "/mcp",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/dashboard-plugins",
)

# Gap-2 path-SUFFIX markers for streaming/SSE/WS tails. Applied ONLY to
# routes NOT pinned in TOOL_NAME_ALIASES, so a classified snapshot read that
# ends in ``/logs`` (logs_tail = GET /api/logs, slot_logs = GET
# /api/slots/{name}/logs) is never excluded — the alias is exposure truth.
_STREAM_PATH_SUFFIXES: tuple[str, ...] = ("/stream", "/events", "/logs", "/ws")

# Gap-2 (c): explicit non-tool endpoints that no marker catches. Empty today
# (the suffix + no-methods-route predicates cover every current stream), but
# wired so a future odd endpoint can be named without touching the walker.
EXCLUDED_ROUTES: frozenset[str] = frozenset()


def _normalize_path(path: str) -> str:
    """Strip Starlette path-converter suffixes (``{id:path}`` -> ``{id}``).

    route_id keys use the bare ``{placeholder}`` form so they match the
    hand-authored ``TOOL_NAME_ALIASES``; live routes occasionally carry a
    ``:path`` / ``:int`` converter (e.g. ``/v1/models/{model_id:path}``).
    """
    return re.sub(r"{(\w+):[^}]+}", r"{\1}", path)


def _is_spa_catchall(raw_path: str) -> bool:
    """The Vue SPA fallback (``/{full_path:path}``) — a root-level catch-all."""
    return re.fullmatch(r"/\{\w+:path\}", raw_path) is not None


def _placeholders(path: str) -> tuple[str, ...]:
    """Ordered ``{placeholder}`` names in a normalized path template."""
    return tuple(re.findall(r"{(\w+)}", path))


def _is_transport_excluded(route: object, path: str) -> bool:
    """Gap-2: a streaming/SSE/WS route, not a request/response tool?"""
    if path in EXCLUDED_ROUTES:
        return True
    if any(path.endswith(suffix) for suffix in _STREAM_PATH_SUFFIXES):
        return True
    # Defence-in-depth: a route whose declared response class is a stream.
    response_cls = getattr(route, "response_class", None)
    cls_name = getattr(response_cls, "__name__", "")
    return "Stream" in cls_name or "EventSource" in cls_name


# Recursion bound for :func:`_iter_leaf_routes`. Router nesting is a handful of
# levels in practice; this only exists so a cyclic or pathological structure can
# never hang the boot path (the walker runs inside ``create_app``).
_MAX_ROUTE_DEPTH = 12


def _iter_leaf_routes(routes: object, *, _depth: int = 0) -> Iterator[object]:
    """Yield every route object that carries a concrete ``path`` + ``methods``.

    A flat walk of ``app.routes`` is NOT enough. FastAPI 0.138 stopped
    flattening ``include_router``: ``app.routes`` holds one
    ``fastapi.routing._IncludedRouter`` per included router, and that wrapper
    exposes neither attribute. The previous one-level loop skipped all of them,
    the map came back empty, and ``install_admin_route_map`` failed the whole
    MCP mount — silently, because ``create_app`` logs the failure and continues.
    Found on halo (21 boots, 0 successful mounts) after the deployed venv
    resolved a newer FastAPI than ``uv.lock`` pins for CI.

    So: descend through anything that can enumerate its own children. We probe
    for ``effective_candidates()`` — the accessor 0.138 provides, returning
    ``_EffectiveRouteContext`` objects whose ``.path`` is ALREADY the fully
    resolved path (router prefix included), so there is no prefix arithmetic to
    get wrong here. Those contexts also carry ``response_class``, which keeps
    :func:`_is_transport_excluded` working on wrapped routes.

    Duck-typed rather than ``isinstance``-checked so this holds across FastAPI
    versions that rename or move the private class. Anything we cannot expand
    (and anything whose expansion raises) is skipped — an unknown wrapper
    degrades to "not a tool", never to a crash on the boot path. Pair this
    tolerance with the upper bound in ``pyproject.toml``: this keeps a surprise
    from being catastrophic, the pin keeps it from being a surprise.

    SECOND WALKER, DELIBERATE: ``tests/security/test_exposure.py::_iter_effective``
    solves the same wrapper problem independently, and had solved it BEFORE this
    one — the knowledge sat in a test while the production walker rotted. They
    are not merged because the contracts differ: this iterator yields only
    request/response routes (``.methods`` required, so WebSocket routes are
    correctly excluded from the tool catalog), while the exposure ratchet must
    see WebSocket routes too. Keep both duck-typed on the same accessor, and fix
    both when FastAPI moves again.
    """
    if _depth > _MAX_ROUTE_DEPTH:
        return
    for route in routes:  # type: ignore[attr-defined]
        if getattr(route, "path", None) and getattr(route, "methods", None):
            yield route
            continue
        expand = getattr(route, "effective_candidates", None)
        if not callable(expand):
            # Mounts / WebSocket routes / opaque objects — never tools.
            continue
        try:
            children = expand()
        except Exception:  # pragma: no cover — defensive against private-API drift
            continue
        yield from _iter_leaf_routes(children, _depth=_depth + 1)


def build_admin_route_map(
    app: object,
) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, ...]]]:
    """Walk ``app.routes`` -> ``(route_map, route_path_args)`` keyed by route_id.

    ``route_map`` is the full scaffolding: ``route_id -> (method, path)`` for
    every request/response route with a supported verb, minus the skip
    prefixes / SPA catch-all / transport excludes. Exposure is NOT decided
    here (Gap 1) — that is the classification overlay's job. A route pinned in
    :data:`TOOL_NAME_ALIASES` is ALWAYS kept, so an over-broad exclude can
    never silently drop a live, classified tool.

    Route discovery goes through :func:`_iter_leaf_routes`, which descends
    through FastAPI's ``include_router`` wrappers — see its docstring for why a
    flat walk silently cost us the entire MCP surface.
    """
    routes = getattr(app, "routes", app)
    route_map: dict[str, tuple[str, str]] = {}
    route_path_args: dict[str, tuple[str, ...]] = {}
    for route in _iter_leaf_routes(routes):
        raw_path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not raw_path or not methods:  # pragma: no cover — guaranteed by the iterator
            continue
        path = _normalize_path(raw_path)
        placeholders = _placeholders(path)
        for method in methods:
            verb = method.upper()
            if verb not in _SUPPORTED_VERBS:
                continue
            route_id = f"{verb}:{path}"
            if route_id not in TOOL_NAME_ALIASES:
                if _is_spa_catchall(raw_path):
                    continue
                if any(path.startswith(prefix) for prefix in _SKIP_PATH_PREFIXES):
                    continue
                if _is_transport_excluded(route, path):
                    continue
            route_map[route_id] = (verb, path)
            if placeholders:
                route_path_args[route_id] = placeholders
    return route_map, route_path_args


# ── Lazy route map (populated by install/set from the live app) ──────────────
#
# These stay REAL mutable module dicts for back-compat: callers + tests read
# ``admin._REST_MAP`` / ``admin._PATH_ARGS`` directly and monkeypatch them.
# They are EMPTY at import and populated by :func:`install_admin_route_map`
# (create_app, before build_server reads them) or :func:`set_admin_route_map`
# (tests). The route-half of :func:`_validate_catalog` only fires once a map
# is installed.

#: route_id -> (method, path): the full generated scaffolding.
_ROUTE_MAP: dict[str, tuple[str, str]] = {}
#: route_id -> path-arg names.
_ROUTE_PATH_ARGS: dict[str, tuple[str, ...]] = {}
#: tool_name -> (method, path): alias-resolved view the dispatch path forwards.
_REST_MAP: dict[str, tuple[str, str]] = {}
#: tool_name -> path-arg names (alias-resolved).
_PATH_ARGS: dict[str, tuple[str, ...]] = {}
#: generated route_ids with no TOOL_NAME_ALIASES entry (Gap-1 CI report):
#: hidden from tools/list, never fatal.
_UNCLASSIFIED_ROUTES: list[str] = []


def _reconstruct_tool_map(
    route_map: dict[str, tuple[str, str]],
    route_path_args: dict[str, tuple[str, ...]],
) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, ...]]]:
    """Re-key route_id scaffolding onto tool names via TOOL_NAME_ALIASES."""
    rest: dict[str, tuple[str, str]] = {}
    path_args: dict[str, tuple[str, ...]] = {}
    for route_id, tool_names in TOOL_NAME_ALIASES.items():
        target = route_map.get(route_id)
        if target is None:
            continue  # missing live route — _validate_catalog raises on this
        for tool_name in tool_names:
            rest[tool_name] = target
            if route_id in route_path_args:
                path_args[tool_name] = route_path_args[route_id]
    return rest, path_args


def _apply_route_map(
    route_map: dict[str, tuple[str, str]],
    route_path_args: dict[str, tuple[str, ...]],
) -> None:
    """Install a generated route map into the module-level lazy dicts + validate."""
    rest, path_args = _reconstruct_tool_map(route_map, route_path_args)
    for stash, fresh in (
        (_ROUTE_MAP, route_map),
        (_ROUTE_PATH_ARGS, route_path_args),
        (_REST_MAP, rest),
        (_PATH_ARGS, path_args),
    ):
        stash.clear()
        stash.update(fresh)
    _UNCLASSIFIED_ROUTES[:] = sorted(set(route_map) - set(TOOL_NAME_ALIASES))
    _validate_catalog()


def install_admin_route_map(app: object) -> None:
    """Build + install the admin route map from a live FastAPI ``app``.

    Called once from :func:`hal0.api.mcp_mount.mount_mcp_servers` (in
    create_app), BEFORE ``build_server`` reads ``_PATH_ARGS`` to advertise
    per-tool schemas in tools/list.
    """
    _apply_route_map(*build_admin_route_map(app))


def set_admin_route_map(*source: object) -> None:
    """Test helper: install from an app/routes OR prebuilt dicts.

    ``set_admin_route_map(app)`` / ``set_admin_route_map(routes)`` builds the
    map first; ``set_admin_route_map(route_map, route_path_args)`` installs
    prebuilt dicts (spec §4.3 signature) — lets tests exercise the catalog
    without booting the full lifespan.
    """
    if len(source) == 2:
        route_map, route_path_args = source  # type: ignore[assignment]
    elif len(source) == 1:
        route_map, route_path_args = build_admin_route_map(source[0])
    else:  # pragma: no cover — misuse
        raise TypeError("set_admin_route_map takes (app) | (routes) | (map, path_args)")
    _apply_route_map(route_map, route_path_args)


# ── Per-tool call-arg schemas (shared with the dashboard agent chat) ─────────
#
# _PATH_ARGS names only the URL path args; the body/query fields stay
# invisible to a caller reading tools/list, so the model invented arg
# names (observed: model_inspect called without hf_repo, model_pull
# called with model_id='org/repo'). These hints enrich the tools the
# agent misuses most: they extend the generated schema with named body
# fields + descriptions and add to 'required'. This dict is the SINGLE
# source of truth — both surfaces build their advertised schema from
# :func:`tool_param_schema` (the dashboard chat wraps it as an OpenAI
# function's ``parameters``; :func:`build_server` nests it under the MCP
# passthrough ``args`` object), so a hint authored once reaches every
# agent that can call the tool. Keys must be catalog tools (guarded by
# :func:`_validate_catalog`).
TOOL_PARAM_HINTS: dict[str, dict[str, Any]] = {
    "model_inspect": {
        "properties": {
            "hf_repo": {"type": "string", "description": "HuggingFace repo as 'org/name'"},
            "hf_url": {
                "type": "string",
                "description": "Alternative: full https://huggingface.co/... URL",
            },
        },
    },
    "model_pull": {
        "properties": {
            "model_id": {
                "type": "string",
                "description": (
                    "LOCAL model id — short name, NO slashes; invent one for a new model"
                ),
            },
            "hf_repo": {"type": "string", "description": "HF source repo 'org/name'"},
            "hf_filename": {
                "type": "string",
                "description": "Exact .gguf filename in the repo (from model_inspect)",
            },
            "mmproj_filename": {"type": "string", "description": "Optional vision sidecar"},
        },
    },
    "model_scan": {
        "properties": {
            "prune": {
                "type": "boolean",
                "description": (
                    "Also remove registry rows whose file is missing on disk; "
                    "slot/stack-referenced rows are protected and only reported "
                    "(missing_referenced), never deleted. Default false = add-only."
                ),
            },
        },
    },
    "model_swap": {
        "properties": {
            "name": {
                "type": "string",
                "description": "SLOT name (e.g. 'agent', 'ops') — NOT the model",
            },
            "model_id": {"type": "string", "description": "Registered model id to swap in"},
        },
        "required": ["model_id"],
    },
    "model_assign": {
        "properties": {
            "name": {"type": "string", "description": "SLOT name — NOT the model"},
            "model": {
                "type": "string",
                "description": "Registered model id to set as the slot's default",
            },
        },
        "required": ["model"],
    },
    "slot_create": {
        "properties": {
            "name": {"type": "string", "description": "New slot name"},
            "model": {"type": "string", "description": "Registered model id to assign"},
            "type": {
                "type": "string",
                "description": "llm|embedding|reranking|transcription|tts|image (default llm)",
            },
            "port": {"type": "integer", "description": "Omit to auto-assign the next free port"},
            "image": {
                "type": "string",
                "description": (
                    "Container image override — FPX/FP4 quants need "
                    "ghcr.io/hal0ai/hal0-rocmfpx:ade07ba with runtime='container'"
                ),
            },
            "runtime": {"type": "string", "description": "Set 'container' when image is set"},
            "autoload": {
                "type": "boolean",
                "description": "Boot-start this slot on hal0 startup (spec 2026-08-02).",
            },
            "priority": {
                "type": "integer",
                "description": (
                    "Eviction priority 0-100 (higher = evicted later under memory "
                    "pressure). Default 50."
                ),
            },
        },
        "required": ["name", "model"],
    },
    "slot_edit": {
        "properties": {
            "name": {"type": "string", "description": "Slot name to update (path arg)"},
            "port": {"type": "integer", "description": "New port"},
            "device": {"type": "string", "description": "e.g. 'gpu:0', 'cpu', 'npu'"},
            "model": {
                "type": "object",
                "description": (
                    "ModelConfig sub-table, e.g. {default: model_id, context_size: int}"
                ),
            },
            "autoload": {
                "type": "boolean",
                "description": "Boot-start this slot on hal0 startup (spec 2026-08-02).",
            },
            "priority": {
                "type": "integer",
                "description": (
                    "Eviction priority 0-100 (higher = evicted later under memory "
                    "pressure). Default 50."
                ),
            },
            "pinned": {
                "type": "boolean",
                "description": "Exempt this slot from automatic eviction entirely.",
            },
            "idle_timeout_s": {
                "type": "integer",
                "description": "Seconds idle before auto-unload (0 = never).",
            },
        },
        "required": ["name"],
    },
    "slot_set_defaults": {
        "properties": {
            "name": {"type": "string", "description": "Slot name to update (path arg)"},
            "default": {
                "type": "string",
                "description": "Default model id from the registry (ModelConfig.default)",
            },
            "context_size": {
                "type": "integer",
                "description": (
                    "Hardware ceiling on the model's resolved context window "
                    "(caps, never raises — ModelConfig.context_size)"
                ),
            },
            "n_gpu_layers": {
                "type": "integer",
                "description": "GPU offload layer count, -1 = all (ModelConfig.n_gpu_layers)",
            },
        },
        "required": ["name"],
    },
    "upstream_create": {
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "New upstream name — lowercase alnum plus -/_ ('hal0' reserved for the "
                    "model catalogue cache)"
                ),
            },
            "catalog_id": {
                "type": "string",
                "description": (
                    "Optional provider template: openai|anthropic|openrouter|"
                    "google_ai_studio|ollama — prefills url/auth"
                ),
            },
            "url": {
                "type": "string",
                "description": "OpenAI-compatible base URL (required without catalog_id)",
            },
            "auth_value_env": {
                "type": "string",
                "description": "Env-var NAME for the API key (never the key itself)",
            },
        },
        "required": ["name"],
    },
    "upstream_update": {
        "properties": {
            "name": {"type": "string", "description": "Upstream name to update"},
            "enabled": {
                "type": "boolean",
                "description": "Routing kill-switch — false removes it from dispatch",
            },
            "advertise_models": {
                "type": "boolean",
                "description": "Whether its models list in /v1/models",
            },
            "model_filters": {
                "type": "object",
                "description": (
                    "{models: [exact ids], include: [globs], exclude: [globs]} — "
                    "exclude wins; all-empty clears"
                ),
            },
        },
    },
    "slot_rename": {
        "properties": {
            "name": {"type": "string", "description": "CURRENT slot name (slot must be OFFLINE)"},
            "new_name": {"type": "string", "description": "New display name — must be unique"},
        },
        "required": ["new_name"],
    },
    "model_set_default": {
        "properties": {
            "model_id": {"type": "string", "description": "Model id to promote/clear"},
            "default": {
                "type": "boolean",
                "description": "true=promote (demotes current holder), false=clear; default true",
            },
        },
    },
    "service_action": {
        "properties": {
            "service_id": {"type": "string", "description": "Service id (path arg)"},
            "action": {
                "type": "string",
                "description": (
                    "start|stop|restart|enable|disable — must be in the service's "
                    "allow-list (see service_list's per-row 'actions'). ComfyUI's "
                    "'start' triggers the GPU-arbiter switchover, not a raw unit start."
                ),
            },
        },
        "required": ["action"],
    },
    "comfyui_switchover": {
        "properties": {
            "mode": {
                "type": "string",
                "description": "'generation' (hand iGPU to ComfyUI) or 'inference' (restore LLM slots)",
            },
            "force": {
                "type": "boolean",
                "description": "Switch to inference even with a busy ComfyUI queue",
            },
            "pin": {
                "type": "boolean",
                "description": "Pin generation mode so idle auto-restore-to-inference won't fire",
            },
        },
        "required": ["mode"],
    },
    "comfyui_pin": {
        "properties": {
            "pinned": {
                "type": "boolean",
                "description": "true blocks idle auto-restore off generation mode",
            },
        },
        "required": ["pinned"],
    },
    "comfyui_models_fetch": {
        "properties": {
            "auto": {
                "type": "boolean",
                "description": "Fetch the default variant for every ComfyUI capability (5 total)",
            },
            "selections": {
                "type": "array",
                "description": (
                    "Explicit variants instead of 'auto': "
                    "[{capability: 'txt2img', family: 'sdxl'}, ...]"
                ),
            },
        },
    },
    "npu_backend_load": {
        "properties": {
            "model_id": {
                "type": "string",
                "description": "FLM model tag, e.g. 'lfm2:1.2b'. Idempotent per tag.",
            },
        },
        "required": ["model_id"],
    },
    "npu_backend_unload": {
        "properties": {
            "slot_name": {
                "type": "string",
                "description": "Dynamic NPU slot name (must be 'npu-' prefixed)",
            },
        },
        "required": ["slot_name"],
    },
    "mcp_server_install": {
        "properties": {
            "url": {
                "type": "string",
                "description": "Install spec/URL to resolve (oci://, npm:, uvx:, git+https://, or a manifest URL)",
            },
            "manifest": {
                "type": "object",
                "description": "Alternative to 'url': a pre-resolved manifest (round-tripped from mcp_resolve)",
            },
        },
    },
    "mcp_server_config_write": {
        "properties": {
            "server_id": {"type": "string", "description": "Installed server id (path arg)"},
            "env": {
                "type": "object",
                "description": "FULL replacement env block (name -> value), not a delta",
            },
            "enabled": {"type": "boolean", "description": "Enable/disable without touching env"},
        },
    },
    "mcp_server_action": {
        "properties": {
            "server_id": {"type": "string", "description": "Installed server id (path arg)"},
            "action": {
                "type": "string",
                "description": (
                    "start|stop|restart — currently always 501s (mcp.supervisor_unavailable); "
                    "no process supervisor is wired yet."
                ),
            },
        },
    },
    "bench_run": {
        "properties": {
            "suite": {
                "type": "string",
                "description": "Suite to enqueue (default 'roster'). Back-compat wrapper over bench_enqueue.",
            },
        },
    },
    "profile_generate": {
        "properties": {
            "model_id": {
                "type": "string",
                "description": "Registered model id to base the draft on (exactly one of this/hf_repo)",
            },
            "hf_repo": {
                "type": "string",
                "description": "HF repo 'org/name' or full URL to base the draft on",
            },
            "name": {
                "type": "string",
                "description": "Suggested profile name (sanitized to kebab-case, <=32 chars)",
            },
            "use_llm": {
                "type": "boolean",
                "description": "Summarize the model card via the utility LLM for the intent headline",
            },
        },
    },
}


def tool_param_schema(tool: str) -> dict[str, Any]:
    """The flat JSON-Schema for one tool's call args — shared by both surfaces.

    Path args (:data:`_PATH_ARGS`) become required string properties;
    :data:`TOOL_PARAM_HINTS` merge over them with named body/query fields +
    descriptions and extend ``required``. ``additionalProperties`` stays
    open so an agent can still pass undeclared body fields a description
    calls out (e.g. ``slot_edit``'s arbitrary config keys) without the
    schema rejecting them. Returns the object schema itself; callers wrap
    it into their own envelope.

    Path args come from the installed map when present, else from the tool's
    ``TOOL_NAME_ALIASES`` route_id — so the advertised schema is correct even
    if a caller (e.g. brain chat) builds it before the map is installed.
    """
    path_args = _declared_path_args(tool)
    properties: dict[str, Any] = {arg: {"type": "string"} for arg in path_args}
    required = list(path_args)
    hint = TOOL_PARAM_HINTS.get(tool)
    if hint:
        properties.update(hint.get("properties", {}))
        required += [r for r in hint.get("required", ()) if r not in required]
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": True,
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
        value = str(remainder.pop(key))
        if "/" in value:
            # A slash would splice extra path segments into the REST URL and
            # mis-route (observed: model_pull with model_id='org/repo' → 405).
            raise KeyError(
                f"tool {tool!r} arg {key!r} must not contain '/' (got {value!r}). "
                "For model_pull, use a short local model_id and pass the HF repo "
                "as hf_repo='org/name' + hf_filename in the body."
            )
        path_args[key] = value
    return path_args, remainder


def _format_url(base_url: str, template: str, path_args: dict[str, str]) -> str:
    """Substitute ``{name}`` placeholders in ``template`` from ``path_args``."""
    return base_url.rstrip("/") + template.format(**path_args)


#: HTTP methods ``_call_rest`` knows how to forward, mapped to the httpx
#: kwarg that carries the payload (``params`` for query-string verbs,
#: ``json`` for body verbs). ``_validate_catalog`` checks every
#: ``_REST_MAP`` entry's method against this table at import time so an
#: unsupported method (e.g. a typo'd verb, or a route added with a method
#: nobody wired a branch for) fails loudly at import instead of surviving
#: operator approval and raising deep inside a gated tool call — see
#: upstream_update's PATCH route, which shipped mapped but unforwardable.
_REST_VERB_PAYLOAD_KWARG: dict[str, str] = {
    "GET": "params",
    "DELETE": "params",
    "POST": "json",
    "PUT": "json",
    "PATCH": "json",
}

#: Fallback code for a REST failure whose body isn't a hal0 envelope — a bare
#: FastAPI ``{"detail": …}``, a reverse proxy's HTML 502, an empty body. The
#: HTTP status is still carried separately as ``http_status``; this exists so
#: ``error.code`` is ALWAYS a branchable string, never absent.
_REST_FALLBACK_ERROR_CODE = "mcp.rest_error"


def _rest_error_payload(body: Any, *, text: str = "") -> dict[str, Any]:
    """Normalise a failed REST response body into the flat MCP error shape.

    Every hal0 REST failure is ``{"error": {"code", "message", "details"}}``
    (``api.middleware.error_codes._envelope``, mirrored by ``api.auth._envelope``).
    Returning that verbatim under this function's own ``error`` key produced
    ``error.error.code`` — one nesting level deeper than the in-process paths
    (``mcp.unknown_tool`` / ``mcp.missing_arg`` / the memory dispatcher), so a
    client or toolloop branching on ``result["error"]["code"]`` read ``None``
    for every REST-forwarded 4xx/5xx (#1468). This lifts the inner object so
    both paths answer to one accessor.

    Non-hal0 bodies keep all their diagnostic content under a synthetic
    ``mcp.rest_error`` code rather than being dropped — a proxy's HTML 502 is
    often the only evidence of what actually broke.
    """
    if isinstance(body, dict):
        inner = body.get("error")
        if isinstance(inner, dict) and isinstance(inner.get("code"), str):
            # The hal0 envelope — lift it verbatim (message/details included).
            return dict(inner)
        # A JSON body in some other shape (FastAPI's bare {"detail": …}, an
        # upstream's own error schema). Preserve it under `details` so nothing
        # is lost, and synthesise a code so the accessor still works.
        return {
            "code": _REST_FALLBACK_ERROR_CODE,
            "message": str(body.get("detail") or body.get("message") or "REST call failed"),
            "details": body,
        }
    if body is not None:
        # Valid JSON, but not an object (a bare string/list).
        return {
            "code": _REST_FALLBACK_ERROR_CODE,
            "message": "REST call failed",
            "details": {"body": body},
        }
    # Body wasn't JSON at all.
    return {
        "code": _REST_FALLBACK_ERROR_CODE,
        "message": text or "REST call failed",
        "details": {"text": text},
    }


#: Tools whose REST route blocks on the slot state machine, and therefore
#: cannot use ``_call_rest``'s generic 30s forward timeout: at 30s against a
#: 180s+ health poll the agent is told the op failed while it succeeds
#: server-side, the same defect #1832 fixed for the CLI. Budgets derive from
#: :mod:`hal0.slot_lifecycle_budget` so a server-side retune carries here too.
_SLOT_LIFECYCLE_TIMEOUT_S: dict[str, float] = {
    "slot_load": slot_lifecycle_timeout_s(loads=1, unloads=0),
    "slot_unload": slot_lifecycle_timeout_s(loads=0, unloads=1),
    "slot_restart": slot_lifecycle_timeout_s(loads=1, unloads=1),
    "model_swap": slot_lifecycle_timeout_s(loads=1, unloads=1),
    # POST /api/backends/npu/{load,unload} awaits SlotManager.load/unload on
    # the dynamic NPU slot in the request handler, same budget as the rest.
    "npu_backend_load": slot_lifecycle_timeout_s(loads=1, unloads=0),
    "npu_backend_unload": slot_lifecycle_timeout_s(loads=0, unloads=1),
}


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
    privilege elevation. Non-2xx responses come back as the same flat
    ``{"status": "error", "error": {"code", …}, "http_status": N}`` shape
    every other dispatch path uses — see :func:`_rest_error_payload` (#1468).
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

    if method not in _REST_VERB_PAYLOAD_KWARG:
        raise ValueError(f"unsupported HTTP method: {method}")
    payload_kwarg = _REST_VERB_PAYLOAD_KWARG[method]
    call_kwargs: dict[str, Any] = {"headers": headers}
    call_kwargs[payload_kwarg] = (payload or None) if payload_kwarg == "params" else (payload or {})

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_s) as client:
        verb = getattr(client, method.lower())
        response = await verb(url, **call_kwargs)

    if response.status_code >= 400:
        try:
            body: Any = response.json()
        except json.JSONDecodeError:
            body = None
        return {
            "status": "error",
            "http_status": response.status_code,
            "error": _rest_error_payload(body, text=response.text),
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


# ── Per-persona tool policy (overlay on the server classification) ──────────
#
# The classification frozensets above are the FLOOR every caller gets.
# A persona TOML (hal0.agents.personas) can overlay them per agent:
# hide tools entirely (tools_allowed), force approval onto autonomous
# tools (require_approval), or grant standing approval to gated ones
# (auto_approve / default_policy="auto-approve"). Loosening is an
# operator decision — the TOML is operator-owned, editing it IS the
# approval, granted standingly instead of per-call — EXCEPT for the
# no-loosen floor below: destructive/secret-bearing tools stay at the
# server verdict no matter what the persona says, so a persona edit can
# never fully disarm the approval queue.

#: Tools whose server gating can never be loosened by persona policy.
POLICY_NO_LOOSEN: frozenset[str] = frozenset(
    {
        "model_delete",
        "slot_delete",
        "stack_delete",
        "profile_delete",
        "memory_delete",  # keeps the bulk-delete arity gate intact
        "memory_mental_model_delete",
        "memory_directive_delete",
        "config_write",
        "provider_credential_write",
    }
)


def _matches(patterns: tuple[str, ...], tool: str) -> bool:
    return any(fnmatch.fnmatchcase(tool, p) for p in patterns)


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    """One persona's tool-policy overlay, resolved at dispatch time.

    Field semantics mirror the persona TOML 1:1 (``[persona.tools]`` +
    ``[persona.approval]``); patterns are fnmatch globs matched against
    ADMIN TOOL NAMES (``slot_create``, ``model_pull``, …), not REST
    paths. Precedence per call: tools_allowed (hide) → require_approval
    (tighten) → auto_approve / default_policy (loosen, no-loosen floor
    permitting) → server classification.
    """

    tools_allowed: tuple[str, ...] = ("*",)
    default_policy: str = "ask"  # ask | auto-approve | never
    auto_approve: tuple[str, ...] = ()
    require_approval: tuple[str, ...] = ()

    @classmethod
    def from_persona(cls, persona: Any) -> ToolPolicy:
        """Build from a :class:`hal0.agents.personas.Persona` (duck-typed
        so this module keeps zero imports from the agents package)."""
        return cls(
            tools_allowed=tuple(persona.tools_allowed or ("*",)),
            default_policy=str(persona.approval.default_policy or "ask"),
            auto_approve=tuple(persona.approval.auto_approve),
            require_approval=tuple(persona.approval.require_approval),
        )

    def allows(self, tool: str) -> bool:
        """Whether the tool is on this persona's surface at all."""
        return _matches(self.tools_allowed or ("*",), tool)

    def classify(self, tool: str, *, server_gated: bool) -> str:
        """Resolve one call → ``run`` | ``gated`` | ``denied`` | ``refused``.

        ``denied`` = not in tools_allowed (the tool shouldn't even be
        surfaced); ``refused`` = the call needs approval but the persona's
        default_policy is ``never`` (refuse outright instead of queueing).
        """
        if not self.allows(tool):
            return "denied"
        gated = server_gated
        if _matches(self.require_approval, tool):
            gated = True
        elif (
            gated
            and tool not in POLICY_NO_LOOSEN
            and (_matches(self.auto_approve, tool) or self.default_policy == "auto-approve")
        ):
            gated = False
        if gated and self.default_policy == "never":
            return "refused"
        return "gated" if gated else "run"


# ── Dispatch core ────────────────────────────────────────────────────────────


def is_gated(tool: str, args: dict[str, Any], *, client_id: str | None = None) -> bool:
    """Classify a tool invocation as gated (needs approval) or autonomous.

    ``memory_delete`` is the only tool whose gating depends on args —
    single-id deletes run autonomously, bulk deletes (>1 id) gate. Every
    other tool's classification is static.

    Diagnosis #8 hardening: a single-id delete that also directs the
    sweep at an explicit ``dataset`` used to run fully autonomously with
    zero namespace validation — a caller could hand it a guessed/foreign
    bank string and it would execute unattended. We now gate a single-id
    delete too when its ``dataset`` is (a) a list (multi-bank sweep,
    same blast radius as a bulk delete) or (b) a string naming a
    namespace outside the spec §3 closed set / not the caller's own
    (``is_known_namespace``). An absent/blank ``dataset`` (the common
    case — default shared+own-private sweep) stays autonomous.
    """
    if tool in GATED_TOOLS:
        return True
    if tool == "memory_delete":
        ids = args.get("ids") or []
        if len(ids) > 1:
            return True
        requested = args.get("dataset")
        if requested is None or (isinstance(requested, str) and not requested.strip()):
            return False
        if isinstance(requested, list):
            return True
        if isinstance(requested, str):
            return not is_known_namespace(requested, client_id=client_id)
        return True
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
    policy: ToolPolicy | None = None,
) -> dict[str, Any]:
    """Run one tool. Autonomous tools execute now; gated tools enqueue.

    Returns the tool's JSON result for autonomous calls or
    ``{"status": "pending_approval", "approval_id": "..."}`` for gated
    ones.

    ``memory_dispatcher`` is the in-process callable the memory server
    exposes for direct invocation (avoiding the HTTP round-trip for
    memory-engine calls). When ``None``, memory tools route through REST like
    everything else, which is the safer default.

    ``policy`` is the caller persona's :class:`ToolPolicy` overlay;
    ``None`` (every pre-existing caller) means the server classification
    stands unmodified.
    """
    if tool not in (AUTONOMOUS_READ_TOOLS | AUTONOMOUS_WRITE_TOOLS | GATED_TOOLS):
        return {"status": "error", "error": {"code": "mcp.unknown_tool", "tool": tool}}

    gated = is_gated(tool, args, client_id=client_id)
    if policy is not None:
        verdict = policy.classify(tool, server_gated=gated)
        if verdict == "denied":
            _audit(client_id=client_id, tool=tool, args=args, gated=gated, outcome="denied")
            return {
                "status": "error",
                "error": {
                    "code": "mcp.tool_not_allowed",
                    "tool": tool,
                    "detail": "tool is outside this persona's tools_allowed surface",
                },
            }
        if verdict == "refused":
            _audit(client_id=client_id, tool=tool, args=args, gated=True, outcome="refused")
            return {
                "status": "error",
                "error": {
                    "code": "mcp.gated_tool_refused",
                    "tool": tool,
                    "detail": (
                        "call requires approval but the persona's default_policy is 'never'"
                    ),
                },
            }
        gated = verdict == "gated"

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
        return {
            "status": "pending_approval",
            "approval_id": approval_id,
            "detail": (
                "queued for operator approval — the call runs only after the "
                "operator approves it (top-bar bell / approvals inbox). This "
                "response returns immediately; nothing waits on this transport, "
                "so the outcome is NOT returned inline here. Tell the operator "
                "the call is pending and, once they approve, the tool executes "
                "out-of-band — re-check the result via slot/model/profile reads "
                "rather than expecting it in this reply."
            ),
        }

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
    don't bounce through HTTP for a memory-engine call that runs in the same
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
    call: dict[str, Any] = {}
    if tool in _SLOT_LIFECYCLE_TIMEOUT_S:
        call["timeout_s"] = _SLOT_LIFECYCLE_TIMEOUT_S[tool]
    result = await _call_rest(
        base_url=base_url,
        bearer=bearer,
        method=method,
        url=url,
        payload=payload,
        **call,
    )
    # Redact every Bearer / HAL0_BEARER_TOKEN occurrence before a
    # journald-backed response leaves this process. The REST routes
    # themselves stay unredacted — REST consumers on the same host
    # already have credential access; the MCP transport is the spot
    # where a narrowly-scoped agent can otherwise siphon tokens out
    # (security review MED-1). slot_logs / comfyui_logs proxy the same
    # journald surface per-unit, so they get the same treatment.
    if tool in ("logs_tail", "slot_logs", "comfyui_logs"):
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
# the right approval-prompt language without having to inspect server
# internals.

_ANNOTATIONS: dict[str, ToolAnnotations] = {
    # ── Autonomous read — pure reads against the local REST surface. ─────
    # Slots
    "slot_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "port_list": ToolAnnotations(
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
    "slot_by_name": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "slot_by_id": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "slot_resolved": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "slot_state": ToolAnnotations(
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
    "system_info": ToolAnnotations(
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
    # Clears a terminal job's bookkeeping only — a resource disappears
    # (destructive per the module docstring's definition) but re-delete
    # of an already-cleared job just 404s, so end state is stable.
    "model_pull_delete": ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    ),
    # Promotion is atomic + idempotent (re-promoting the current holder
    # is a documented no-op).
    "model_set_default": ToolAnnotations(
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
    # PATCH convenience wrapper over slot_edit's PUT — same "set X to Y"
    # semantics, same idempotency.
    "slot_set_defaults": ToolAnnotations(
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
    # Upstream CRUD (upstream_list is annotated with the list reads above)
    "upstream_get": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "upstream_test": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "upstream_create": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "upstream_update": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "upstream_delete": ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    ),
    # Mutating, reversible, non-idempotent (each call has additional effect).
    "slot_create": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "slot_restart": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    # Relabels a slot — not destructive (nothing is removed), but a
    # second identical call targets a name that no longer exists (404),
    # so non-idempotent.
    "slot_rename": ToolAnnotations(
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
    # Drops a pending queue item outright.
    "bench_queue_delete": ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
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
    # ── Platform-management expansion — reads ──────────────────────────────
    "service_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "service_health": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "comfyui_status": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "comfyui_workflows": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "updater_state": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # Fetches the release manifest from the update channel — open-world.
    "updater_check": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
    "updater_channel_get": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "updater_slot_drift": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "updater_job_status": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "doctor_report": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "health_system": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "feature_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "metrics_snapshot": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "hardware_snapshot": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "slot_stats": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "stats_overview": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "request_metrics": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "system_stats": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "power_stats": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "throughput_history": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "npu_occupancy": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "npu_swap_status": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "model_health_check": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "slot_config": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "slot_voices": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "flm_model_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "slot_pull_status": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "model_validate": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # Reaches the public HuggingFace Hub search API.
    "hf_search": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
    "model_config_read": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "bench_roster": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "bench_plan": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "bench_cells": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "bench_history": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "bench_evals": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "journal_snapshot": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "activity_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "activity_export": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "approval_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "runner_image_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "runner_image_downloaded": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "runner_image_pulls": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "backend_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "backend_detail": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "mcp_server_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "mcp_client_list": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "mcp_catalog": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # Read-shaped POST — compute-only, never writes to the catalog. Reaches
    # HuggingFace when given hf_repo (like model_inspect), so open-world.
    "profile_generate": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
    # ── Platform-management expansion — autonomous write ───────────────────
    "hardware_reprobe": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # ── Platform-management expansion — gated ──────────────────────────────
    "service_action": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # comfyui_logs is read-only in effect but server-gated (MED-1), same
    # posture as logs_tail / slot_logs above.
    "comfyui_logs": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "comfyui_switchover": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "comfyui_pin": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    # Triggers background HF-adjacent model-weight downloads.
    "comfyui_models_fetch": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
    ),
    "comfyui_render_cancel": ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    ),
    "comfyui_restart": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "comfyui_workflow_launch": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    # Pulls a container image over the network — open-world like model_pull.
    "slot_pull_image": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
    "model_config_write": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "bench_run": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    "runner_image_sync": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
    ),
    "npu_backend_load": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "npu_backend_unload": ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    ),
    "mcp_server_install": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
    ),
    "mcp_server_uninstall": ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    ),
    "mcp_server_config_write": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
    "mcp_server_action": ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    ),
    # Read-only in effect but gated — audit rows may echo prior tool args.
    "mcp_server_logs": ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
}

# All 26 memory_* tools' hints are owned by hal0.mcp.memory (imported above as
# _MEMORY_TOOL_ANNOTATIONS) and merged in verbatim here — a single source of
# truth so the /mcp/admin and /mcp/memory mounts can never drift on
# read-only/destructive/idempotent/open-world semantics for the same tool.
_ANNOTATIONS.update(_MEMORY_TOOL_ANNOTATIONS)


# ── Tool catalog descriptions ────────────────────────────────────────────────
#
# Single source of truth for the tool surface's names + descriptions.
# build_server registers exactly this dict; the dashboard's agent chat
# (hal0.api.routes.board_chat) surfaces the same catalog as OpenAI tool
# schemas and routes calls through the same ``dispatch`` core — keep
# descriptions agent-facing (state required args + side effects).

TOOL_DESCRIPTIONS: dict[str, str] = {
    # ── Autonomous read ─────────────────────────────────────────────────
    "slot_list": "List every slot known to hal0 (local + remote).",
    "slot_status": "Get one slot's lifecycle state + metadata.",
    "slot_metrics": "Get per-slot performance metrics (tok/s, latency, queue depth).",
    "slot_capacity": "Get GPU/NPU memory capacity and per-slot allocation.",
    "slot_by_name": "Canonical name-keyed slot lookup (identical payload to slot_status).",
    "slot_by_id": "Stable-id slot lookup: opaque id → current name → snapshot.",
    "slot_resolved": (
        "The resolved llama-server argv with per-flag provenance (which segment — "
        "base/profile/extra_args — set each surviving flag)."
    ),
    "slot_state": "Just the state-machine fields for a slot (lighter than slot_status).",
    "port_list": (
        "The global port-claim map: every port owned by a slot config (incl. "
        "disabled), a runtime slot row, a reserved service, or a live listener — "
        "plus conflicts and the next free port. Check before choosing a port."
    ),
    "model_list": "Aggregate models from local registry + upstreams.",
    "model_show": "Show a single model's full metadata (registry + upstream).",
    "model_scan_preview": "Preview what a model scan would register (dry run).",
    "model_catalogue": "List the curated catalogue of blessed models.",
    "model_update_check": "Check which HF-backed models have updates available.",
    "model_pulls_list": "List all pull jobs (in-flight + terminal).",
    "model_pull_status": "Get one model's pull-job progress (state, %, speed, ETA).",
    "model_inspect": (
        "Inspect a HuggingFace repo — returns detected .gguf/.mmproj files, quants and "
        "capabilities WITHOUT registering anything. Use before model_register/model_pull. "
        "Args: hf_repo='org/name' (or hf_url='https://huggingface.co/org/name')."
    ),
    "model_store": (
        "Show the operator's configured model-store directory ([models].store) and "
        "candidate paths. Pulls always download into this store."
    ),
    "hardware_probe": "Live hardware probe — backends, memory, accelerators.",
    "system_info": (
        "Consolidated hardware + feature flags + per-runner backend state "
        "(installed/installable/unavailable) — one read for 'what can this box run'."
    ),
    "capability_list": "Capability overlay state — backends + selections.",
    "provider_list": "List configured providers.",
    "version_info": "hal0 version + runtime status.",
    "upstream_list": "List all configured upstream LLM providers.",
    "upstream_get": "Get one upstream provider's full detail.",
    "upstream_test": "Probe an upstream's reachability and auth status.",
    "upstream_create": "Register a new remote upstream LLM provider (gated).",
    "upstream_update": (
        "Update an upstream provider's settings (url/auth/visibility/filters/enabled) (gated)."
    ),
    "upstream_delete": "Remove an upstream provider; its api.env credential is kept (gated).",
    "stack_list": "List every stack, with the active stack + drift status.",
    "stack_status": "Get one stack's detail, active flag, and drift status.",
    "profile_list": "List every profile in the catalog (seed + custom).",
    "profile_status": "Get one profile's resolved detail (image, flags, backend, used-by).",
    "profile_export": (
        "Export a profile to a portable .hal0profile.json envelope (no secrets/host-paths)."
    ),
    "settings_get": "Get the current hal0 settings (pydantic schema validated).",
    "settings_schema": "Get the JSON schema for hal0 settings validation.",
    "settings_apply_plan": "Preview what settings changes would take effect.",
    "bench_runs": "List benchmark runs (history + in-flight).",
    "bench_run_status": "Get one benchmark run's detail + results.",
    "bench_queue": "Show the benchmark queue (pending cells).",
    "gpu_target_version": "Decode KFD's gfx_target_version to a gfxNNNN string (e.g. gfx1151).",
    "npu_status": "Report XDNA NPU presence + driver binding (LXC-correct, no modinfo).",
    "env_report": "Composite host snapshot — container, CPU, RAM, GPU, NPU, network, tooling.",
    "model_store_probe": (
        "Probe a model-store path: fstype, free/total bytes, writable, UMA-aware."
    ),
    # ── Autonomous write ────────────────────────────────────────────────
    "model_swap": (
        "Hot-swap a slot to a new model (container restart). "
        "Args: name=SLOT name (not the model!), model_id=registered model id."
    ),
    "model_assign": (
        "Assign a model as a slot's default (does not load the slot). "
        "Args: name=SLOT name (not the model!), model=registered model id."
    ),
    "model_edit": "Update a model's metadata (name, capabilities, tags, mmproj, defaults).",
    "model_scan": (
        "Walk the configured model roots + store and register newly-found files. "
        "Pass prune=true to also remove registry rows whose file is missing on disk "
        "(slot/stack-referenced rows are protected and only reported)."
    ),
    "model_pull_cancel": "Cancel an in-flight model pull job.",
    "model_pull_delete": (
        "Clear a TERMINAL pull job's record from memory + disk (409s if still queued/running)."
    ),
    "model_set_default": (
        "Promote or clear a model's per-type default marker. "
        "Args: model_id, optional default=true|false (default true — bare call promotes)."
    ),
    "slot_load": (
        "Load a slot (optionally assign a model first). "
        "Args: name=SLOT name, optional model_id=registered model to load."
    ),
    "slot_unload": "Unload a running slot gracefully.",
    "slot_edit": (
        "Update one or more slot config fields (model, port, ctx-size, provider, hardware)."
    ),
    "slot_set_defaults": (
        "Update slot defaults (ctx_size/context_size, n_gpu_layers, …) — merges into the "
        "slot's [model] sub-table. Provider-specific params belong under 'extra'."
    ),
    "settings_reload": "Ask the running hal0 daemon to reload configs (re-reads TOMLs).",
    "memory_add": "Add an item to long-term memory.",
    "memory_search": "Search long-term memory.",
    "memory_list": "Page through long-term memory items.",
    "memory_recall": (
        "Recall token-budgeted, consolidated memory (preferred over search). "
        "types defaults to world+experience+observation."
    ),
    "memory_delete": (
        "Delete one or more memory items (autonomous when len(ids)==1, gated otherwise)."
    ),
    # ── Memory extended toolset (Hindsight 0.8.4 parity, §mcp-hal0-integration) ──
    "memory_reflect": (
        "LLM-backed synthesis over memory — ask a question, get a written answer "
        "grounded in recalled facts (not a raw fact list). Single-bank."
    ),
    "memory_curate": (
        "Edit a memory unit, or soft-invalidate/revert it via state ('invalidated'|"
        "'valid' — reversible). The non-destructive correction path; id is the "
        "per-fact id from a search/recall/list result, not memory_add's document_id."
    ),
    "memory_history": "Revision history for one memory unit (curation audit trail).",
    "memory_mental_model_list": "List a bank's mental models (stored reflect responses).",
    "memory_mental_model_get": "Get one mental model by id.",
    "memory_mental_model_create": (
        "Create a mental model — a standing question kept refreshed from memory. "
        "Returns {mental_model_id, operation_id}; poll with memory_operation_get."
    ),
    "memory_mental_model_update": "Update a mental model's name/source_query/max_tokens/tags/trigger.",
    "memory_mental_model_delete": "Delete a mental model (gated — destructive).",
    "memory_mental_model_refresh": (
        "Trigger a mental model refresh (async). Returns {operation_id}; poll with "
        "memory_operation_get."
    ),
    "memory_directive_list": "List a bank's directives (standing instructions injected into prompts).",
    "memory_directive_get": "Get one directive by id.",
    "memory_directive_create": "Create a directive (standing instruction injected into prompts).",
    "memory_directive_update": "Update a directive's name/content/priority/is_active/tags.",
    "memory_directive_delete": "Delete a directive (gated — destructive).",
    "memory_operation_list": "List async operations (retain, consolidation, refresh, ...).",
    "memory_operation_get": (
        "Get one async operation's status — the poll target for memory_add's "
        "operation_id, memory_mental_model_refresh, and memory_bank_consolidate."
    ),
    "memory_operation_cancel": "Cancel a pending/processing operation (aborts in-flight work only).",
    "memory_operation_retry": "Re-queue a failed operation.",
    "memory_tags_list": "List tags in use in a bank, with usage counts.",
    "memory_bank_stats": "Node/link/observation/operation counts for a bank (read-only).",
    "memory_bank_consolidate": (
        "Trigger the engine's world/experience -> observation consolidation pass "
        "early. Returns {operation_id}; not destructive (source facts are kept)."
    ),
    # ── Gated write ─────────────────────────────────────────────────────
    "model_pull": (
        "Download a model from HuggingFace into the operator's configured model store. "
        "model_id is the LOCAL id — a short name with NO slashes (for a new model, "
        "invent one, e.g. 'Qwythos-9B-bf16-mtp'). The HF source goes in the body: "
        "hf_repo='org/name' + hf_filename='file.gguf' (find the exact filename via "
        "model_inspect first; optional mmproj_filename for vision). "
        "Async: returns a job id immediately. Check model_pull_status once and report "
        "progress — do NOT poll in a loop; downloads run for minutes in the background."
    ),
    "model_delete": "Delete a model from the local registry (gated).",
    "model_register": "Register a model that's already on disk into the local registry (gated).",
    "model_add": "Register an already-downloaded model file — capabilities auto-detected (gated).",
    "model_store_set": (
        "Set or change the model-store directory — future pulls download there; existing "
        "files stay until model_store_migrate (gated)."
    ),
    "model_store_migrate": "Migrate model files from the current store to a new path (gated).",
    "model_update": "Re-pull a model's HF file over its installed bytes (in place) (gated).",
    "slot_create": (
        "Create a new slot config (gated). Args pass through to POST /api/slots "
        "(full SlotConfig schema): name + model required; omit port to auto-assign "
        "the next free one; optional type/device, model.context_size, and a string "
        "image override (FPX/FP4-quant GGUFs need the hal0-rocmfpx toolbox image, "
        "with runtime='container'). Writes config only — follow with slot_load to "
        "start it."
    ),
    "slot_delete": "Delete a slot (gated).",
    "slot_rename": (
        "Rename a slot's display label (gated). Args: name=CURRENT name, new_name=new label. "
        "Slot must be OFFLINE; id stays stable so port/state semantics are untouched."
    ),
    "slot_restart": (
        "Restart a slot's systemd unit (gated). Prefer slot_load for a slot in "
        "error state or after a config change — load regenerates the unit; "
        "restart can time out on errored slots."
    ),
    "capability_set": "Assign a capability child to a slot (gated).",
    "config_write": "Update hal0.toml top-level settings (gated).",
    "provider_credential_write": "Write provider credentials (gated; secrets never echoed back).",
    "stack_create": "Create a stack from a slug + full stack body (gated).",
    "stack_update": "Replace a custom stack's body wholesale (gated).",
    "stack_apply": "Apply a stack — commit its slot config and converge runtime to match (gated).",
    "stack_import": "Import a stack from a .hal0stack.json envelope (gated).",
    "stack_export": "Serialize a stack into its portable .hal0stack.json envelope (gated).",
    "stack_snapshot": "Build a stack from the current live slot + capability config (gated).",
    "stack_delete": "Delete a custom stack from the catalog (gated).",
    "profile_create": (
        "Create a custom runtime profile (image, flags, device class) — e.g. authored "
        "from a model card's requirements (gated)."
    ),
    "profile_update": "Update an existing custom profile (shallow merge) (gated).",
    "profile_import": "Import a profile from a .hal0profile.json envelope (gated).",
    "profile_delete": "Delete a custom profile from the catalog (gated).",
    "bench_enqueue": "Enqueue benchmark cells (model x slot x settings) for the runner (gated).",
    "bench_control": "Start/stop/pause the benchmark runner (gated).",
    "bench_queue_delete": "Drop a pending item from the benchmark queue (gated).",
    "logs_tail": "Tail journald for one systemd unit (gated).",
    "slot_logs": "Tail one slot's journal output (gated).",
    # ── Platform-management expansion — reads ──────────────────────────────
    "service_list": "List every companion service (ComfyUI/Hermes/Hindsight/OpenWebUI) with live probe status.",
    "service_health": "Stable 3-service (comfyui/hermes/openwebui) health summary for the Overview card.",
    "comfyui_status": "Aggregate ComfyUI engine status — container/systemd/HTTP reachability, queue, arbiter mode.",
    "comfyui_workflows": "List launchable ComfyUI workflow templates (bind-mounted dirs).",
    "updater_state": "Aggregate hal0 + FLM version/channel update state.",
    "updater_check": "Fetch the release manifest and diff it against the running version.",
    "updater_channel_get": "Get the configured update channel.",
    "updater_slot_drift": "Report slots whose running argv has drifted from a fresh post-update render.",
    "updater_job_status": "Poll an update/prepare/commit job's state. Args: job_id.",
    "doctor_report": "Composed doctor verdict — health, URLs, system, memory, services, capabilities in one read.",
    "health_system": "Deep health check — disk, slot manager, event bus, MCP mount.",
    "feature_list": "Feature-flag map (comfyui_switchover, memory, memory_engine, npu, mcp_supervisor).",
    "metrics_snapshot": "JSON metrics snapshot (slots/hardware/dispatcher).",
    "hardware_snapshot": "Cached hardware.json probe, flattened for display.",
    "slot_stats": "Per-slot runtime metrics aggregated across configured upstreams (fleet-wide).",
    "stats_overview": "Request/throughput rollup over the request-metric table + bench baselines. "
    "Query args: window, model_id, runner (all optional).",
    "request_metrics": "Dispatcher-side request/latency rollup. Args: window_s (optional).",
    "system_stats": "Latest fleet + per-slot metrics sample snapshot.",
    "power_stats": "Lightweight hwmon power/thermal snapshot (GPU power/temp/clock, CPU temp).",
    "throughput_history": "Bucketed token-throughput history. Args: buckets, window_s (both optional).",
    "npu_occupancy": "AIE column-allocation + slot composition for the NPU pane.",
    "npu_swap_status": "Whether the FLM trio chat model is mid-swap (from/to model).",
    "model_health_check": "Per-slot checkpoint/health snapshot + 24h TTFT/decode-tps rollup.",
    "slot_config": "Return a slot's TOML config as a dict. Args: name.",
    "slot_voices": "List a TTS slot's available voices (proxies the slot's own voices endpoint).",
    "flm_model_list": "Full FLM catalog for NPU model dropdowns (model, installed, capabilities, family).",
    "slot_pull_status": "Poll a slot's container-image pull progress. Args: name.",
    "model_validate": (
        "Dry-run screen a candidate model write (create or edit) without persisting anything. "
        "Same body shape as model_register/model_edit."
    ),
    "hf_search": "Free-text search the HuggingFace Hub model catalog. Args: q, type, limit (all optional).",
    "model_config_read": "Get the current [models] config section (roots, auto-scan, extensions, pull_root, ...).",
    "bench_roster": "Per-model benchmark roster board (zoom 1).",
    "bench_plan": "Staleness / what-would-run report for the benchmark suite. Args: suite (optional).",
    "bench_cells": "Filtered current-value benchmark cell matrix (zoom 2).",
    "bench_history": "Time-series benchmark points for trend charts. Args: cell_key, model (both optional).",
    "bench_evals": "Agentic-eval leaderboard.",
    "journal_snapshot": "Recent journal entries (operator-facing event log). Args: since (optional cursor).",
    "activity_list": "Recent audit/activity records. Args: since, category, action, severity, outcome, "
    "actor, kind, search, limit (all optional).",
    "activity_export": (
        "Export activity records as a file download. Args: fmt ('json' recommended for MCP "
        "consumption — 'csv' also supported but returns raw text), plus the same filters as "
        "activity_list."
    ),
    "approval_list": "List pending agent-tool approvals awaiting operator review.",
    "runner_image_list": "List every catalogued runner (container) image.",
    "runner_image_downloaded": "List only the runner images actually present on this host.",
    "runner_image_pulls": "List all runner-image pull jobs (active + persisted terminal).",
    "backend_list": "List every available inference backend with live status.",
    "backend_detail": "Get one backend's detail. Args: backend_id.",
    "mcp_server_list": "List MCP servers hal0 hosts (bundled + user-installed) with tool/resource/prompt counts.",
    "mcp_client_list": "List MCP clients seen in the recent audit window.",
    "mcp_catalog": "The static installable-MCP-servers catalog.",
    "profile_generate": (
        "Generate a draft runtime profile from a registered model or a HuggingFace repo — "
        "compute-only, persists nothing. Args: exactly one of model_id / hf_repo (required), "
        "optional name, optional use_llm=true to summarize the model card via the platform "
        "utility LLM (degrades to heuristics on failure). Returns {profile, warnings, sources} "
        "— 'profile' is the exact envelope shape profile_export produces, so it can be handed "
        "straight to profile_create (unwrap .profile) or profile_import (as 'envelope')."
    ),
    # ── Platform-management expansion — autonomous write ────────────────────
    "hardware_reprobe": "Re-run the live hardware probe and persist the result to /etc/hal0/hardware.json.",
    # ── Platform-management expansion — gated ───────────────────────────────
    "service_action": (
        "Run a lifecycle verb on a companion service's systemd unit (gated). "
        "Args: service_id (path), action=start|stop|restart|enable|disable "
        "(must be in that service's allow-list)."
    ),
    "comfyui_logs": "Tail the ComfyUI slot's journal output (gated; journald secret-redaction posture).",
    "comfyui_switchover": (
        "Flip the iGPU between LLM inference and ComfyUI generation via the GPU arbiter (gated). "
        "Args: mode='generation'|'inference', optional force, pin."
    ),
    "comfyui_pin": "Toggle the arbiter's manual pin (blocks idle auto-restore off generation mode) (gated).",
    "comfyui_models_fetch": (
        "Trigger background ComfyUI model-weight downloads (gated). "
        "Args: auto=true, OR selections=[{capability, family}, ...]."
    ),
    "comfyui_render_cancel": "Clear the ComfyUI queue and interrupt the current render (gated).",
    "comfyui_restart": "Restart the slot-managed ComfyUI runtime (gated).",
    "comfyui_workflow_launch": "Quick-launch a named ComfyUI workflow template (gated). Args: name (path).",
    "slot_pull_image": "Start a background container-image pull for a slot (gated, idempotent). Args: name.",
    "model_config_write": (
        "Update the [models] config section — merges onto the current config, revalidates as a "
        "whole, and persists the entire section (gated)."
    ),
    "bench_run": "Enqueue a benchmark suite run (back-compat wrapper over bench_enqueue) (gated). Args: suite.",
    "runner_image_sync": "Run one runner-image discovery pass against GHCR (gated).",
    "npu_backend_load": (
        "Create + load a dynamic FLM/NPU slot for one model tag (gated, idempotent per tag). "
        "Args: model_id."
    ),
    "npu_backend_unload": (
        "Unload and delete a dynamically-created NPU slot (gated). Args: slot_name (must be "
        "'npu-' prefixed)."
    ),
    "mcp_server_install": "Install a user MCP server from a URL/spec or a resolved manifest (gated).",
    "mcp_server_uninstall": "Uninstall a user-installed MCP server (gated; bundled servers reject 409).",
    "mcp_server_config_write": (
        "Patch an installed MCP server's env/enabled fields (gated; bundled servers reject 409)."
    ),
    "mcp_server_action": (
        "Start/stop/restart an installed MCP server (gated). Currently always 501s — no process "
        "supervisor is wired yet."
    ),
    "mcp_server_logs": "Tail an MCP server's recent audit rows (gated — rows may echo prior tool-call args).",
}


# ── Deliberate exclusions (tier b — spec §4.3) ───────────────────────────────
#
# Capabilities that exist on the REST/CLI surface but are intentionally NOT
# exposed as admin MCP tools — policy decisions, not coverage gaps. Recorded
# explicitly (label -> reason) so future coverage tooling (the §4.4 route-map
# autogen lane) can tell "missing, add it" from "excluded, leave it" instead
# of re-litigating the same call every buildout pass. Keys are documentation
# labels, NOT tool names — they must never collide with a real catalog tool
# (checked by :func:`_validate_catalog`).
EXCLUDED_TOOLS: dict[str, str] = {
    "board_crud": (
        "Board CRUD (create/update/move cards) is better reached via the KB-2/3 "
        "brain tool tiers than a raw MCP REST passthrough — a policy call, not "
        "a coverage gap."
    ),
    "brain_chat": (
        "Steward/brain chat is its own SSE surface (hal0.api.routes.board_chat), "
        "not a stateless request/response REST passthrough — doesn't fit the "
        "admin tool shape."
    ),
    "updater_apply": (
        "Self-update/restart (apply/commit/rollback/prepare/restart-slots, plus the "
        "channel PUT) is a destructive host-level action; exposing it as an MCP tool "
        "needs its own POLICY_NO_LOOSEN + operator-confirmation design, deferred to a "
        "dedicated lane rather than hand-added here. The READ half of the updater "
        "surface (state/check/channel-GET/slot-drift/status) IS classified now — see "
        "updater_state / updater_check / updater_channel_get / updater_slot_drift / "
        "updater_job_status in TOOL_DESCRIPTIONS."
    ),
    "auth_rotate": (
        "Key rotation is a lockout-recovery / operator-console action — never "
        "agent-callable, gated or not."
    ),
    "auth_me": (
        "Identity self-lookup backs the MCP transport's own bearer_resolver; "
        "surfacing it as a callable tool would let an agent probe its own "
        "credential label for no operational benefit."
    ),
    "provider_credential_read": (
        "Provider credentials are write-only from the agent's side "
        "(provider_credential_write exists); no tool ever reads a secret value "
        "back to a caller."
    ),
    "agent_sessions": (
        "Hermes/agent session administration (list/kill sessions) is an "
        "operator-console concern, not a tool an agent should hold over its "
        "own or sibling agents' sessions."
    ),
    # ── Policy-by-record (§4.3 buildout): these surfaces were unclassified
    # by silence — recorded explicitly now so a future pass reads "excluded,
    # leave it" instead of re-litigating each one.
    "approval_approve": (
        "Approving a gated tool call (POST /api/agent/approvals/{id}/approve) is the "
        "owner-approval mechanism ITSELF — an agent approving its own or another "
        "agent's queued call would defeat the gate. Operator-only, no loosen path."
    ),
    "approval_deny": (
        "Denying a queued approval is the same operator-only surface as approve — "
        "see approval_approve. approval_list (read) stays available so an agent can "
        "report what's pending."
    ),
    "secrets_crud": (
        "GET/POST/PUT/DELETE /api/secrets[/...] manage credential values. GET only "
        "returns names/metadata (never values), but the whole surface is grouped "
        "with the write half here — provider_credential_write is the one narrow, "
        "already-gated write seam an agent gets onto secret-shaped config."
    ),
    "installer_wizard": (
        "/api/install/* (state/probe/complete/curated-models/apply/apply-selections/"
        "slots-model/services/services-repair) is the first-run wizard — a one-shot, "
        "no-auth-by-convention bootstrap surface, not an ongoing agent-management tool."
    ),
    "proxmox_config": (
        "/api/settings/proxmox[/test] reads/writes Proxmox host integration config, "
        "including credential-adjacent fields, and probes a live external host — "
        "operator-console territory, same tier as provider credentials."
    ),
    "auth_session": (
        "/api/auth/login, /logout, /require, /status, /exposure — session minting and "
        "the auth-required/exposure posture toggles. Never agent-callable; see "
        "auth_rotate / auth_me above for the adjacent identity/rotation exclusions."
    ),
    "agent_lifecycle": (
        "/api/agents/* (list/install/uninstall bundled agent products, persona-enums, "
        "skills, per-agent activity) is bundled-agent product management — adjacent "
        "to but distinct from agent_sessions above (which covers runtime session "
        "admin). Kept excluded as one policy area rather than split by verb; the "
        "hermes-specific sub-routers at the same prefix (personas/restart/memory-"
        "stats/chat-proxy) are a sibling lane's surface, not aliased here either."
    ),
}


# ── Catalog consistency guard ────────────────────────────────────────────────
#
# The tool surface is spread over the classification frozensets, the
# hand-authored TOOL_NAME_ALIASES / TOOL_PARAM_HINTS / _ANNOTATIONS /
# TOOL_DESCRIPTIONS overlays, and the autogen route map. A tool landing in
# some tables but not others fails at call time with an opaque envelope.
#
# Split by dependency: :func:`_validate_overlay` checks only the
# app-INDEPENDENT overlays and runs at import (fail-fast on a hand-authored
# mistake). :func:`_validate_catalog` adds the route-map checks and runs
# once a live map is installed (:func:`_apply_route_map`) — so drift between
# the classification overlay and the live FastAPI routes surfaces in CI.


def _routed_catalog() -> set[str]:
    """Catalog tools that forward over REST (exclude memory_* + host probes)."""
    catalog = AUTONOMOUS_READ_TOOLS | AUTONOMOUS_WRITE_TOOLS | GATED_TOOLS
    return {t for t in catalog if not t.startswith("memory_")} - PROBE_TOOLS


def _validate_overlay() -> None:
    """App-independent overlay coherence — safe to run before a map installs."""
    catalog = AUTONOMOUS_READ_TOOLS | AUTONOMOUS_WRITE_TOOLS | GATED_TOOLS
    problems: list[str] = []

    overlaps = (
        (AUTONOMOUS_READ_TOOLS & AUTONOMOUS_WRITE_TOOLS)
        | (AUTONOMOUS_READ_TOOLS & GATED_TOOLS)
        | (AUTONOMOUS_WRITE_TOOLS & GATED_TOOLS)
    )
    if overlaps:
        problems.append(f"tools in more than one classification: {sorted(overlaps)}")

    if excluded_overlap := set(EXCLUDED_TOOLS) & catalog:
        problems.append(
            f"labels in both EXCLUDED_TOOLS and the live catalog: {sorted(excluded_overlap)}"
        )

    if unannotated := catalog - set(_ANNOTATIONS):
        problems.append(f"classified but missing ToolAnnotations: {sorted(unannotated)}")
    if set(TOOL_DESCRIPTIONS) != catalog:
        problems.append(
            "TOOL_DESCRIPTIONS out of sync with classification — "
            f"missing: {sorted(catalog - set(TOOL_DESCRIPTIONS))}, "
            f"extra: {sorted(set(TOOL_DESCRIPTIONS) - catalog)}"
        )

    # TOOL_NAME_ALIASES is the tool-name overlay: it must name exactly the
    # REST-routed catalog (Gap 3). A name it lists that isn't routed, or a
    # routed tool it forgets, would detach a tool from its route.
    alias_tools = {t for names in TOOL_NAME_ALIASES.values() for t in names}
    routed = _routed_catalog()
    if stray_alias := alias_tools - routed:
        problems.append(f"TOOL_NAME_ALIASES names non-routed tools: {sorted(stray_alias)}")
    if uncovered := routed - alias_tools:
        problems.append(f"routed tools missing a TOOL_NAME_ALIASES entry: {sorted(uncovered)}")
    if bad_ids := [rid for rid in TOOL_NAME_ALIASES if not re.fullmatch(r"[A-Z]+:/\S*", rid)]:
        problems.append(f"malformed TOOL_NAME_ALIASES route_id keys: {sorted(bad_ids)}")

    # Param hints must reference real catalog tools, and every 'required'
    # field they add must actually be declared (as a hint property or a
    # path arg) — else the shared schema advertises a required field with
    # no matching property.
    if stray := set(TOOL_PARAM_HINTS) - catalog:
        problems.append(f"TOOL_PARAM_HINTS references unknown tools: {sorted(stray)}")
    for tool, hint in TOOL_PARAM_HINTS.items():
        declared = set(hint.get("properties", {})) | set(_declared_path_args(tool))
        if orphan := set(hint.get("required", ())) - declared:
            problems.append(
                f"{tool}: TOOL_PARAM_HINTS required {sorted(orphan)} not in properties/path args"
            )

    if problems:
        raise RuntimeError("hal0.mcp.admin overlay drift: " + " | ".join(problems))


def _declared_path_args(tool: str) -> tuple[str, ...]:
    """Path args for ``tool`` — from the installed map, else its alias route."""
    if tool in _PATH_ARGS:
        return _PATH_ARGS[tool]
    for route_id, names in TOOL_NAME_ALIASES.items():
        if tool in names:
            return _placeholders(route_id.split(":", 1)[1])
    return ()


def _validate_catalog() -> None:
    """Full guard: overlay coherence + the installed route map (Gap 1/3)."""
    _validate_overlay()
    problems: list[str] = []

    # Gap 3 — every classified route must resolve to a LIVE route_id. Replaces
    # the old "classified but missing from _REST_MAP" import check + the
    # separate route-sync test's job.
    if missing_live := [rid for rid in TOOL_NAME_ALIASES if rid not in _ROUTE_MAP]:
        problems.append(f"classified route_id with no live route: {sorted(missing_live)}")

    routed = _routed_catalog()
    if unmapped := routed - set(_REST_MAP):
        problems.append(f"classified but missing from _REST_MAP: {sorted(unmapped)}")
    if unclassified := set(_REST_MAP) - (
        AUTONOMOUS_READ_TOOLS | AUTONOMOUS_WRITE_TOOLS | GATED_TOOLS
    ):
        problems.append(f"in _REST_MAP but never classified: {sorted(unclassified)}")

    for tool, (method, template) in _REST_MAP.items():
        placeholders = set(re.findall(r"{(\w+)}", template))
        declared = set(_PATH_ARGS.get(tool, ()))
        if placeholders != declared:
            problems.append(
                f"{tool}: _PATH_ARGS {sorted(declared)} != template placeholders "
                f"{sorted(placeholders)}"
            )
        if method not in _REST_VERB_PAYLOAD_KWARG:
            problems.append(
                f"{tool}: _REST_MAP method {method!r} unsupported by _call_rest "
                f"(supported: {sorted(_REST_VERB_PAYLOAD_KWARG)})"
            )

    if problems:
        raise RuntimeError("hal0.mcp.admin catalog drift: " + " | ".join(problems))


# Import-time: only the hand-authored overlays exist yet (the route map is
# installed later by create_app / a test helper), so validate those now and
# defer the route checks to :func:`_apply_route_map`.
_validate_overlay()


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
    # FastMCP's constructor has no ``version`` kwarg, so the underlying
    # ``mcp.server.lowlevel.Server`` defaults ``initialize``'s
    # ``serverInfo.version`` to the ``mcp`` SDK package version (e.g.
    # "1.28.1") — a client sees the FastMCP library version, not hal0's own
    # (#1796). Stamp it post-construction on the wrapped server object.
    from hal0 import __version__ as _hal0_version

    server._mcp_server.version = _hal0_version

    def _resolve() -> tuple[str | None, str]:
        if bearer_resolver is None:
            return None, "anonymous"
        return bearer_resolver()

    # Single tool factory — every tool in the catalog dispatches into
    # the same ``dispatch`` core. Registration iterates TOOL_DESCRIPTIONS
    # verbatim, so FastMCP's tools/list is exactly the validated catalog
    # (dict keys are unique; _validate_catalog pins keys == catalog).
    def _register(tool_name: str, description: str) -> None:
        async def _tool(args: dict[str, Any] | None = None) -> dict[str, Any]:
            bearer, client_id = _resolve()
            result = await dispatch(
                tool=tool_name,
                args=args or {},
                client_id=client_id,
                bearer=bearer,
                base_url=base_url,
                approval_queue=approval_queue,
                memory_dispatcher=memory_dispatcher,
            )
            # dispatch()/_execute_tool() signal failure (bad/missing args,
            # unknown tool, denied/refused policy, REST 4xx/5xx, ...) by
            # returning ``{"status": "error", "error": {...}}`` rather than
            # raising — a normal return, so FastMCP's CallToolResult came
            # back ``isError: false`` with the error dict just sitting in
            # the content as if it were a successful payload (#1796). Raise
            # here so the lowlevel handler's ``except Exception`` path sets
            # ``isError: true`` the way every MCP client expects to detect
            # a failed call without string-sniffing the content.
            if isinstance(result, dict) and result.get("status") == "error":
                raise ToolError(json.dumps(result.get("error", result)))
            return result

        _tool.__name__ = tool_name
        _tool.__doc__ = description
        annotations = _ANNOTATIONS.get(tool_name)
        server.tool(name=tool_name, description=description, annotations=annotations)(_tool)

        # The wrapper's ``args: dict`` signature is deliberate — it passes the
        # whole arg dict through to ``dispatch`` untouched (a flat signature
        # would make FastMCP silently drop undeclared body fields). But that
        # signature also advertises an opaque ``{args: object}`` in tools/list,
        # leaving the caller to guess every field. Override the ADVERTISED
        # schema (not the validation model, which stays permissive) so the
        # nested ``args`` object carries the SAME per-tool schema the dashboard
        # agent chat surfaces — one source of truth, two envelopes.
        schema = tool_param_schema(tool_name)
        server._tool_manager.get_tool(tool_name).parameters = {
            "type": "object",
            "properties": {"args": schema},
            "required": ["args"] if schema["required"] else [],
        }

    for _name, _description in TOOL_DESCRIPTIONS.items():
        _register(_name, _description)

    return server


__all__ = [
    "AUTONOMOUS_READ_TOOLS",
    "AUTONOMOUS_WRITE_TOOLS",
    "EXCLUDED_ROUTES",
    "EXCLUDED_TOOLS",
    "GATED_TOOLS",
    "POLICY_NO_LOOSEN",
    "TOOL_DESCRIPTIONS",
    "TOOL_NAME_ALIASES",
    "TOOL_PARAM_HINTS",
    "_ANNOTATIONS",
    "ToolPolicy",
    "build_admin_route_map",
    "build_server",
    "dispatch",
    "install_admin_route_map",
    "is_gated",
    "set_admin_route_map",
    "tool_param_schema",
]
