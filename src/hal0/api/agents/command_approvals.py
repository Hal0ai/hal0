"""Hermes command approvals routes.

``GET  /api/agents/{agent_id}/command-approvals`` — the setting, what is in
effect and why (:func:`hal0.agents.hermes_command_approvals.status`).

``PUT  /api/agents/{agent_id}/command-approvals`` — body ``{"mode": "smart" |
"manual"}``. Persists the setting, re-renders Hermes's ``approvals.mode`` and
restarts ``hal0-agent@hermes`` when it is running; all or nothing
(:func:`hal0.agents.hermes_command_approvals.apply_mode`).

Not hal0's MCP tool approval queue (``/api/agent/approvals``): this is
Hermes's own gate on dangerous terminal commands.

Auth: ADMIN, inherited from the ``/api/agents`` prefix rule in
:mod:`hal0.security.exposure`. The admin MCP server never exposes these
routes as tools (``hal0.mcp.admin.EXCLUDED_TOOLS["hermes_command_approvals"]``),
and the PUT refuses a request whose ``X-hal0-Agent`` header names anything
but an operator surface. That header is self-asserted, so this is defence in
depth for calls relayed through hal0's own MCP plumbing, not a boundary: under
ADR-0024 an agent's Bearer token is still the box admin key, and an agent with
the terminal tool on can call this route like any other ADMIN route.
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import APIRouter, Request

from hal0.api.agents import restart as restart_seam
from hal0.errors import BadRequest, Forbidden, NotFound

router = APIRouter()

#: The only agent with this setting. Literal here (not imported) so importing
#: hal0.api never loads Hermes code (tests/golden_paths/test_gp15_no_hermes.py);
#: the handlers import hal0.agents.hermes_command_approvals lazily.
_AGENT_ID: Final[str] = "hermes"

#: ``X-hal0-Agent`` values that identify an operator surface rather than an
#: agent. Absent header = the dashboard (see ``restart._resolve_actor``).
OPERATOR_ACTORS: Final[frozenset[str]] = frozenset({"hal0-dashboard", "hal0-cli"})


def _resolve_agent(agent_id: str) -> None:
    if agent_id != _AGENT_ID:
        raise NotFound(
            f"agent {agent_id!r} has no command-approvals setting",
            code="agent.unknown",
            details={"agent_id": agent_id},
        )


def _operator_actor(request: Request) -> str:
    """Return the caller label, refusing one that identifies as an agent."""
    raw = request.headers.get("x-hal0-agent")
    actor = raw.strip() if isinstance(raw, str) and raw.strip() else "hal0-dashboard"
    if actor not in OPERATOR_ACTORS:
        raise Forbidden(
            "Hermes command approvals can only be changed by the operator, "
            f"not by an agent (X-hal0-Agent: {actor!r}).",
            code="agent.command_approvals_agent_refused",
            details={"actor": actor},
        )
    return actor


@router.get("/{agent_id}/command-approvals")
async def get_command_approvals(agent_id: str) -> dict[str, Any]:
    _resolve_agent(agent_id)
    from hal0.agents import hermes_command_approvals as cmd_approvals

    return cmd_approvals.status()


@router.put("/{agent_id}/command-approvals")
async def put_command_approvals(agent_id: str, request: Request) -> dict[str, Any]:
    _resolve_agent(agent_id)
    actor = _operator_actor(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("request body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict) or "mode" not in body:
        raise BadRequest(
            'request body must be {"mode": "smart" | "manual"}',
            code="agent.command_approvals_invalid",
        )

    from hal0.agents import hermes_command_approvals as cmd_approvals

    async def _unit_active() -> bool | None:
        return await restart_seam.unit_is_active(agent_id)

    async def _restart() -> dict[str, Any]:
        return await restart_seam.restart_agent_unit(agent_id, actor=actor)

    result = await cmd_approvals.apply_mode(
        body["mode"], actor=actor, unit_active=_unit_active, restart=_restart
    )
    if result.get("changed"):
        # The card shows the mode and whether it is in sync: drop the cached build.
        # Imported here, like the Hermes module above (GP15).
        from hal0.api.agents.overview import invalidate

        invalidate(agent_id)
    return result


__all__ = ["OPERATOR_ACTORS", "router"]
