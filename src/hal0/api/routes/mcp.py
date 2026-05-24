"""MCP introspection routes (mounted under ``/api/mcp``).

Issue #206 — wires the v3 dashboard's ``/agents/mcp`` page to the live
backend. Surfaces a read-only view of the MCP servers hal0 hosts (the
two bundled servers built by :mod:`hal0.mcp.admin` + :mod:`hal0.mcp.memory`),
the clients currently using them (derived from the ``hal0.mcp.audit``
journald stream), a static catalog of installable MCPs, and a Server-
Sent-Events tail of tool invocations.

Out of scope (ADR-0013 ``mcp_client.py`` work):
  - install / uninstall / restart / config-write — these stub with 501
    so the dashboard can render the toast hint without the routes being
    absent. The actual lifecycle work owns a separate follow-up PR.

Endpoints
---------

::

    GET  /api/mcp/servers            — list hosted MCP servers
    GET  /api/mcp/clients            — connected clients (audit-derived)
    GET  /api/mcp/catalog            — installable MCPs (static)
    GET  /api/mcp/stream             — SSE of mcp.tool.* events
    GET  /api/mcp/{id}/logs          — recent audit rows for one server
    POST /api/mcp/install            — 501 (ADR-0013 follow-up)
    DELETE /api/mcp/{id}             — 501 (ADR-0013 follow-up)
    POST /api/mcp/{id}/{action}      — 501 (ADR-0013 follow-up)
    PATCH /api/mcp/{id}/config       — 501 (ADR-0013 follow-up)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from hal0 import __version__
from hal0.errors import Hal0Error

router = APIRouter()


# ── 501 sentinel ─────────────────────────────────────────────────────────────


class McpNotImplemented(Hal0Error):
    """Lifecycle mutations stub here until ADR-0013 lands.

    The dashboard surfaces a toast when these endpoints reply 501 — see
    ``ui/src/api/hooks/useMcp.ts``. The error code is stable so the
    toast text can branch on it instead of the (more brittle) message.
    """

    code = "mcp.not_implemented"
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


# ── 501 stubs (ADR-0013 follow-up) ──────────────────────────────────────────


@router.post("/install")
async def install_server(body: dict[str, Any]) -> dict[str, Any]:
    """Stub — install/uninstall lifecycle lives in ADR-0013's mcp_client.py work."""
    raise McpNotImplemented(
        "MCP install pending ADR-0013 follow-up (#206)",
        details={"requested": body},
    )


@router.delete("/{server_id}")
async def uninstall_server(server_id: str) -> dict[str, Any]:
    """Stub — uninstall ships with ADR-0013."""
    raise McpNotImplemented(
        "MCP uninstall pending ADR-0013 follow-up (#206)",
        details={"server_id": server_id},
    )


@router.post("/{server_id}/{action}")
async def server_action(server_id: str, action: str) -> dict[str, Any]:
    """Stub — restart / start / stop ship with ADR-0013."""
    raise McpNotImplemented(
        f"MCP {action!r} pending ADR-0013 follow-up (#206)",
        details={"server_id": server_id, "action": action},
    )


@router.patch("/{server_id}/config")
async def patch_server_config(server_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Stub — config write lands with ADR-0013."""
    raise McpNotImplemented(
        "MCP config patch pending ADR-0013 follow-up (#206)",
        details={"server_id": server_id, "patch": body},
    )
