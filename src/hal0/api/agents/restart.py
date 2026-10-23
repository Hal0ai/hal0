"""Restart endpoints for bundled agents (v0.3 PR-11).

``POST /api/agents/{agent_id}/restart`` restarts ``hal0-agent@{agent_id}.service``
and ``POST /api/agents/{agent_id}/gateway/restart`` restarts the Hermes
messaging gateway, ``hermes-gateway.service``. The sidebar agent block
(SidebarAgentBlock), the dashboard service-status chip and the agent cards all
want a one-click "restart this" action that doesn't require dropping to a
terminal.

Why a dedicated module
----------------------
Restart is a state-mutation on a systemd unit, so it carries the same audit
obligations as the rest of the v0.3 agent surface. Keeping it in its own module
lets the test seam (``hal0.api.agents.restart``) stay narrow and lets the
audit-log invocation live alongside the unit call rather than getting tangled
up with the lifecycle routes.

Privilege
---------
hal0-api runs as the unprivileged ``hal0`` user, and a bare ``systemctl
restart`` is denied by polkit on a provisioned box (#2913). The restart goes
through :func:`hal0.services.systemd.unit_action`, which runs the verb through
:class:`hal0.system.seam.SystemCtlSeam` — the same sudo-wrapped path the slot
and companion-service restarts take.

Resolution
----------
v0.3 only resolves ``"hermes"`` (single-pick). The agent
registry mirrors :mod:`hal0.api.agents.personas` — adding a future
bundled agent lights up restart automatically without touching this file.

Audit log
---------
Every restart emits a structlog event on the ``hal0.agents.audit``
logger so the dashboard's Activity tab + ``journalctl -u hal0-api`` can
correlate the action with the resulting unit state. The shape mirrors
the ``mcp.tool.*`` audit events the approval inbox writes
(:mod:`hal0.api.routes.agents` ``agent_activity``).
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from typing import Final

import structlog
from fastapi import APIRouter, Request

from hal0.errors import Hal0Error, NotFound
from hal0.services.systemd import unit_action

log = structlog.get_logger(__name__)
audit_log = structlog.get_logger("hal0.agents.audit")
router = APIRouter()


# Mirror :data:`hal0.api.agents.personas._AGENT_PERSONAS_ROOTS` — one
# place per file that knows about agent identity, so a future bundled
# agent lights up restart automatically. Stored as a frozenset to
# emphasise the "membership check only" intent.
_KNOWN_AGENT_IDS: Final[frozenset[str]] = frozenset({"hermes"})

#: The Hermes messaging gateway, restarted by its own route (the agent unit does
#: not carry it). ``COMPANION_SERVICE_UNITS`` maps it to the wrapper's
#: ``hermes-gateway`` service key.
GATEWAY_UNIT: Final[str] = "hermes-gateway.service"

#: Longest failure message that reaches the HTTP envelope (the old spawn path
#: trimmed stderr to the same bound); keeps a chatty systemctl error bounded.
_MAX_DETAIL_CHARS: Final[int] = 200

#: Agents that have a gateway to restart. Separate from ``_KNOWN_AGENT_IDS`` so
#: a future bundled agent without a messaging gateway (pi) stays 404 on
#: ``/{agent_id}/gateway/restart`` even once it joins the restart registry.
_GATEWAY_AGENTS: Final[frozenset[str]] = frozenset({"hermes"})

# Timeout for the read-only ``is-active`` probe. Much tighter than a restart's
# budget (``unit_action`` allows 90s): this one runs on a POLLED route (``GET /api/agents``, 5s from
# the dashboard), so a wedged systemd bus must degrade to "unknown" fast rather
# than hold a request open. ``is-active`` needs no polkit authorization and
# does no unit work, so a healthy host answers in single-digit milliseconds.
IS_ACTIVE_TIMEOUT_SECONDS: Final[float] = 2.0


def _systemctl_path() -> str | None:
    """Resolve ``systemctl`` on PATH.

    Returns ``None`` on hosts that don't have systemd (containers, CI
    macOS runners, the hal0-dev VM when tested via Docker). Used by the
    read-only :func:`unit_is_active` probe; restarts go through
    :func:`hal0.services.systemd.unit_action` instead.
    """
    return shutil.which("systemctl")


def _unit_name(agent_id: str) -> str:
    """Compose the unit name for an agent id.

    Matches ``installer/systemd/hal0-agent@.service`` — the template
    unit is parameterised by agent id so a future bundled agent is a drop-in.
    """
    return f"hal0-agent@{agent_id}.service"


async def unit_is_active(agent_id: str) -> bool | None:
    """Probe ``systemctl is-active hal0-agent@{agent_id}.service``.

    The liveness half of this module's systemctl seam (#1459).
    ``AgentManager.list()`` is a filesystem read, so ``GET /api/agents`` could
    only ever report whether a bundle is *installed*; the dashboard was
    treating that as "running" and rendering an inactive Hermes as ready.

    Returns:
        ``True``  — the unit is active.
        ``False`` — systemctl answered with any other state
                    (``inactive`` / ``failed`` / ``activating`` / ...).
        ``None``  — the probe could not run at all: no systemd on the host
                    (container, CI runner, dev box), a spawn failure, a
                    timeout, or empty output.

    ``None`` means UNKNOWN and must never be rendered as healthy — it is the
    honest answer for a host where liveness is unobservable, and the caller is
    responsible for keeping it distinct from ``True``.

    Read-only and unprivileged: ``is-active`` needs no polkit authorization,
    which is why this can live on a polled route while ``restart`` cannot.
    """
    systemctl = _systemctl_path()
    if systemctl is None:
        return None

    try:
        proc = await asyncio.create_subprocess_exec(
            systemctl,
            "is-active",
            _unit_name(agent_id),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError) as exc:
        log.warning("agent.is_active.spawn_failed", agent_id=agent_id, error=str(exc))
        return None

    try:
        stdout_bytes, _ = await asyncio.wait_for(
            proc.communicate(),
            timeout=IS_ACTIVE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        log.warning("agent.is_active.timeout", agent_id=agent_id)
        return None

    # `is-active` exits non-zero for an inactive unit, so the return code is
    # not the signal — the state word on stdout is.
    state = (stdout_bytes or b"").decode("utf-8", errors="replace").strip()
    if not state:
        return None
    return state == "active"


def _resolve_actor(request: Request) -> str:
    """Identify the caller for the audit log.

    There is no Bearer token store (auth was removed in v0.3). We fall back
    to the ``X-hal0-Agent`` header (see memory ``hal0_v0.3_auth_removed``),
    defaulting to ``"hal0-dashboard"`` when neither is present. Best-effort
    forensic grounding — the audit row is for "what happened", not "who
    can do this" (the LAN-trust posture handles authorization).
    """
    actor = request.headers.get("x-hal0-agent")
    if isinstance(actor, str) and actor.strip():
        return actor.strip()
    return "hal0-dashboard"


@router.post("/{agent_id}/restart")
async def restart_agent(agent_id: str, request: Request) -> dict[str, str]:
    """Restart the systemd unit backing ``agent_id``.

    Returns ``{agent_id, unit, status, detail}`` with ``status="restarted"``:
    the restart verb returned success. The Hermes unit may still be coming up
    (``Type=notify``); the dashboard polls the service-status chip after this to
    converge.

    A failed restart raises :class:`Hal0Error` (HTTP 500 via the envelope
    middleware, the message carrying the failure line for the dashboard toast):

    * ``code="agent.restart_failed"`` — the verb failed (no systemctl on the
      host, a spawn failure, a non-zero exit, a refused privilege grant).
    * ``code="agent.restart_timeout"`` — the verb did not finish in time.

    Validation
    ----------
    * Unknown ``agent_id`` → 404 with ``code="agent.unknown"`` (mirrors
      :func:`hal0.api.agents.personas._resolve_agent`).

    Idempotency
    -----------
    ``systemctl restart`` is safe to call on a stopped unit (it starts
    it). The route doesn't pre-check ``is-active`` because the result
    is identical: the caller wanted the unit running and now it is (or
    failed to start, surfaced as an error).
    """
    _require_known_agent(agent_id, _KNOWN_AGENT_IDS)
    return await restart_agent_unit(agent_id, actor=_resolve_actor(request))


@router.post("/{agent_id}/gateway/restart")
async def restart_gateway(agent_id: str, request: Request) -> dict[str, str]:
    """Restart ``hermes-gateway.service``, the messaging gateway of ``agent_id``.

    Same contract as :func:`restart_agent` (including the 404 for an unknown
    agent and the ``agent.restart_failed`` / ``agent.restart_timeout`` errors),
    with ``unit="hermes-gateway.service"``. Unlike the agent restart it is also a
    404 (``agent.not_installed``) when the agent has no install record: there is
    no gateway to restart.
    """
    _require_known_agent(agent_id, _GATEWAY_AGENTS)
    require_installed(agent_id)
    return await restart_gateway_unit(agent_id, actor=_resolve_actor(request))


def _require_known_agent(agent_id: str, known: frozenset[str]) -> None:
    if agent_id not in known:
        raise NotFound(
            f"unknown agent {agent_id!r}",
            code="agent.unknown",
            details={"agent_id": agent_id},
        )


def require_installed(agent_id: str) -> None:
    """404 ``agent.not_installed`` unless the agent manager lists a record named ``agent_id``.

    The check the card itself uses (``overview._installed_record``), shared by the
    repair and gateway-restart routes. The routes module is imported here, not at
    the top: it imports this one.
    """
    from hal0.api.routes import agents as agents_routes

    if not any(rec.name == agent_id for rec in agents_routes._manager().list()):
        raise NotFound(
            f"{agent_id} is not installed",
            code="agent.not_installed",
            details={"agent_id": agent_id},
        )


async def _restart_unit(agent_id: str, unit: str) -> str:
    """Restart ``unit`` through the systemctl seam; return the success detail.

    Raises :class:`~hal0.errors.Hal0Error` when the restart did not succeed:
    ``agent.restart_timeout`` when ``unit_action`` flags ``timed_out`` (the
    outcome is unknown), ``agent.restart_failed`` for everything else.
    ``unit_action`` reports runtime failures in its result rather than
    raising, so this is the one place they become errors. The message is
    trimmed to :data:`_MAX_DETAIL_CHARS` before it reaches the envelope.
    """
    result = await unit_action(unit, "restart")
    message = str(result.get("message", ""))
    if result.get("ok"):
        return message or f"restart {unit}: ok"

    details = {"agent_id": agent_id, "unit": unit}
    detail = (message or f"restart {unit} failed")[:_MAX_DETAIL_CHARS]
    if result.get("timed_out"):
        raise Hal0Error(detail, code="agent.restart_timeout", details=details)
    raise Hal0Error(detail, code="agent.restart_failed", details=details)


async def restart_agent_unit(agent_id: str, *, actor: str) -> dict[str, str]:
    """Restart ``hal0-agent@{agent_id}.service`` through the seam and audit it.

    The body of :func:`restart_agent`, shared with routes that restart the
    agent after changing its config (``command_approvals``). Raises
    :class:`~hal0.errors.Hal0Error` on every failure; returns ``{agent_id,
    unit, status, detail}`` on success. The caller validates ``agent_id``.

    The restart budget (90s) belongs to :func:`hal0.services.systemd.unit_action`
    (its ``restart`` verb timeout), not to this module; a verb that outlives it
    surfaces as ``agent.restart_timeout``.
    """
    unit = _unit_name(agent_id)

    audit_log.info(
        "agent.restart.invoked",
        agent_id=agent_id,
        unit=unit,
        actor=actor,
    )

    try:
        detail = await _restart_unit(agent_id, unit)
    except Hal0Error as exc:
        audit_log.warning(
            "agent.restart.timeout"
            if exc.code == "agent.restart_timeout"
            else "agent.restart.failed",
            agent_id=agent_id,
            unit=unit,
            actor=actor,
            error=str(exc),
        )
        raise

    audit_log.info(
        "agent.restart.ok",
        agent_id=agent_id,
        unit=unit,
        actor=actor,
        status="restarted",
    )

    return {
        "agent_id": agent_id,
        "unit": unit,
        "status": "restarted",
        "detail": detail,
    }


async def restart_gateway_unit(agent_id: str, *, actor: str) -> dict[str, str]:
    """Restart ``hermes-gateway.service`` through the seam and audit it.

    Emits ``agent.gateway_restart.invoked`` then ``agent.gateway_restart.finished``
    (``status`` = ``restarted`` or ``error``) on the audit logger. Raises
    :class:`~hal0.errors.Hal0Error` on failure, after the ``finished`` event.
    The caller validates ``agent_id``.
    """
    audit_log.info(
        "agent.gateway_restart.invoked",
        agent_id=agent_id,
        unit=GATEWAY_UNIT,
        actor=actor,
    )

    try:
        detail = await _restart_unit(agent_id, GATEWAY_UNIT)
    except Hal0Error as exc:
        audit_log.warning(
            "agent.gateway_restart.finished",
            agent_id=agent_id,
            unit=GATEWAY_UNIT,
            actor=actor,
            status="error",
            error=str(exc),
        )
        raise

    audit_log.info(
        "agent.gateway_restart.finished",
        agent_id=agent_id,
        unit=GATEWAY_UNIT,
        actor=actor,
        status="restarted",
    )

    return {
        "agent_id": agent_id,
        "unit": GATEWAY_UNIT,
        "status": "restarted",
        "detail": detail,
    }


# Re-exported for tests so they can override the registry without poking
# at module-internals via the underscore name.
KNOWN_AGENT_IDS = _KNOWN_AGENT_IDS

__all__ = [
    "GATEWAY_UNIT",
    "KNOWN_AGENT_IDS",
    "require_installed",
    "restart_agent_unit",
    "restart_gateway_unit",
    "router",
    "unit_is_active",
]
