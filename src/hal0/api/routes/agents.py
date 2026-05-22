"""Bundled-agent lifecycle endpoints (mounted under /api/agents).

Phase 8, ADR-0004. Thin wrapper around :class:`hal0.agents.AgentManager`
that mirrors the slot-route shape (``router`` + per-mutation
``_writer`` dep). The actual single-pick / atomic-swap / driver dispatch
logic lives in the manager so the CLI and the API share one
implementation.

Approval-queue endpoints (the ``/api/agent/approvals`` surface the CLI
``hal0 agent approvals`` subcommand consumes) are NOT defined here —
those are owned by the MCP-backend team per the wave-1 brief. We assume
the shape settled in ADR-0004 §5:

    GET    /api/agent/approvals
    POST   /api/agent/approvals/{id}/approve
    POST   /api/agent/approvals/{id}/deny

Coupling captured in ``WAVE1_INSTALLER_PENDING.md``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from hal0.agents import (
    AgentAlreadyInstalledError,
    AgentManager,
    AgentNotFoundError,
    HermesNotHal0AwareError,
)
from hal0.api.middleware.auth import require_writer
from hal0.errors import BadRequest, Conflict, Hal0Error, NotFound

_writer = [Depends(require_writer)]

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


# ── POST /api/agents/install ──────────────────────────────────────────────────


@router.post("/install", dependencies=_writer)
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


# ── DELETE /api/agents/{name} ─────────────────────────────────────────────────


@router.delete("/{name}", dependencies=_writer)
async def uninstall_agent(name: str) -> dict[str, str]:
    """Uninstall a bundled agent.

    Idempotent: removing an agent that isn't installed returns 200 OK
    with ``status="not_installed"`` rather than 404. Aligns with the
    slot-delete posture — operators running uninstall from a script
    shouldn't have to special-case the "already gone" branch.
    """
    mgr = _manager()
    try:
        installed = name in mgr.installed_names()
        mgr.uninstall(name)
    except AgentNotFoundError as exc:
        raise NotFound(str(exc), code="agent.unknown") from exc
    return {
        "name": name,
        "status": "uninstalled" if installed else "not_installed",
    }
