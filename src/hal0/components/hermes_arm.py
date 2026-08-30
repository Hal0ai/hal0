"""Converge the bundled Hermes venv onto the requirements.txt pin (spec §1).

Reuses the provisioner's own install + probe seams exactly as
``updater.repair_hermes_mcp_client`` does (that pass heals import-broken
venvs; this arm heals VERSION drift — both land on the state a fresh
provision produces). The build identity is a stamp file, not a dist-probe:
the pin may be a git ref, which no importlib.metadata probe can confirm.
No snapshot machinery — hermes has no one-way DB; HERMES_HOME untouched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

import structlog

from hal0.config.paths import var_lib
from hal0.system.seam import SEAM_BIN, is_hal0_service_user

log = structlog.get_logger(__name__)

_CTL_TIMEOUT_S = 120.0

Runner = Callable[..., subprocess.CompletedProcess]


def stamp_path() -> Path:
    return var_lib() / "state" / "agents" / "hermes" / "venv.pin"


def installed_hermes_pin(venv: Path | None = None) -> str | None:
    """The pin identifier stamped after the last successful converge.

    ``venv`` is accepted for interface symmetry with the other component
    getters but does not gate the read: the stamp is a single canonical
    file (one hermes install per box, spec §1), not venv-relative, and
    "is hermes provisioned at all" is already ``converge_hermes``'s own
    concern via its own ``bin/python`` check. Gating this getter on
    ``hermes.HERMES_VENV_DEFAULT`` (a hardcoded FHS path, not HAL0_HOME-aware
    like :func:`stamp_path`) would make it blind to the stamp under any
    HAL0_HOME sandbox — the dev/test posture this whole module is written
    to run under.
    """
    del venv
    try:
        value = stamp_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def converge_hermes(
    *,
    job_id: str | None = None,
    apply: bool = True,
    runner: Runner | None = None,
    venv: Path | None = None,
    is_hal0_user: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    from hal0.agents import hermes_provision as hermes

    runner = runner if runner is not None else subprocess.run
    is_hal0_user = is_hal0_user if is_hal0_user is not None else is_hal0_service_user
    venv = venv or hermes.HERMES_VENV_DEFAULT

    if not (venv / "bin" / "python").exists():
        return {"status": "skipped", "reason": "hermes not provisioned"}

    pinned = hermes._hermes_version_pin()
    installed = installed_hermes_pin(venv)
    result: dict[str, Any] = {"installed": installed, "pinned": pinned}
    if installed == pinned:
        return {**result, "status": "converged"}
    if not apply:
        log.warning(
            "components.hermes_stale", job_id=job_id, installed=installed, pinned=pinned,
            remedy="run 'hal0 update'",
        )
        return {**result, "status": "stale"}

    try:
        hermes._install_venv(venv, hermes.HERMES_REQUIREMENTS)
    except Exception as exc:
        return {**result, "status": "build_failed", "error": f"venv install failed: {exc}"}

    try:
        probe = hermes._probe_mcp_client(venv / "bin" / "python", None, agent_id="hermes")
    except Exception as exc:
        return {**result, "status": "build_failed", "error": f"post-install probe failed: {exc}"}
    if not probe.get("ok"):
        return {
            **result, "status": "build_failed",
            "error": f"rebuilt venv fails its own probe: {probe.get('error')}",
            "remedy": "run 'sudo hal0 agent provision hermes --repair'",
        }

    try:
        stamp_path().parent.mkdir(parents=True, exist_ok=True)
        stamp_path().write_text(pinned + "\n", encoding="utf-8")
    except OSError as exc:
        log.warning("components.hermes_stamp_failed", job_id=job_id, error=str(exc))

    # Bounce the agent unit (and its gateway) so the running process picks
    # up the rebuilt venv — same reason repair_hermes_mcp_client bounces
    # hermes-gateway. Best-effort.
    for argv in (
        (["sudo", "-n", SEAM_BIN, "try-restart-agent", "hermes"]
         if is_hal0_user() else ["systemctl", "try-restart", "hal0-agent@hermes.service"]),
        (["sudo", "-n", SEAM_BIN, "svc-restart", "hermes-gateway"]
         if is_hal0_user() else ["systemctl", "try-restart", "hermes-gateway.service"]),
    ):
        try:
            runner(argv, capture_output=True, text=True, timeout=_CTL_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("components.hermes_restart_failed", job_id=job_id, argv=argv[0], error=str(exc))

    log.info("components.hermes_upgraded", job_id=job_id, installed=installed, pinned=pinned)
    return {**result, "status": "upgraded", "from": installed, "to": pinned}
