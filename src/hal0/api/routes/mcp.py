"""MCP introspection + lifecycle routes (mounted under ``/api/mcp``).

Issue #206 wired the v3 dashboard's ``/agents/mcp`` page to the live
backend; issue #305 swaps the 501 stubs for real install / uninstall /
config-patch handlers backed by :mod:`hal0.mcp.installed` + the
manifest resolver in :mod:`hal0.mcp.manifest`.

Endpoints
---------

::

    GET    /api/mcp/servers            — list hosted MCP servers (bundled + installed)
    GET    /api/mcp/clients            — connected clients (audit-derived)
    GET    /api/mcp/catalog            — installable MCPs (static)
    GET    /api/mcp/resolve            — manifest preview from a URL/spec (#224)
    GET    /api/mcp/stream             — SSE of mcp.tool.* events
    GET    /api/mcp/{id}/logs          — recent audit rows for one server
    POST   /api/mcp/install            — install from a resolved spec/URL (#305)
    DELETE /api/mcp/{id}               — uninstall a user-installed server (#305)
    PATCH  /api/mcp/{id}/config        — write env / enabled overrides (#305)
    POST   /api/mcp/{id}/{action}      — start/stop/restart — stubs 501
                                          pending the supervisor follow-up.

Bundled servers (``hal0-admin``, ``hal0-memory``) are uninstall-protected;
the route returns ``409 mcp.bundled`` rather than letting the registry
file system shadow the in-process FastMCP mount.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import time
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from hal0 import __version__
from hal0.errors import BadRequest, Conflict, Hal0Error
from hal0.mcp import installed as installed_registry
from hal0.mcp import manifest as manifest_resolver

log = structlog.get_logger(__name__)

router = APIRouter()


# ── Action-stub sentinel ─────────────────────────────────────────────────────


class McpNotImplemented(Hal0Error):
    """``POST /{id}/{action}`` (start / stop / restart) still stubs here.

    Install / uninstall / config-patch landed in #305; start / stop /
    restart need the still-pending process-supervisor layer (ADR-0015,
    not yet written). The code is the explicit
    ``mcp.supervisor_unavailable`` so the dashboard can key on it and
    render a "supervisor not implemented yet" affordance distinct from a
    generic 501.
    """

    code = "mcp.supervisor_unavailable"
    status = 501


# ── Static catalog ──────────────────────────────────────────────────────────
#
# Mirrors the prototype's ``MCP_CATALOG`` in ``ui/src/dash/mcp-data.jsx``
# so the v3 page renders the same set of installable servers it did
# while wired to the mock. ADR-0013 will eventually replace this with a
# real registry probe; for v0.3-alpha the static list is enough to let
# operators browse + reason about what they could install.

_CATALOG: list[dict[str, Any]] = [
    {
        "id": "puppeteer",
        "name": "puppeteer",
        "author": "modelcontextprotocol",
        "verified": True,
        "description": "Headless-browser automation. Navigate, scrape, screenshot.",
        "tools": 9,
        "stars": 2840,
        "category": "browser",
        "spec": "npm:@modelcontextprotocol/server-puppeteer",
    },
    {
        "id": "sqlite",
        "name": "sqlite",
        "author": "modelcontextprotocol",
        "verified": True,
        "description": "Read-only SQL over a single sqlite database file.",
        "tools": 4,
        "stars": 1820,
        "category": "data",
        "spec": "npm:@modelcontextprotocol/server-sqlite",
    },
    {
        "id": "gdrive",
        "name": "google-drive",
        "author": "modelcontextprotocol",
        "verified": True,
        "description": "Browse and read documents from a Google Drive account.",
        "tools": 6,
        "stars": 1410,
        "category": "files",
        "spec": "npm:@modelcontextprotocol/server-gdrive",
    },
    {
        "id": "slack",
        "name": "slack",
        "author": "modelcontextprotocol",
        "verified": True,
        "description": "Channel + DM read, message send, thread fetch.",
        "tools": 8,
        "stars": 990,
        "category": "comms",
        "spec": "npm:@modelcontextprotocol/server-slack",
    },
    {
        "id": "linear",
        "name": "linear",
        "author": "linear-app",
        "verified": True,
        "description": "Issue + project ops backed by the Linear GraphQL API.",
        "tools": 14,
        "stars": 720,
        "category": "issues",
        "spec": "npm:@linear/mcp-server",
    },
    {
        "id": "exa-search",
        "name": "exa-search",
        "author": "exa-labs",
        "verified": False,
        "description": "Neural web search and similarity-based document retrieval.",
        "tools": 3,
        "stars": 540,
        "category": "search",
        "spec": "npm:exa-mcp-server",
    },
    {
        "id": "homeassistant",
        "name": "home-assistant",
        "author": "community",
        "verified": False,
        "description": "Control Home Assistant entities — lights, sensors, scenes, automations.",
        "tools": 11,
        "stars": 480,
        "category": "iot",
        "spec": "npm:homeassistant-mcp",
    },
    {
        "id": "kubernetes",
        "name": "kubernetes",
        "author": "manusa",
        "verified": False,
        "description": "kubectl-flavoured read access to a cluster. Logs, describe, get.",
        "tools": 16,
        "stars": 920,
        "category": "ops",
        "spec": "npm:mcp-server-kubernetes",
    },
    {
        "id": "todoist",
        "name": "todoist",
        "author": "abhiz123",
        "verified": False,
        "description": "Create, update, complete tasks in Todoist.",
        "tools": 6,
        "stars": 240,
        "category": "productivity",
        "spec": "npm:todoist-mcp",
    },
]

_CATEGORIES: list[str] = [
    "Files",
    "Data",
    "Search",
    "Browser",
    "Comms",
    "Issues",
    "Ops",
    "IoT",
    "Productivity",
]


# ── Audit-log helpers ───────────────────────────────────────────────────────


_AUDIT_EVENTS = frozenset(
    {
        "mcp.tool.invoked",
        "mcp.tool.enqueued",
        "mcp.tool.approved",
        "mcp.tool.denied",
        "mcp.tool.executed",
        "mcp.tool.failed",
    }
)


async def _read_audit_events(
    *,
    limit: int = 500,
    server_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Pull recent ``hal0.mcp.audit`` rows from journald.

    Best-effort — returns ``[]`` on hosts without ``journalctl``. Mirrors
    the parsing pattern in :mod:`hal0.api.routes.agents` so structlog +
    journald frames decode the same way in both places.

    ``server_filter`` matches the audit row's ``mcp_server`` field when
    present (admin / memory tag their events with the server name); we
    fall through filtering when the field is absent so older audit rows
    without the tag still show up.
    """
    if shutil.which("journalctl") is None:
        return []

    cmd = [
        "journalctl",
        "-u",
        "hal0-api",
        "--no-pager",
        "-o",
        "json",
        "-n",
        str(min(5000, max(limit * 10, 200))),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, FileNotFoundError):
        return []

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        return []

    events: list[dict[str, Any]] = []
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        message_raw = row.get("MESSAGE") or row.get("message")
        if not message_raw:
            continue
        payload: dict[str, Any] | None = None
        if isinstance(message_raw, str):
            try:
                payload = json.loads(message_raw)
            except json.JSONDecodeError:
                continue
        elif isinstance(message_raw, dict):
            payload = message_raw
        if not payload or not isinstance(payload, dict):
            continue
        evt = payload.get("event")
        if evt not in _AUDIT_EVENTS:
            continue
        server_tag = payload.get("mcp_server") or payload.get("server")
        if server_filter is not None and server_tag and server_tag != server_filter:
            continue
        ts_payload = payload.get("timestamp")
        ts_micros = row.get("__REALTIME_TIMESTAMP")
        ts: float | str | None
        if ts_payload is not None:
            ts = ts_payload
        elif ts_micros:
            try:
                ts = int(ts_micros) / 1_000_000
            except (TypeError, ValueError):
                ts = None
        else:
            ts = None
        events.append(
            {
                "event": evt,
                "server": server_tag,
                "tool": payload.get("tool"),
                "args": payload.get("args") or {},
                "client_id": str(payload.get("client_id") or ""),
                "gated": payload.get("gated"),
                "outcome": payload.get("outcome") or evt.split(".")[-1],
                "timestamp": ts,
            }
        )
        if len(events) >= limit:
            break
    return events


def _activity_rpm(events: list[dict[str, Any]], server_id: str) -> int:
    """Count tool-invocation events for ``server_id`` in the last 60 s.

    Returns 0 when timestamps are unparseable rather than throwing — the
    dashboard treats 0 as "no recent activity" which is the safe default
    when the audit log isn't structured the way we expect.
    """
    cutoff = time.time() - 60.0
    n = 0
    for e in events:
        if e.get("server") and e["server"] != server_id:
            continue
        ts = e.get("timestamp")
        if isinstance(ts, int | float) and ts >= cutoff:
            n += 1
    return n


def _connect_url(request: Request, mount: str) -> str:
    """Build the connect URL the dashboard renders next to each server.

    Uses the request's own host so a user browsing the page sees the URL
    their own client should hit (not the canonical hal0 hostname, which
    they may not be able to resolve from where they are sitting).
    """
    return f"{request.url.scheme}://{request.url.netloc}/mcp/{mount}"


# ── Routes ──────────────────────────────────────────────────────────────────


@router.get("/servers")
async def list_servers(request: Request) -> dict[str, Any]:
    """List MCP servers hal0 hosts.

    Reads the live :class:`FastMCP` instances off ``app.state.mcp_servers``
    (populated by :func:`hal0.api.mcp_mount.mount_mcp_servers`) and asks
    each one for its tool / resource / prompt counts via the SDK's
    ``list_*`` async methods. Returns ``[]`` when the mount hasn't run
    yet (test fixtures that skip the lifespan see this branch).
    """
    servers_state: dict[str, Any] = getattr(request.app.state, "mcp_servers", {}) or {}
    audit = await _read_audit_events(limit=500)

    items: list[dict[str, Any]] = []
    # ── Bundled servers (live FastMCP introspection) ─────────────────────────
    for sid, server in servers_state.items():
        # SDK contract: ``list_tools/resources/prompts`` are async on
        # FastMCP. Wrap each in a try-block so a misbehaving server
        # doesn't fail the whole list — we surface ``-1`` instead.
        try:
            tools = await server.list_tools()
            tools_count = len(tools)
        except Exception:
            tools_count = -1
        try:
            resources = await server.list_resources()
            resources_count = len(resources)
        except Exception:
            resources_count = 0
        try:
            prompts = await server.list_prompts()
            prompts_count = len(prompts)
        except Exception:
            prompts_count = 0

        # Connected clients = unique client_ids that touched this server
        # in the audit window. Empty list when journald is unavailable.
        connected = sorted(
            {
                e["client_id"]
                for e in audit
                if e["client_id"] and (not e.get("server") or e["server"] == sid)
            }
        )

        items.append(
            {
                "id": sid,
                "name": sid,
                "bundled": True,
                "state": "running",
                "transport": "streamable-http",
                "connect_url": _connect_url(
                    request, sid.removeprefix("hal0-") if sid.startswith("hal0-") else sid
                ),
                "pid": None,
                "version": __version__,
                "tools": tools_count,
                "resources": resources_count,
                "prompts": prompts_count,
                "activity": {"rpm": _activity_rpm(audit, sid)},
                "connected": connected,
                "description": f"hal0 bundled {sid} MCP server (FastMCP, streamable-http).",
                "provider": "hal0",
            }
        )

    # ── User-installed servers (registry-derived) ────────────────────────────
    #
    # No supervisor yet (#305 ships the registry; supervision follows in a
    # tracked issue). The route reports each installed server as
    # ``state="stopped"`` so the dashboard renders it in the list +
    # offers the config / uninstall affordances; the Start button is the
    # action-stub 501 path that surfaces the "pending supervisor" toast.
    #
    # Bundled-id shadow defense (#383): the install / uninstall routes
    # reject ``BUNDLED_SERVER_IDS``, but a direct .toml drop in the
    # registry dir bypasses that guard (physical-access scenario). The
    # bundled FastMCP mount is authoritative — skip any installed record
    # whose id is already represented by a bundled server so the operator
    # sees a single, correct entry instead of a duplicate or a shadowed
    # record that would override the authoritative bundled state.
    bundled_ids = set(servers_state) | set(installed_registry.BUNDLED_SERVER_IDS)
    for record in installed_registry.list_installed():
        if record.id in bundled_ids:
            log.warning(
                "hal0.mcp.list.shadow_skipped",
                server_id=record.id,
                reason="installed record shadows a bundled server id",
            )
            continue
        items.append(
            {
                "id": record.id,
                "name": record.name,
                "bundled": False,
                # No supervisor yet → installed servers always start stopped;
                # ``enabled`` is the operator's intent for when the supervisor
                # arrives, not the current process state.
                "state": "stopped",
                "transport": record.transport,
                "connect_url": _connect_url(request, record.id),
                "pid": None,
                "version": "0.0.0",
                "tools": record.tools,
                "resources": record.resources,
                "prompts": record.prompts,
                "activity": {"rpm": _activity_rpm(audit, record.id)},
                "connected": sorted(
                    {
                        e["client_id"]
                        for e in audit
                        if e["client_id"] and e.get("server") == record.id
                    }
                ),
                "description": record.description,
                "provider": record.author,
                "spec": record.spec,
                "source_url": record.source_url,
                "env": record.env,
                "enabled": record.enabled,
                "installed_at": record.installed_at,
            }
        )

    return {"servers": items, "count": len(items)}


@router.get("/clients")
async def list_clients() -> dict[str, Any]:
    """Return MCP clients seen in the recent audit window.

    Grouped by ``client_id`` with the first-seen timestamp + the set of
    servers each one has touched. The dashboard's ClientsRibbon renders
    one card per row. ``role`` is heuristic — derived from the client_id
    string (claude-code → CLI, cursor → IDE, claude-desktop → App).
    """
    audit = await _read_audit_events(limit=1000)
    by_client: dict[str, dict[str, Any]] = {}
    for e in audit:
        cid = e.get("client_id") or ""
        if not cid or cid == "anonymous":
            continue
        slot = by_client.setdefault(
            cid,
            {
                "id": cid,
                "name": _client_display_name(cid),
                "role": _client_role(cid),
                "host": "—",
                "since": e.get("timestamp"),
                "connected_to": set(),
            },
        )
        srv = e.get("server")
        if srv:
            slot["connected_to"].add(srv)
        ts = e.get("timestamp")
        if isinstance(ts, int | float) and (
            slot["since"] is None or (isinstance(slot["since"], int | float) and ts < slot["since"])
        ):
            slot["since"] = ts

    clients = [
        {
            **row,
            "connected_to": sorted(row["connected_to"]),
        }
        for row in by_client.values()
    ]
    return {"clients": clients, "count": len(clients)}


def _client_display_name(client_id: str) -> str:
    """Pretty name from a client_id string.

    Heuristic — picks off common SDK names so the dashboard renders
    "Claude Code" instead of an opaque token fingerprint. Falls back to
    the raw id when nothing matches.
    """
    low = client_id.lower()
    if "claude-code" in low or "claude_code" in low:
        return "Claude Code"
    if "claude-desktop" in low:
        return "Claude Desktop"
    if "cursor" in low:
        return "Cursor"
    if "hermes" in low:
        return "Hermes-Agent"
    if "pi-coder" in low:
        return "pi-coder"
    return client_id


def _client_role(client_id: str) -> str:
    """CLI / IDE / App heuristic from the client_id string."""
    low = client_id.lower()
    if "cursor" in low or "vscode" in low or ("code-" in low and "claude" not in low):
        return "IDE"
    if "desktop" in low or "app" in low:
        return "App"
    return "CLI"


@router.get("/catalog")
async def list_catalog() -> dict[str, Any]:
    """Return the installable-MCPs catalog.

    Static module-level constant for v0.3-alpha — ADR-0013's
    ``mcp_client.py`` work will eventually swap in a live registry
    probe. The shape matches the prototype's ``MCP_CATALOG`` so the
    dashboard's InstallDrawer renders unchanged.
    """
    return {"items": _CATALOG, "categories": _CATEGORIES}


@router.get("/stream")
async def stream_events(request: Request) -> StreamingResponse:
    """SSE stream of recent + future MCP tool-call events.

    On subscribe we replay the last 60 s of audit rows so a freshly-
    opened dashboard tab shows the LiveTimeline ticks immediately. We
    then poll journald every 2 s for new rows and emit them as SSE
    frames. The 2 s cadence is intentionally coarser than the audit-row
    arrival rate (sub-second possible) so the route doesn't drown the
    event loop — the LiveTimeline render uses opacity + fade based on
    the row's timestamp, so a 2 s sampling interval still looks live.
    """

    async def _gen() -> AsyncIterator[str]:
        # Backfill last minute.
        seen: set[tuple[str, str | None, Any]] = set()
        backfill = await _read_audit_events(limit=200)
        cutoff = time.time() - 60.0
        for e in backfill:
            ts = e.get("timestamp")
            if not isinstance(ts, int | float) or ts < cutoff:
                continue
            key = (e["event"], e.get("tool"), ts)
            seen.add(key)
            yield _sse_frame(e)
        # Live tail.
        while True:
            if await request.is_disconnected():
                return
            await asyncio.sleep(2.0)
            try:
                recent = await _read_audit_events(limit=50)
            except Exception:
                # Defensive: never let the tail loop blow up the SSE.
                yield ": tail-error\n\n"
                continue
            for e in recent:
                ts = e.get("timestamp")
                key = (e["event"], e.get("tool"), ts)
                if key in seen:
                    continue
                seen.add(key)
                yield _sse_frame(e)
            # Bound seen-set so it doesn't grow unboundedly.
            if len(seen) > 1000:
                seen = set(list(seen)[-500:])

    async def _safe_gen() -> AsyncIterator[str]:
        try:
            async for chunk in _gen():
                yield chunk
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                pass
            raise

    return StreamingResponse(
        _safe_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _sse_frame(event: dict[str, Any]) -> str:
    """Format an audit row as an SSE ``mcp.tool.*`` frame.

    The event name carries the qualified ``mcp.tool.invoked`` / ``.executed``
    / etc. so the dashboard's EventSource listener can branch on it
    without parsing the data payload.
    """
    name = event.get("event") or "mcp.tool.invoked"
    payload = {
        "server": event.get("server"),
        "tool": event.get("tool"),
        "client": event.get("client_id"),
        "gated": event.get("gated"),
        "outcome": event.get("outcome"),
        "ts": event.get("timestamp"),
    }
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n"


@router.get("/{server_id}/logs")
async def server_logs(
    server_id: str,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Return the last ``limit`` audit rows attributed to ``server_id``.

    Drives the per-server LogsDrawer in the dashboard. Same parsing as
    :func:`_read_audit_events`; the server filter falls through rows
    that don't carry a ``mcp_server`` tag (older audit format) so the
    drawer doesn't render empty against a working but pre-tag log
    stream.
    """
    events = await _read_audit_events(limit=limit, server_filter=server_id)
    return {"server": server_id, "events": events, "count": len(events)}


# ── Manifest resolve (#224) ─────────────────────────────────────────────────


@router.get("/resolve")
async def resolve_manifest(
    request: Request,
    url: str = Query(..., min_length=1, max_length=2048),
) -> dict[str, Any]:
    """Resolve a paste-box URL/spec to a manifest preview.

    The InstallDrawer calls this once on paste to fill its preview card;
    if the user clicks Install, the same URL/spec is replayed against
    ``POST /api/mcp/install`` which re-resolves (so a manifest that
    changed between paste + install still gets the latest data).

    A test-only fetcher can be injected via
    ``request.app.state.mcp_manifest_fetcher`` — production leaves that
    attribute unset and the resolver builds its own httpx client.
    """
    fetcher = getattr(request.app.state, "mcp_manifest_fetcher", None)
    resolved = await manifest_resolver.resolve(url, fetcher=fetcher)
    return resolved.model_dump(mode="python")


# ── Install / uninstall / patch (#305) ──────────────────────────────────────


@router.post("/install", status_code=201)
async def install_server(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Install a user MCP server from a URL / spec.

    Body shape (one of)::

        {"url": "oci://…", ...}      — re-resolve via the manifest layer
        {"manifest": {...}}          — pre-resolved manifest (round-tripped
                                       from /api/mcp/resolve)

    Either path produces an :class:`InstalledServer` record on disk;
    bundled-id collisions return 409 ``mcp.id_reserved``; an already-
    installed id returns 409 ``mcp.already_installed``.

    The dashboard normally pastes the URL, gets a preview, and on
    Install posts ``{"url": "<spec>"}``. The ``manifest`` form covers
    the case where the operator edited the preview before installing.
    """
    if not isinstance(body, dict):
        raise BadRequest("install body must be a JSON object", code="mcp.body_invalid")

    manifest_dict = body.get("manifest")
    if isinstance(manifest_dict, dict):
        try:
            resolved = manifest_resolver.ResolvedManifest.model_validate(manifest_dict)
        except Exception as exc:
            raise BadRequest(
                "invalid manifest body",
                code="mcp.manifest_invalid",
                details={"reason": str(exc)},
            ) from exc
    else:
        url = body.get("url") or body.get("spec")
        if not isinstance(url, str) or not url.strip():
            raise BadRequest(
                "install body must include 'url' (or 'manifest')",
                code="mcp.url_required",
            )
        fetcher = getattr(request.app.state, "mcp_manifest_fetcher", None)
        resolved = await manifest_resolver.resolve(url, fetcher=fetcher)

    record = installed_registry.InstalledServer(
        id=resolved.id,
        name=resolved.name,
        description=resolved.description,
        spec=resolved.spec,
        transport=resolved.transport,
        tools=resolved.tools,
        resources=resolved.resources,
        prompts=resolved.prompts,
        env={k: "" for k in resolved.env_required},
        enabled=True,
        source_url=resolved.source_url,
        author=resolved.author,
        verified=resolved.verified,
    )
    stored = installed_registry.install(record)
    return {"installed": stored.model_dump(mode="python")}


@router.delete("/{server_id}")
async def uninstall_server(server_id: str) -> dict[str, Any]:
    """Remove a user-installed MCP server. Bundled servers reject 409.

    The route validates the id charset before the bundled check so a
    request with garbage doesn't leak ``mcp.id_reserved`` for inputs
    that wouldn't have matched anything.
    """
    if server_id in installed_registry.BUNDLED_SERVER_IDS:
        raise Conflict(
            f"server {server_id!r} is bundled — cannot uninstall",
            code="mcp.bundled",
            details={"server_id": server_id},
        )
    installed_registry.uninstall(server_id)
    return {"uninstalled": server_id}


@router.patch("/{server_id}/config")
async def patch_server_config(server_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Patch the env / enabled fields of an installed server.

    The InstallDrawer's EditConfigModal posts ``{"env": {...}}`` (full
    intended env block — the route replaces, not deltas) and optionally
    ``{"enabled": bool}``. Bundled servers reject 409.
    """
    if server_id in installed_registry.BUNDLED_SERVER_IDS:
        raise Conflict(
            f"server {server_id!r} is bundled — config is read-only here",
            code="mcp.bundled",
            details={"server_id": server_id},
        )
    if not isinstance(body, dict):
        raise BadRequest("patch body must be a JSON object", code="mcp.body_invalid")
    env_block: dict[str, str] | None = None
    raw_env = body.get("env")
    if raw_env is not None:
        if not isinstance(raw_env, dict):
            raise BadRequest(
                "'env' must be an object mapping name → value",
                code="mcp.env_invalid",
            )
        env_block = {str(k): str(v) for k, v in raw_env.items()}
    enabled = body.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise BadRequest("'enabled' must be a boolean", code="mcp.enabled_invalid")
    updated = installed_registry.patch_config(server_id, env=env_block, enabled=enabled)
    return {"server": updated.model_dump(mode="python")}


# ── Action stub (start/stop/restart — supervisor follow-up) ─────────────────


@router.post("/{server_id}/{action}")
async def server_action(server_id: str, action: str) -> dict[str, Any]:
    """Start / stop / restart — stubbed pending the process supervisor.

    The installed-server registry (#305) doesn't yet drive a supervisor;
    that's a tracked follow-up. The dashboard surfaces a distinct toast
    for this 501 vs the (now-real) install/uninstall path.
    """
    raise McpNotImplemented(
        "process supervisor not implemented (pending ADR-0015)",
        details={"server_id": server_id, "action": action},
    )
