"""Bundled-agent lifecycle endpoints (mounted under /api/agents).

Phase 8, ADR-0004. Thin wrapper around :class:`hal0.agents.AgentManager`
that mirrors the slot-route shape (``router`` + per-mutation
``_writer`` dep). The actual single-pick / atomic-swap / driver dispatch
logic lives in the manager so the CLI and the API share one
implementation.

Approval-queue endpoints (the ``/api/agent/approvals`` surface the CLI
``hal0 agent approvals`` subcommand consumes) live in
:mod:`hal0.api.routes.approvals` and are mounted in-process from
:mod:`hal0.api` next to this router. Shape per ADR-0004 §5:

    GET    /api/agent/approvals
    POST   /api/agent/approvals/{id}/approve
    POST   /api/agent/approvals/{id}/deny
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil

from fastapi import APIRouter, Query

from hal0.agents import (
    AgentAlreadyInstalledError,
    AgentManager,
    AgentNotFoundError,
    HermesNotHal0AwareError,
)
from hal0.agents.manager import BUNDLED_AGENTS
from hal0.agents.persona import AGENT_SKILLS, PERSONA_TONES, PERSONA_TOOLS
from hal0.errors import BadRequest, Conflict, Hal0Error, NotFound

router = APIRouter()


def _manager() -> AgentManager:
    # Stateless — no shared state to wire onto app.state. A per-request
    # instance keeps the file-system read fresh (matches manager
    # docstring re: hot reload).
    return AgentManager()


# ── GET /api/agents ───────────────────────────────────────────────────────────


@router.get("")
async def list_agents() -> dict[str, object]:
    """List installed bundled agents (zero or one for v0.2)."""
    mgr = _manager()
    items = [rec.as_dict() for rec in mgr.list()]
    return {"agents": items, "count": len(items)}


# ── GET /api/agents/persona-enums ────────────────────────────────────────────


@router.get("/persona-enums")
async def persona_enums() -> dict[str, object]:
    """Enum payload for PersonaEditModal (#226).

    Returns the canonical tone presets + the allowed-tools catalogue
    the modal renders. Tones + tools live in
    :mod:`hal0.agents.persona`; adding new entries lands there.
    """
    return {
        "tones": list(PERSONA_TONES),
        "tools": list(PERSONA_TOOLS),
    }


# ── GET /api/agents/skills ───────────────────────────────────────────────────


@router.get("/skills")
async def list_skills() -> dict[str, object]:
    """Catalogue for the dashboard's Agent → Skills tab (#227).

    Static for v0.3 — sources from :data:`hal0.agents.persona.AGENT_SKILLS`.
    The ``calls`` column is omitted at the API layer; the dashboard
    shows zero counts until #227's follow-up wires the journal-derived
    counters.
    """
    return {
        "skills": list(AGENT_SKILLS),
        "count": len(AGENT_SKILLS),
    }


# ── POST /api/agents/install ──────────────────────────────────────────────────


@router.post("/install")
async def install_agent(body: dict[str, object]) -> dict[str, object]:
    """Install a bundled agent. Body shape: ``{"name": str, "switch"?:
    bool}``.

    Single-pick enforced by the manager. ``switch=true`` triggers an
    atomic uninstall-then-install. The Bearer token wired into the
    agent's adapter config is NOT taken from the request — the
    installer scripts read ``/etc/hal0/tokens.toml`` on the host so the
    agent is always pinned to a token the operator can rotate
    independently of this API call.
    """
    name = body.get("name") if isinstance(body, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise BadRequest("'name' is required (non-empty string)", code="agent.name_required")
    switch = bool(body.get("switch", False)) if isinstance(body, dict) else False

    mgr = _manager()
    try:
        rec = mgr.install(name, switch=switch)
    except AgentNotFoundError as exc:
        raise NotFound(str(exc), code="agent.unknown") from exc
    except AgentAlreadyInstalledError as exc:
        # 409 maps naturally — single-pick is a state-conflict, not a
        # validation failure.
        raise Conflict(str(exc), code="agent.already_installed") from exc
    except HermesNotHal0AwareError as exc:
        # 409 because the conflict is with the host's upstream Hermes
        # build, not with anything the caller can fix by editing the
        # request body. The error message carries the actionable hint
        # (the docstring on the exception class lays this out).
        raise Conflict(str(exc), code="agent.hermes_not_hal0_aware") from exc
    except Hal0Error:
        raise
    except Exception as exc:
        # Driver subprocess failures, FS errors, etc. — surface as a
        # generic 5xx-style Hal0Error so the envelope middleware
        # renders consistently.
        raise Hal0Error(
            f"install failed for {name!r}: {type(exc).__name__}: {exc}",
            code="agent.install_failed",
        ) from exc

    return rec.as_dict()


# ── GET /api/agents/{name}/activity ──────────────────────────────────────────


@router.get("/{name}/activity")
async def agent_activity(
    name: str,
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, object]:
    """Return the last ``limit`` MCP audit rows attributed to ``name``.

    Reads from journald (``hal0.mcp.audit`` logger; see ``hal0/mcp/__init__``
    module docstring). Each row carries ``client_id, tool, args, gated,
    outcome, timestamp`` — the dashboard's Activity tab renders them
    verbatim, filtering client-side by tool / status.

    Best-effort: returns ``{events: [], hint}`` on hosts without
    journalctl. ``client_id`` filter is substring-matched against the
    agent name so e.g. ``pi-coder`` matches client_ids like
    ``pi-coder@host``. The MCP server attributes ``client_id`` from the
    Bearer token (ADR-0004 §7) so the agent identity is forensically
    grounded.
    """
    mgr = _manager()
    if name not in mgr.installed_names() and name not in BUNDLED_AGENTS:
        # 404 when the name isn't a recognised bundled agent at all. An
        # installed-but-uninstalled-since case still serves history so
        # operators can audit a removed agent's last actions.
        raise NotFound(f"unknown agent {name!r}", code="agent.unknown")

    if shutil.which("journalctl") is None:
        return {
            "agent": name,
            "events": [],
            "count": 0,
            "hint": "journalctl not available on this host",
        }

    # ``-o json`` gives one structlog-formatted line per row. We pull
    # the API unit's log (the MCP servers run in the API process — see
    # /grill-with-docs session 2026-05-22) and filter for the audit
    # logger name on the way back.
    cmd = [
        "journalctl",
        "-u",
        "hal0-api",
        "--no-pager",
        "-o",
        "json",
        "-n",
        # Pull more than the requested limit because filtering by audit
        # logger discards a lot of noise from the same unit.
        str(min(5000, max(limit * 20, 200))),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, FileNotFoundError):
        return {
            "agent": name,
            "events": [],
            "count": 0,
            "hint": "journalctl invocation failed",
        }

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        return {
            "agent": name,
            "events": [],
            "count": 0,
            "hint": "journalctl timed out",
        }

    events: list[dict[str, object]] = []
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        # journalctl wraps the structlog payload in MESSAGE; structlog
        # serialises as JSON when configured for journald.
        message_raw = row.get("MESSAGE") or row.get("message")
        if not message_raw:
            continue
        payload: dict[str, object] | None = None
        if isinstance(message_raw, str):
            try:
                payload = json.loads(message_raw)
            except json.JSONDecodeError:
                continue
        elif isinstance(message_raw, dict):
            payload = message_raw
        if not payload or not isinstance(payload, dict):
            continue
        if payload.get("logger") not in (None, "hal0.mcp.audit"):
            # Some structlog configs put logger name under different keys —
            # accept either by leaving the None branch through.
            pass
        # The audit event itself uses the structlog "event" key; we match
        # on the canonical mcp.tool.invoked + the family of approval-
        # related events emitted alongside it.
        evt = payload.get("event")
        if evt not in {
            "mcp.tool.invoked",
            "mcp.tool.enqueued",
            "mcp.tool.approved",
            "mcp.tool.denied",
            "mcp.tool.executed",
            "mcp.tool.failed",
        }:
            continue
        client_id = str(payload.get("client_id") or "")
        if name not in client_id and client_id != name:
            # client_id might be e.g. "pi-coder" or "pi-coder@host". Match
            # both with a contains check; agents installed via the MCP
            # adapter all carry their own canonical name.
            continue
        events.append(
            {
                "id": row.get("__CURSOR") or payload.get("id"),
                "timestamp": payload.get("timestamp")
                or (
                    int(row.get("__REALTIME_TIMESTAMP", "0")) / 1_000_000
                    if row.get("__REALTIME_TIMESTAMP")
                    else None
                ),
                "tool": payload.get("tool"),
                "args": payload.get("args") or {},
                "gated": payload.get("gated"),
                "outcome": payload.get("outcome") or evt.split(".")[-1],
                "client_id": client_id,
            }
        )
        if len(events) >= limit:
            break

    return {
        "agent": name,
        "events": events,
        "count": len(events),
    }


# ── DELETE /api/agents/{name} ─────────────────────────────────────────────────


@router.delete("/{name}")
async def uninstall_agent(name: str) -> dict[str, str]:
    """Uninstall a bundled agent.

    Idempotent: removing an agent that isn't installed returns 200 OK
    with ``status="not_installed"`` rather than 404. Aligns with the
    slot-delete posture — operators running uninstall from a script
    shouldn't have to special-case the "already gone" branch.

    Status derives from whether ``mgr.uninstall()`` actually removed
    anything (#346) — the old code consulted ``installed_names()``
    BEFORE the uninstall call and that view only saw the seed TOML, so a
    half-uninstalled agent whose seed was already gone reported
    ``not_installed`` even while ``rm -rf``'ing its data + state dirs.
    """
    mgr = _manager()
    try:
        removed = mgr.uninstall(name)
    except AgentNotFoundError as exc:
        raise NotFound(str(exc), code="agent.unknown") from exc
    return {
        "name": name,
        "status": "uninstalled" if removed else "not_installed",
    }
