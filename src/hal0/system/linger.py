"""systemd linger for the ``hal0`` user (ADR-0034 decision 6).

From Hermes v0.22, ``hermes gateway install --system --run-as-user hal0``
enables linger for ``hal0`` (hermes ``hermes_cli/gateway.py:3476-3530`` at
``d923a5eb``) and orders the gateway after ``user@<uid>.service``
(``gateway.py:3292-3295``). Hermes's restart-safe cron and kanban workers run
through ``systemd-run --user``, which needs that user manager; kanban always
requires it (``hermes_cli/config_defaults.py:1872-1874``). hal0 accepts the
linger, shows it in ``hal0 doctor all``, and removes it on uninstall.

The read is non-mutating: ``loginctl show-user <user> -p Linger --value``,
falling back to logind's own record under ``/var/lib/systemd/linger`` when
``loginctl`` cannot answer (no logind, or a user that is neither logged in nor
lingering, which ``show-user`` reports as an error).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The service user whose linger the Hermes gateway enables.
HAL0_USER = "hal0"

#: Where logind records lingering users (``loginctl enable-linger <user>``).
LINGER_DIR = "/var/lib/systemd/linger"


@dataclass(frozen=True)
class LingerState:
    """Whether ``user`` lingers, and how that was determined."""

    user: str
    enabled: bool | None
    source: str
    detail: str = ""


def read_linger(
    user: str = HAL0_USER,
    *,
    run: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    linger_dir: str = LINGER_DIR,
) -> LingerState:
    """Return the linger state of ``user`` without changing anything.

    ``enabled`` is ``None`` only when neither ``loginctl`` nor the linger
    directory can say (no systemd at all).
    """
    detail = ""
    if which("loginctl"):
        try:
            proc = run(  # nosec B603 B607 — fixed argv, read-only query
                ["loginctl", "show-user", user, "-p", "Linger", "--value"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            detail = str(exc)
        else:
            value = (proc.stdout or "").strip().lower()
            if proc.returncode == 0 and value in {"yes", "no"}:
                return LingerState(user, value == "yes", "loginctl")
            detail = (proc.stderr or proc.stdout or "").strip()
    if os.path.isdir(linger_dir):
        return LingerState(
            user, os.path.exists(os.path.join(linger_dir, user)), "linger-dir", detail
        )
    return LingerState(user, None, "unavailable", detail or "no loginctl and no linger dir")


def disable_linger(
    user: str = HAL0_USER,
    *,
    run: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[bool, str]:
    """``loginctl disable-linger <user>``; returns ``(ok, message)``.

    Needs root. Never raises: uninstall must keep going when it fails.
    """
    if not which("loginctl"):
        return (False, "loginctl not found")
    try:
        proc = run(  # nosec B603 B607 — fixed argv
            ["loginctl", "disable-linger", user],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return (False, str(exc))
    if proc.returncode != 0:
        return (False, (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}")
    return (True, f"linger disabled for {user}")


#: The Hermes gateway's system unit, written by ``hermes gateway install``.
HERMES_GATEWAY_UNIT = "/etc/systemd/system/hermes-gateway.service"

#: What an operator runs to regenerate the gateway unit and turn linger on.
GATEWAY_REGEN_COMMAND = "sudo hal0 agent install hermes"


def gateway_in_use(
    *,
    unit_dir: str = "/etc/systemd/system",
    run: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> bool:
    """Is ``hermes-gateway.service`` enabled or running?

    Enablement is read from the ``*.wants/hermes-gateway.service`` symlinks
    ``systemctl enable`` lays under ``unit_dir`` (no subprocess). Only when
    there is none does it ask ``systemctl is-active``, which needs no
    privilege, to catch a gateway the operator disabled but left running. A
    disabled, stopped gateway (the test boxes) never runs, so its unit shape
    and linger do not matter.
    """
    base = Path(unit_dir)
    if any(base.glob("*.wants/hermes-gateway.service")):
        return True
    if not which("systemctl"):
        return False
    try:
        proc = run(  # nosec B603 B607 — fixed argv, read-only query
            ["systemctl", "is-active", "--quiet", "hermes-gateway.service"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def gateway_unit_regen_reason(
    *,
    unit: str = HERMES_GATEWAY_UNIT,
    linger_dir: str = LINGER_DIR,
    user: str = HAL0_USER,
    in_use: Callable[[], bool] | None = None,
) -> str | None:
    """Why the installed gateway unit needs ``sudo hal0 agent install hermes``, or ``None``.

    Read-only and unprivileged, so ``hal0 update`` can call it as any user
    (its only subprocess is :func:`gateway_in_use`'s ``systemctl is-active``,
    and only when the gateway is not enabled). A box that ran Hermes before
    1.6.0 keeps the unit Hermes 0.19.0 wrote until ``hal0 update`` heals it
    through ``hal0-update hermes-gateway`` (:mod:`hal0.agents.hermes_gateway_regen`): no
    ``user@<uid>.service`` ordering and no linger. Under v0.22.2 that unit
    starts, but kanban workers cannot get their restart-safe scope and never
    start, and cron jobs run unscoped (hermes ``tools/process_registry.py:415-462``
    at ``d923a5eb``). A unit upstream rendered itself (``hermes gateway
    migrate``) runs its release-local ``src/.hermes/bin/hermes`` shim, which
    fails on the next release (#2894); any unit whose ``Exec*`` lines are not
    hal0's launcher is stale too (``hermes_gateway_regen.unit_stale_reason``).
    ``None`` when there is no gateway unit, or when the
    gateway is neither enabled nor running (:func:`gateway_in_use`).
    """
    try:
        text = Path(unit).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"the hermes-gateway unit could not be read ({exc})"
    if not (in_use or gateway_in_use)():
        return None
    from hal0.agents.hermes_gateway_regen import unit_stale_reason

    stale = unit_stale_reason(text)
    if stale is not None:
        return stale
    if os.path.isdir(linger_dir) and not os.path.exists(os.path.join(linger_dir, user)):
        return f"systemd linger is off for {user}"
    return None


__all__ = [
    "GATEWAY_REGEN_COMMAND",
    "HAL0_USER",
    "HERMES_GATEWAY_UNIT",
    "LINGER_DIR",
    "LingerState",
    "disable_linger",
    "gateway_in_use",
    "gateway_unit_regen_reason",
    "read_linger",
]
