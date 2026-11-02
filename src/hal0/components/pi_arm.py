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


def _version_label(pi: str | None, adapter: str | None) -> str:
    return f"{pi or 'none'} (adapter {adapter or 'none'})"


def pinned_pi() -> str:
    """``<pi> (adapter <adapter>)`` from the installer script; raises when unreadable."""
    from hal0.agents.manager import installer_script_path
    from hal0.agents.overview import pi_pins

    pins = pi_pins(installer_script_path("pi"))
    return _version_label(pins["pi"], pins["adapter"])


def installed_pi() -> str | None:
    """The installed versions in :func:`pinned_pi`'s shape, or ``None`` when not installed.

    Pi counts as installed when the driver's profile marker exists and ``pi``
    is an npm install on PATH, the same two facts the daemon's Agents card reads.
    """
    from hal0.agents.overview import pi_installed_versions
    from hal0.agents.pi_coder import PiDriver

    if not PiDriver()._profile_marker_path().exists():
        return None
    binary = shutil.which("pi")
    versions = pi_installed_versions(Path(binary) if binary else None)
    if versions is None:
        return None
    return _version_label(versions.get("pi"), versions.get("adapter"))


def converge_pi(*, job_id: str | None = None, apply: bool = True) -> dict[str, Any]:
    """Status dict for Pi; read-only whatever ``apply`` says (see the module docstring)."""
    installed = installed_pi()
    if installed is None:
        return {"status": "skipped", "reason": "pi not installed"}
    try:
        pinned = pinned_pi()
    except (OSError, ValueError) as exc:
        return {"status": "skipped", "installed": installed, "reason": str(exc)}
    if installed == pinned:
        return {"status": "converged", "installed": installed, "pinned": pinned}
    return {
        "status": "stale",
        "installed": installed,
        "pinned": pinned,
        "remedy": f"run '{UPDATE_COMMAND}'",
    }
