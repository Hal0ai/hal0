"""Shared systemctl helpers for the companion-service management layer.

Until now every route reimplemented its own ``systemctl is-active``
subprocess call (comfyui.py, config.py, installer.py, agents/restart.py …).
This module is the single implementation the services surface uses:

* :func:`unit_state`  — rich state via ``systemctl show`` (active/sub/
  unit-file state + ActiveEnterTimestamp for uptime display).
* :func:`unit_is_active` — cheap boolean probe.
* :func:`unit_action` — run one allow-listed lifecycle verb.

Everything is fail-soft: a host without systemd (CI, dev laptop) yields
"unknown" states and honest error strings, never an exception. hal0-api
runs as root, so systemctl is invoked directly — same assumption as
``installer._privileged_systemctl_argv`` (the sudoers seam was removed).

The verb allow-list is enforced HERE, at the execution boundary, in
addition to the per-service ``ServiceDef.actions`` check in the route —
a crafted request can never reach an arbitrary systemctl verb.
"""

from __future__ import annotations

import asyncio
import re
import shutil

import structlog

log = structlog.get_logger(__name__)

#: Verbs :func:`unit_action` will ever execute.
ALLOWED_VERBS = frozenset({"start", "stop", "restart", "enable", "disable"})

#: Per-verb subprocess timeouts (seconds). restart can pull a container
#: image layer on ExecStartPre, so it gets headroom.
_VERB_TIMEOUT = {
    "start": 60.0,
    "stop": 30.0,
    "restart": 90.0,
    "enable": 10.0,
    "disable": 10.0,
}

_SHOW_TIMEOUT = 3.0

# systemd unit names: letters, digits, '@-_.:\' — conservative (mirrors
# api/routes/logs.py _validate_unit) so a unit string is always safe to
# hand to a subprocess argv.
_UNIT_RE = re.compile(r"^[A-Za-z0-9@_\-.:]+$")


def valid_unit(unit: str) -> bool:
    """True when ``unit`` is a plausible systemd unit name."""
    return bool(unit) and bool(_UNIT_RE.match(unit))


async def _run(*args: str, timeout: float) -> tuple[int | None, str, str]:
    """Run ``systemctl <args>``; (rc, stdout, stderr), rc=None on no-systemd/timeout."""
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        return None, "", "systemctl not available on this host"
    try:
        proc = await asyncio.create_subprocess_exec(
            systemctl,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return None, "", f"systemctl spawn failed: {exc}"
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        return None, "", f"systemctl {' '.join(args)} timed out after {timeout:.0f}s"
    return (
        proc.returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def unit_state(unit: str) -> dict[str, str | None]:
    """Return the unit's systemd state for display.

    Shape (all values fail-soft)::

        {
          "active_state":    "active" | "inactive" | "failed" | ... | "unknown",
          "sub_state":       "running" | "dead" | ... | "unknown",
          "unit_file_state": "enabled" | "disabled" | ... | "unknown",
          "since":           "Fri 2026-07-03 09:12:01 UTC" | None,
        }
    """
    fallback: dict[str, str | None] = {
        "active_state": "unknown",
        "sub_state": "unknown",
        "unit_file_state": "unknown",
        "since": None,
    }
    if not valid_unit(unit):
        return fallback
    rc, stdout, _stderr = await _run(
        "show",
        unit,
        "--property=ActiveState,SubState,UnitFileState,ActiveEnterTimestamp",
        timeout=_SHOW_TIMEOUT,
    )
    if rc is None:
        return fallback
    props: dict[str, str] = {}
    for line in stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            props[key] = value.strip()
    # `systemctl show` exits 0 even for unloaded units (ActiveState=inactive,
    # empty UnitFileState) — surface what it said, defaulting honestly.
    since = props.get("ActiveEnterTimestamp", "")
    return {
        "active_state": props.get("ActiveState") or "unknown",
        "sub_state": props.get("SubState") or "unknown",
        "unit_file_state": props.get("UnitFileState") or "unknown",
        "since": since or None,
    }


async def timer_schedule(unit: str) -> dict[str, str | None]:
    """Return a ``.timer`` unit's calendar expression + last/next trigger.

    Shape (fail-soft — a host without systemd or an unknown unit yields all
    ``None``, never an exception)::

        {
          "calendar":     "hourly" | "*-*-* *:00:00" | None,  # OnCalendar= expression
          "last_trigger": "Sat 2026-07-11 21:00:00 EDT" | None,  # raw systemd string
          "next_elapse":  "Sat 2026-07-11 22:00:00 EDT" | None,  # raw systemd string
        }

    Timestamps are passed through verbatim (not ISO-normalised) — matches
    :func:`unit_state`'s ``since`` field, which does the same.
    """
    fallback: dict[str, str | None] = {"calendar": None, "last_trigger": None, "next_elapse": None}
    if not valid_unit(unit):
        return fallback
    rc, stdout, _stderr = await _run(
        "show",
        unit,
        "--property=TimersCalendar,LastTriggerUSec,NextElapseUSecRealtime",
        timeout=_SHOW_TIMEOUT,
    )
    if rc is None:
        return fallback
    props: dict[str, str] = {}
    for line in stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            props[key] = value.strip()

    calendar = None
    match = re.search(r"OnCalendar=(\S+)", props.get("TimersCalendar", ""))
    if match:
        calendar = match.group(1)

    def _clean(value: str | None) -> str | None:
        if not value or value in ("n/a", "0"):
            return None
        return value

    return {
        "calendar": calendar,
        "last_trigger": _clean(props.get("LastTriggerUSec")),
        "next_elapse": _clean(props.get("NextElapseUSecRealtime")),
    }


async def unit_is_active(unit: str) -> bool:
    """True when ``systemctl is-active <unit>`` reports ``active``."""
    if not valid_unit(unit):
        return False
    rc, stdout, _ = await _run("is-active", unit, timeout=_SHOW_TIMEOUT)
    return rc == 0 and stdout.strip() == "active"


async def unit_action(unit: str, verb: str) -> dict[str, object]:
    """Run one allow-listed lifecycle verb against ``unit``.

    Returns ``{"ok": bool, "message": str}`` — never raises for runtime
    failures (missing systemd, unit errors); raises ``ValueError`` only for
    programming errors (verb outside :data:`ALLOWED_VERBS`, bad unit name),
    which the route layer maps to a 400.
    """
    if verb not in ALLOWED_VERBS:
        raise ValueError(f"verb {verb!r} is not an allowed systemctl verb")
    if not valid_unit(unit):
        raise ValueError(f"invalid unit name {unit!r}")
    rc, _stdout, stderr = await _run(verb, unit, timeout=_VERB_TIMEOUT[verb])
    if rc == 0:
        return {"ok": True, "message": f"{verb} {unit}: ok"}
    detail = stderr.strip() or f"exit code {rc}"
    log.warning("services.unit_action_failed", unit=unit, verb=verb, detail=detail)
    return {"ok": False, "message": f"{verb} {unit} failed: {detail}"}


__all__ = [
    "ALLOWED_VERBS",
    "timer_schedule",
    "unit_action",
    "unit_is_active",
    "unit_state",
    "valid_unit",
]
