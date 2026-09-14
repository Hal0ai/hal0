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
release pin. ``clear_override`` removes it. The marker is written
whenever ``target_digest`` is supplied — including when it already
matches the installed digest, so a subsequent un-targeted converge still
holds the box at the operator's pin instead of drifting back to the
release pin the moment nothing needs pulling.

Env re-render (RAG/image-gen/web-search full wiring): every ``apply``
pass of :func:`converge_openwebui` also re-renders ``openwebui.env``'s
dynamic blocks from live capability state
(:func:`hal0.openwebui.wiring.resolve_dynamic_env_overrides`, via
:func:`_render_dynamic_env`) and restarts the unit when the rendered bytes
changed. That covers ``hal0 update``/``hal0 app converge`` and the
boot-time convergence pass (:mod:`hal0.components.runner`), which are the
only existing callers of this arm — capability apply
(:mod:`hal0.api.routes.capabilities`) and slot create/delete for the
``embed``/``img`` slots (:mod:`hal0.api.routes.slots`) call
:func:`reconcile_openwebui_env` directly instead, since neither goes
through component convergence today. A ``diagnose_only`` pass
(``apply=False``, e.g. the boot-time drift check) never renders or
restarts — matching the read-only contract every other branch of this
function already gives that flag.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

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


def _restart_openwebui(runner: Runner, is_hal0_user: Callable[[], bool]) -> bool:
    """Restart the OpenWebUI unit through the same seam every other branch
    of this module uses. Returns whether the restart command itself
    succeeded (a process failure never raises — callers treat this as a
    best-effort signal, same posture as the rest of the arm)."""
    restart_argv = (
        ["sudo", "-n", SEAM_BIN, "svc-restart", "openwebui"]
        if is_hal0_user()
        else ["systemctl", "restart", image_pin.OPENWEBUI_UNIT_NAME]
    )
    try:
        proc = runner(restart_argv, capture_output=True, text=True, timeout=_CTL_TIMEOUT_S)
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _render_dynamic_env(job_id: str | None) -> tuple[bool, str | None]:
    """Re-render ``openwebui.env``'s dynamic RAG/image-gen/web-search blocks
    from live capability state. Returns ``(env_changed, error)`` — never
    raises; a broken resolver degrades to ``(False, <error>)`` so it can
    never break whichever caller is piggybacking this render on top of
    (image-pin convergence, a capability apply, a slot delete).
    """
    from hal0.config.paths import openwebui_env
    from hal0.openwebui.env_writer import write_openwebui_env
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides

    target = openwebui_env()
    try:
        before = target.read_bytes() if target.exists() else None
    except OSError as exc:
        return False, f"env read failed: {exc}"

    try:
        overrides = resolve_dynamic_env_overrides()
        write_openwebui_env(target, overrides=overrides, preserve_existing=True)
    except Exception as exc:
        log.warning("components.owui_env_render_failed", job_id=job_id, error=str(exc))
        return False, f"env render failed: {exc}"

    try:
        after = target.read_bytes()
    except OSError as exc:
        return False, f"env read-back failed: {exc}"

    return before != after, None


def reconcile_openwebui_env(
    *,
    job_id: str | None = None,
    runner: Runner | None = None,
    is_hal0_user: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Re-render the dynamic env blocks and restart the unit only when the
    rendered bytes actually changed.

    The single reconcile point every event that changes that truth calls
    directly: capability apply (:mod:`hal0.api.routes.capabilities`), slot
    create/delete for the ``embed``/``img`` slots
    (:mod:`hal0.api.routes.slots`). :func:`converge_openwebui` calls
    :func:`_render_dynamic_env` itself instead (see its own restart, which
    this would otherwise race/duplicate on an image-pin change).
    """
    runner = runner if runner is not None else subprocess.run
    is_hal0_user = is_hal0_user if is_hal0_user is not None else is_hal0_service_user

    if not image_pin.installed_unit_path().is_file():
        # Mirrors converge_openwebui's own early-exit: a box that never
        # installed the OpenWebUI companion (HAL0_SKIP_OPENWEBUI=1) has
        # nothing to restart, and writing an env file for a companion that
        # isn't provisioned would be a surprising side effect of a
        # capability apply / slot create/delete on an unrelated feature.
        return {"status": "skipped", "reason": "openwebui unit not installed"}

    changed, error = _render_dynamic_env(job_id)
    if error is not None:
        return {"status": "build_failed", "error": error}
    if not changed:
        return {"status": "unchanged", "env_changed": False}

    restarted = _restart_openwebui(runner, is_hal0_user)
    if not restarted:
        log.warning(
            "components.owui_env_restart_failed",
            job_id=job_id,
            remedy=f"env rewritten; run 'systemctl restart {image_pin.OPENWEBUI_UNIT_NAME}' by hand",
        )
    log.info("components.owui_env_reconciled", job_id=job_id, restarted=restarted)
    return {"status": "converged", "env_changed": True, "restarted": restarted}


def reconcile_openwebui_env_background() -> None:
    """``reconcile_openwebui_env()``, fire-and-forget.

    The one shared body for every caller that triggers a reconcile as a
    side effect of its own unrelated response — capability apply
    (:mod:`hal0.api.routes.capabilities`) and slot create/delete for the
    ``embed``/``img`` slots (:mod:`hal0.api.routes.slots`) both pass this
    straight to ``BackgroundTasks.add_task``. Never raises: a broken
    resolver or a dead unit is this function's own problem (logged), never
    a failure on the response it's piggybacking on.
    """
    try:
        reconcile_openwebui_env()
    except Exception as exc:  # pragma: no cover — defensive, arm is fail-soft
        log.warning("components.owui_reconcile_background_failed", error=str(exc))


def _env_reconcile_fields(
    job_id: str | None, runner: Runner, is_hal0_user: Callable[[], bool]
) -> dict[str, Any]:
    """Render the dynamic env blocks and restart-if-changed, as extra fields
    for ``converge_openwebui``'s own payload (never clobbers its ``status``
    key — image-pin status and env-reconcile status are reported
    separately).

    Deliberately does NOT call :func:`reconcile_openwebui_env` — that
    function re-checks ``image_pin.installed_unit_path()``, but
    ``converge_openwebui`` already confirmed its (possibly test-injected
    ``unit_path=``) unit exists before reaching either branch that calls
    this. Re-deriving the path here would silently diverge from the one
    the caller already resolved.
    """
    changed, error = _render_dynamic_env(job_id)
    fields: dict[str, Any] = {}
    if error is not None:
        fields["env_error"] = error
        return fields
    if changed:
        fields["env_changed"] = True
        fields["restarted"] = _restart_openwebui(runner, is_hal0_user)
    return fields


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
        if target_digest is not None:
            # The operator's --target already matches what's installed —
            # still persist the marker so a subsequent un-targeted converge
            # holds the box here instead of drifting back to the release
            # pin (spec §2: --target always writes the override marker).
            try:
                override_path().parent.mkdir(parents=True, exist_ok=True)
                override_path().write_text(target_digest + "\n", encoding="utf-8")
            except OSError as exc:
                log.warning(
                    "components.owui_override_persist_failed", job_id=job_id, error=str(exc)
                )
            # No env render/restart here (unlike the branches below): this
            # is the operator re-affirming an already-matching --target —
            # persisting the marker is the only side effect that branch has
            # ever had, and a runner call here would surprise a caller that
            # passed --target expecting a pure no-op. The next regular
            # converge pass reconciles the env as usual.
            return {**result, "status": "override"}
        status = "override" if overridden else "converged"
        if not apply:
            return {**result, "status": status}
        # Image already matches — the env's dynamic blocks can still be
        # stale (a capability apply/slot delete happened without an image
        # change in between), so every apply pass reconciles them here too.
        payload = {**result, "status": status}
        payload.update(_env_reconcile_fields(job_id, runner, is_hal0_user))
        return payload
    if not apply:
        log.warning(
            "components.owui_stale",
            job_id=job_id,
            installed=current,
            pinned=desired,
            remedy="run 'hal0 update' or 'hal0 update owui'",
        )
        return {**result, "status": "override" if overridden else "stale"}

    # ── Pull first ──
    ref = image_pin.pinned_ref(desired)
    pull_argv = (
        ["sudo", "-n", _PODMAN_RW, "image-pull", ref] if is_hal0_user() else ["podman", "pull", ref]
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
                capture_output=True,
                text=True,
                timeout=_CTL_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {**result, "status": "build_failed", "error": f"repin failed: {exc}"}
        if proc.returncode != 0:
            return {
                **result,
                "status": "build_failed",
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

    # ── Persist / render env / restart ──
    if target_digest is not None:
        try:
            override_path().parent.mkdir(parents=True, exist_ok=True)
            override_path().write_text(target_digest + "\n", encoding="utf-8")
        except OSError as exc:
            log.warning("components.owui_override_persist_failed", job_id=job_id, error=str(exc))

    # Render before the restart below so it's the new image AND the new env
    # that come up together — one restart covers both (a second,
    # env-triggered restart would just be wasted churn here).
    env_changed, env_error = _render_dynamic_env(job_id)

    restarted = _restart_openwebui(runner, is_hal0_user)
    if not restarted:
        log.warning(
            "components.owui_restart_failed",
            job_id=job_id,
            remedy=f"repinned; run 'systemctl restart {image_pin.OPENWEBUI_UNIT_NAME}' and check its journal",
        )
    log.info("components.owui_repinned", job_id=job_id, from_=current, to=desired)
    payload = {
        **result,
        "status": "upgraded",
        "from": current,
        "to": desired,
        "restarted": restarted,
    }
    if env_changed:
        payload["env_changed"] = True
    if env_error is not None:
        payload["env_error"] = env_error
    return payload
