"""Report the bundled Pi install against the versions ``installer/agents/pi.sh`` pins.

Pi differs from every other component: it lives in the global npm prefix and
in the ``~/.pi`` of the user who installed it, which the ``hal0`` service user
can neither write nor read. So this arm never changes anything. It runs in the
daemon and in the post-update pass, and only says whether Pi is on its pin. The
re-pin itself is ``sudo hal0 update component pi``, which runs
``PiDriver.upgrade`` in the CLI process as root.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

#: The one command that re-pins Pi.
UPDATE_COMMAND = "sudo hal0 update component pi"


def _version_label(versions: dict[str, str | None]) -> str:
    return f"{versions.get('pi') or 'none'} (adapter {versions.get('adapter') or 'none'})"


def _pins() -> dict[str, str]:
    from hal0.agents.manager import installer_script_path
    from hal0.agents.overview import pi_pins

    return pi_pins(installer_script_path("pi"))


def _installed_versions() -> dict[str, str | None] | None:
    """Pi and adapter versions, or ``None`` when Pi is not installed.

    Pi counts as installed when the driver's profile marker exists and ``pi``
    is an npm install on PATH, the same two facts the daemon's Agents card reads.
    """
    from hal0.agents.overview import pi_installed_versions
    from hal0.agents.pi_coder import PiDriver

    if not PiDriver()._profile_marker_path().exists():
        return None
    binary = shutil.which("pi")
    return pi_installed_versions(Path(binary) if binary else None)


def pinned_pi() -> str:
    """``<pi> (adapter <adapter>)`` from the installer script; raises when unreadable."""
    return _version_label(_pins())


def installed_pi() -> str | None:
    """The installed versions in :func:`pinned_pi`'s shape, or ``None`` when not installed."""
    versions = _installed_versions()
    return _version_label(versions) if versions is not None else None


def pin_state() -> tuple[str, str | None]:
    """``(status, note)`` of Pi against its pin.

    Advisory: none of these statuses counts as pending. A pending row exits
    `hal0 update` with 2 because that pass should have cleared it, and nothing
    in the pass can re-pin Pi, so a pin bump would otherwise fail every
    successful update. A Pi newer than the pin (``pi update`` exists) gets no
    command to run, because the command would downgrade it.
    """
    from hal0.agents.overview import pi_update_available

    versions = _installed_versions()
    if versions is None:
        return "not-installed", None
    try:
        pins = _pins()
    except (OSError, ValueError) as exc:
        return "unknown", f"cannot read pi's pin: {exc}"
    if pi_update_available(versions, pins):
        return "update-available", f"behind its pin; run '{UPDATE_COMMAND}' to update it"
    if versions == pins:
        return "converged", None
    return "newer-than-pin", "newer than the version this hal0 release pins; nothing to do"


def converge_pi(*, job_id: str | None = None, apply: bool = True) -> dict[str, Any]:
    """Status dict for Pi; read-only whatever ``apply`` says (see the module docstring)."""
    status, note = pin_state()
    if status in ("not-installed", "unknown"):
        return {"status": "skipped", "reason": note or "pi not installed"}
    return {"status": "stale" if status == "update-available" else status}
