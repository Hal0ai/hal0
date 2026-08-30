"""Converge the OpenWebUI companion image onto the release pin (spec §2).

Pull FIRST, repin second, restart last — a failed pull never strands the
unit on an unpullable digest (the order `hal0 update owui` proved). On a
provisioned box (process runs as the hal0 service user) the unit rewrite
+ daemon-reload route through `sudo -n hal0-systemctl repin-owui` and the
image pull through `sudo -n hal0-podman-rw image-pull` (rootful store —
the one the unit launches from); dev/test processes rewrite the unit
directly and pull with bare podman. Never raises operationally — status
dicts, the ``upgrade_memory_engine`` posture.

Operator override: ``--target`` persists the digest to
``/var/lib/hal0/state/openwebui.pin-override``; while present, converge
holds the box at the override (status ``override``) instead of the
release pin. ``clear_override`` removes it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Callable

import structlog

from hal0.config.paths import var_lib
from hal0.openwebui import image_pin
from hal0.system.seam import SEAM_BIN, is_hal0_service_user

log = structlog.get_logger(__name__)

_PODMAN_RW = "/usr/lib/hal0/bin/hal0-podman-rw"
_PULL_TIMEOUT_S = 600.0
_CTL_TIMEOUT_S = 120.0

Runner = Callable[..., subprocess.CompletedProcess]


def override_path() -> Path:
    return var_lib() / "state" / "openwebui.pin-override"


def read_pin_override() -> str | None:
    try:
        value = override_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value if image_pin.is_sha256_digest(value) else None


def _write_unit_atomic(unit: Path, text: str) -> None:
    mode = unit.stat().st_mode & 0o777
    tmp = unit.with_name(f".{unit.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, unit)


def converge_openwebui(
    *,
    job_id: str | None = None,
    apply: bool = True,
    target_digest: str | None = None,
    clear_override: bool = False,
    runner: Runner | None = None,
    is_hal0_user: Callable[[], bool] | None = None,
    unit_path: Path | None = None,
) -> dict[str, Any]:
    runner = runner if runner is not None else subprocess.run
    is_hal0_user = is_hal0_user if is_hal0_user is not None else is_hal0_service_user
    unit = unit_path if unit_path is not None else image_pin.installed_unit_path()

    if not unit.is_file():
        return {"status": "skipped", "reason": "openwebui unit not installed"}
    try:
        text = unit.read_text(encoding="utf-8")
    except OSError as exc:
        return {"status": "build_failed", "error": f"unit unreadable: {exc}"}
    current = image_pin.parse_pinned_digest(text)
    if current is None:
        return {
            "status": "build_failed",
            "error": "no consistent pinned digest in unit",
            "remedy": f"inspect {unit} — pins missing or disagree",
        }

    if clear_override:
        try:
            override_path().unlink(missing_ok=True)
        except OSError as exc:
            log.warning("components.owui_override_clear_failed", job_id=job_id, error=str(exc))

    override = None if clear_override else read_pin_override()
    overridden = False
    if target_digest is not None:
        desired = target_digest
    elif override is not None:
        desired, overridden = override, True
    else:
        desired = image_pin.OPENWEBUI_IMAGE_PIN

    result: dict[str, Any] = {"installed": current, "pinned": desired}
    if current == desired:
        return {**result, "status": "override" if overridden else "converged"}
    if not apply:
        log.warning(
            "components.owui_stale", job_id=job_id, installed=current, pinned=desired,
            remedy="run 'hal0 update' or 'hal0 update owui'",
        )
        return {**result, "status": "override" if overridden else "stale"}

    # ── Pull first ──
    ref = image_pin.pinned_ref(desired)
    pull_argv = (
        ["sudo", "-n", _PODMAN_RW, "image-pull", ref]
        if is_hal0_user()
        else ["podman", "pull", ref]
    )
    try:
        proc = runner(pull_argv, capture_output=True, text=True, timeout=_PULL_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        return {**result, "status": "build_failed", "error": f"pull failed: {exc}"}
    if proc.returncode != 0:
        return {
            **result,
            "status": "build_failed",
            "error": f"pull failed: {(proc.stderr or '').strip() or f'exit {proc.returncode}'}",
            "remedy": "unit unchanged; fix network/registry access and retry",
        }

    # ── Repin ──
    if is_hal0_user():
        try:
            proc = runner(
                ["sudo", "-n", SEAM_BIN, "repin-owui", desired],
                capture_output=True, text=True, timeout=_CTL_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {**result, "status": "build_failed", "error": f"repin failed: {exc}"}
        if proc.returncode != 0:
            return {
                **result, "status": "build_failed",
                "error": f"repin failed: {(proc.stderr or '').strip() or f'exit {proc.returncode}'}",
            }
    else:
        new_text, count = image_pin.repin_unit_text(text, desired)
        if count == 0:
            return {**result, "status": "build_failed", "error": "no pin occurrences rewritten"}
        try:
            _write_unit_atomic(unit, new_text)
        except OSError as exc:
            return {**result, "status": "build_failed", "error": f"unit write failed: {exc}"}
        runner(["systemctl", "daemon-reload"], capture_output=True, text=True, timeout=30.0)

    # ── Persist / restart ──
    if target_digest is not None:
        try:
            override_path().parent.mkdir(parents=True, exist_ok=True)
            override_path().write_text(target_digest + "\n", encoding="utf-8")
        except OSError as exc:
            log.warning("components.owui_override_persist_failed", job_id=job_id, error=str(exc))

    restart_argv = (
        ["sudo", "-n", SEAM_BIN, "svc-restart", "openwebui"]
        if is_hal0_user()
        else ["systemctl", "restart", image_pin.OPENWEBUI_UNIT_NAME]
    )
    try:
        proc = runner(restart_argv, capture_output=True, text=True, timeout=_CTL_TIMEOUT_S)
        restarted = proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        restarted = False
    if not restarted:
        log.warning(
            "components.owui_restart_failed", job_id=job_id,
            remedy=f"repinned; run 'systemctl restart {image_pin.OPENWEBUI_UNIT_NAME}' and check its journal",
        )
    log.info("components.owui_repinned", job_id=job_id, from_=current, to=desired)
    return {**result, "status": "upgraded", "from": current, "to": desired, "restarted": restarted}
