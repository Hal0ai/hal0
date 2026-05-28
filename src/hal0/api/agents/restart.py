"""Restart endpoint for bundled agents (v0.3 PR-11).

``POST /api/agents/{agent_id}/restart`` — wraps ``systemctl restart
hal0-agent@{agent_id}.service``. Flagged as missing during PR-6/PR-8/PR-10
integration: the sidecar agent block (SidebarAgentBlock) and the dashboard
service-status chip both want a one-click "restart this agent" action that
doesn't require dropping to a terminal.

Why a dedicated module
----------------------
The action is small (one subprocess.run) but the security posture is
non-trivial — restart is a state-mutation on a systemd unit, so it carries
the same audit obligations as the rest of the v0.3 agent surface. Keeping
it in its own module lets the test seam (``hal0.api.agents.restart``)
stay narrow and lets the audit-log invocation live alongside the
subprocess call rather than getting tangled up with the lifecycle routes.

Resolution
----------
v0.3 only resolves ``"hermes"`` (single-pick per ADR-0004). The agent
registry mirrors :mod:`hal0.api.agents.personas` — adding pi-coder in
v0.4 lights up restart automatically without touching this file.

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

log = structlog.get_logger(__name__)
audit_log = structlog.get_logger("hal0.agents.audit")
router = APIRouter()


# Mirror :data:`hal0.api.agents.personas._AGENT_PERSONAS_ROOTS` — one
# place per file that knows about agent identity, so a v0.4 pi-coder
# add-on lights up restart automatically. Stored as a frozenset to
# emphasise the "membership check only" intent.
_KNOWN_AGENT_IDS: Final[frozenset[str]] = frozenset({"hermes"})

# Timeout for the systemctl call itself. systemctl has its own deadlines
# (TimeoutStartSec/TimeoutStopSec in the unit file), but we add a wall
# clock here so a wedged systemd-bus doesn't hang the API request
# forever. 30s matches hal0-agent@.service's ``TimeoutStartSec=120``
# floor plus a small margin — long enough for a normal restart, short
# enough to fail fast on a broken host.
SYSTEMCTL_TIMEOUT_SECONDS: Final[float] = 30.0


def _systemctl_path() -> str | None:
    """Resolve ``systemctl`` on PATH.

    Returns ``None`` on hosts that don't have systemd (containers, CI
    macOS runners, the hal0-dev VM when tested via Docker). The route
    surfaces that as a 503 with a clear hint rather than a 500 trace.
    """
    return shutil.which("systemctl")


def _unit_name(agent_id: str) -> str:
    """Compose the unit name for an agent id.

    Matches ``installer/systemd/hal0-agent@.service`` — the template
    unit is parameterised by agent id so v0.4 pi-coder is a drop-in.
    """
    return f"hal0-agent@{agent_id}.service"


def _resolve_actor(request: Request) -> str:
    """Identify the caller for the audit log.

    Post-ADR-0012 there is no Bearer token store. We fall back to the
    ``X-hal0-Agent`` header (per ADR-0012 / memory ``hal0_v0.3_auth_removed``),
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

    Returns ``{status, detail}``:

    * ``status="restarted"`` — systemctl returned 0; the unit was
      stopped then restarted. Caller's next ``GET`` of the agent's
      status surface will show the new uptime.
    * ``status="restarting"`` — systemctl returned 0 BUT the unit is
      ``Type=notify`` (per the template); the start handshake is
      in-flight. The dashboard polls the service-status chip after this
      to converge.
    * ``status="error"`` — systemctl returned non-zero. ``detail``
      carries the trimmed stderr line so the dashboard can render a
      toast. The HTTP response stays 500 via :class:`Hal0Error` so the
      envelope middleware renders consistently.

    Validation
    ----------
    * Unknown ``agent_id`` → 404 with ``code="agent.unknown"`` (mirrors
      :func:`hal0.api.agents.personas._resolve_agent`).
    * Missing ``systemctl`` on host → 503 (Hal0Error) with
      ``code="agent.systemctl_unavailable"``.
    * Timeout → 504-equivalent Hal0Error with
      ``code="agent.restart_timeout"``.

    Idempotency
    -----------
    ``systemctl restart`` is safe to call on a stopped unit (it starts
    it). The route doesn't pre-check ``is-active`` because the result
    is identical: the caller wanted the unit running and now it is (or
    failed to start, surfaced as ``error``).
    """
    if agent_id not in _KNOWN_AGENT_IDS:
        raise NotFound(
            f"unknown agent {agent_id!r}",
            code="agent.unknown",
            details={"agent_id": agent_id},
        )

    systemctl = _systemctl_path()
    if systemctl is None:
        # 503-equivalent. Surfaces as a clear "host doesn't have systemd"
        # rather than a generic 500 trace.
        raise Hal0Error(
            "systemctl not available on this host; cannot restart agent",
            code="agent.systemctl_unavailable",
            details={"agent_id": agent_id},
        )

    unit = _unit_name(agent_id)
    actor = _resolve_actor(request)

    audit_log.info(
        "agent.restart.invoked",
        agent_id=agent_id,
        unit=unit,
        actor=actor,
    )

    cmd = [systemctl, "restart", unit]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, FileNotFoundError) as exc:
        audit_log.warning(
            "agent.restart.spawn_failed",
            agent_id=agent_id,
            unit=unit,
            actor=actor,
            error=str(exc),
        )
        raise Hal0Error(
            f"failed to spawn systemctl: {exc}",
            code="agent.restart_failed",
            details={"agent_id": agent_id, "unit": unit},
        ) from exc

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=SYSTEMCTL_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        audit_log.warning(
            "agent.restart.timeout",
            agent_id=agent_id,
            unit=unit,
            actor=actor,
            timeout_s=SYSTEMCTL_TIMEOUT_SECONDS,
        )
        raise Hal0Error(
            f"systemctl restart {unit} timed out after {SYSTEMCTL_TIMEOUT_SECONDS}s",
            code="agent.restart_timeout",
            details={"agent_id": agent_id, "unit": unit},
        ) from exc

    rc = proc.returncode or 0
    stderr_text = (stderr_bytes or b"").decode("utf-8", errors="replace").strip()
    stdout_text = (stdout_bytes or b"").decode("utf-8", errors="replace").strip()

    if rc != 0:
        audit_log.warning(
            "agent.restart.failed",
            agent_id=agent_id,
            unit=unit,
            actor=actor,
            returncode=rc,
            stderr=stderr_text,
        )
        # Use a Hal0Error rather than letting the caller see raw stderr —
        # the envelope middleware renders this as a 500 with the stable
        # ``code`` set. Trim stderr to the first 200 chars to keep the
        # response bounded.
        raise Hal0Error(
            f"systemctl restart {unit} failed (rc={rc}): {stderr_text[:200] or '<no stderr>'}",
            code="agent.restart_failed",
            details={
                "agent_id": agent_id,
                "unit": unit,
                "returncode": rc,
                "stderr": stderr_text[:200],
            },
        )

    # systemctl restart returns 0 immediately for Type=notify units once
    # the ExecStart command has been spawned; the unit may still be in
    # ``activating`` state. The dashboard polls the chip after this so
    # we don't need to block waiting for ``active``. Report
    # "restarting" when stdout/stderr suggest the activation is still
    # in flight; otherwise "restarted".
    status = "restarted"
    detail = stdout_text or f"systemctl restart {unit} returned 0"
    if "activating" in stderr_text.lower() or "queued" in stderr_text.lower():
        status = "restarting"
        detail = stderr_text

    audit_log.info(
        "agent.restart.ok",
        agent_id=agent_id,
        unit=unit,
        actor=actor,
        status=status,
    )

    return {
        "agent_id": agent_id,
        "unit": unit,
        "status": status,
        "detail": detail,
    }


# Re-exported for tests so they can override the registry without poking
# at module-internals via the underscore name.
KNOWN_AGENT_IDS = _KNOWN_AGENT_IDS

__all__ = ["KNOWN_AGENT_IDS", "router"]
