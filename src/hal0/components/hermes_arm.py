"""Converge the bundled Hermes release onto the requirements.txt pin (spec §1).

Goes through the same release builder (:mod:`hal0.agents.hermes_release`) as
provisioning and ``updater.repair_hermes_mcp_client``, so every path lands on
the state a fresh provision produces. The status identity is the
``venv.pin`` stamp, written from ``release.json`` by every activation; the
build decision reads ``release.json`` itself. No snapshot machinery — hermes
has no one-way DB; HERMES_HOME is touched only by ``hermes config migrate``
and the flip-back's config restore.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from hal0.agents import hermes_release as _rel
from hal0.config.paths import var_lib
from hal0.system.seam import is_hal0_service_user

log = structlog.get_logger(__name__)

Runner = Callable[..., subprocess.CompletedProcess]


def stamp_path() -> Path:
    return var_lib() / "state" / "agents" / "hermes" / "venv.pin"


def _provision_checkpoint_path() -> Path:
    return var_lib() / "state" / "agents" / "hermes" / "provision.json"


def _installed_pin_from_provision_checkpoint() -> str | None:
    """Fall back to the provisioner's last-run report for the installed pin.

    No box provisioned before the venv.pin stamp existed has one — see
    ``hermes_arm.stamp_path`` — so on those boxes ``installed_hermes_pin``
    would otherwise report "not installed" on an install that has been
    running fine, and the first `hal0 update` needlessly rebuilds the live
    venv. ``hermes_provision._write_run_report`` (hermes_provision.py:6618)
    writes a flat JSON object with a top-level ``hermes_version`` key on
    every provision/repair run; that is the same identifier
    ``_hermes_version_pin()`` produces, which is what the venv.pin stamp
    also holds. Parsed defensively: any missing file, bad JSON, non-dict
    top level, or non-string/empty ``hermes_version`` reads as "no
    fallback available" (``None``) rather than raising.
    """
    try:
        text = _provision_checkpoint_path().read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("hermes_version")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def installed_hermes_pin(venv: Path | None = None) -> str | None:
    """The pin identifier stamped after the last successful converge.

    Gated on the venv actually existing: a deprovisioned box (venv
    removed, stamp file surviving under ``var_lib()`` — state is preserved
    across uninstall unless ``--keep-data`` is dropped) must read as
    ``None``, not as a stale "converged"/"pending" verdict, or the
    component-status derivation (spec §1 / Task 8) misreports an
    uninstalled component as still on some version.

    The default is ``var_lib() / "venvs" / "hermes"`` rather than
    ``hermes.HERMES_VENV_DEFAULT`` directly: in production ``HAL0_HOME`` is
    unset and the two are the same path
    (``/var/lib/hal0/venvs/hermes``), but under HAL0_HOME sandboxing (dev,
    tests) this one follows the sandbox and the hardcoded FHS constant does
    not — gating on the constant made this getter permanently blind to a
    freshly-written, correctly-sandboxed stamp in exactly that posture.

    When the venv.pin stamp is absent or empty — every box provisioned
    before this component-status pass shipped — fall back to the
    ``hermes_version`` recorded in the provisioner's ``provision.json``
    checkpoint (see :func:`_installed_pin_from_provision_checkpoint`)
    rather than misreporting a working install as not-installed. The
    stamp wins whenever it is present.
    """
    venv = venv or (var_lib() / "venvs" / "hermes")
    if not (venv / "bin" / "python").exists():
        return None
    try:
        value = stamp_path().read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value:
        return value
    return _installed_pin_from_provision_checkpoint()


def hermes_build_trigger(venv: Path | None = None) -> str | None:
    """Why the live Hermes release needs a build, or ``None`` (read-only).

    The component row uses it so a host still on the pre-layout venv (whose
    stamp already equals the pin) reads ``pending``, not ``converged``.
    Best-effort: any error reads as ``None``.
    """
    try:
        from hal0.agents import hermes_release as rel

        layout = rel.Layout.for_venv(venv or (var_lib() / "venvs" / "hermes"))
        if (
            not layout.venv_link.exists()
            and rel.live_release(layout) is None
            and not rel.complete_releases(layout)
        ):
            return None
        ref = rel.require_vetted(rel.read_pin().ref)
        return rel.build_trigger(layout, ref, rel.target_minor(ref), rel.configured_extras())
    except Exception:
        return None


#: What `hal0 update` reports when the backstop below changed config.yaml.
CONFIG_BACKSTOP_NOTE = (
    "Hermes config keys applied; restart hal0-agent@hermes to load agent.system_prompt"
)


def _config_backstop(result: dict[str, Any], *, job_id: str | None) -> None:
    """Write hal0's must-have Hermes config keys on a converged box (#2615).

    A release activation writes them (``hermes_release._activate``), but a box
    already on the pinned release never activates again, so `hal0 update`
    would otherwise leave a config written before that fix as it is.
    Idempotent; a no-op when the file already has the keys. No restart is
    needed for ``sessions.auto_prune``: Hermes reads it at each startup.
    ``agent.system_prompt`` is read by each new session's agent, so the note
    says how to make the running agent pick it up now. Never fails the
    converge.
    """
    from hal0.agents import hermes_provision as hermes

    home = var_lib() / ".hermes"
    config = home / "config.yaml"

    def _backend() -> dict[str, str | None]:
        """``terminal.backend`` of the root config and every profile's."""
        paths = [config, *sorted((home / "profiles").glob("*/config.yaml"))]
        out: dict[str, str | None] = {}
        for path in paths:
            try:
                out[str(path)] = hermes._terminal_backend_in_config(
                    path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeDecodeError):
                out[str(path)] = None
        return out

    backend_before = _backend()
    try:
        changed = hermes.apply_release_config_invariants(home)
    except hermes.TerminalGateError as exc:
        # Not cosmetic: a served profile's side agents still see the terminal.
        log.error("components.hermes_terminal_gate_failed", job_id=job_id, error=str(exc))
        result["detail"] = [*result.get("detail", []), f"Hermes terminal gate not applied: {exc}"]
        if _backend() != backend_before:
            # The root (and any profile written before the failure) already
            # changed on disk; the live consumers must still re-read it.
            hermes.try_restart_hermes_consumers()
        return
    except Exception as exc:  # the converge verdict must not depend on it
        log.warning("components.hermes_config_backstop_failed", job_id=job_id, error=str(exc))
        result["detail"] = [*result.get("detail", []), f"Hermes config keys not applied: {exc}"]
        return
    if changed:
        log.info("components.hermes_config_backstop_applied", job_id=job_id, home=str(home))
        result["note"] = CONFIG_BACKSTOP_NOTE
        result["detail"] = [*result.get("detail", []), CONFIG_BACKSTOP_NOTE]
        if _backend() != backend_before:
            # Unlike a release activation, this runs with both Hermes consumers
            # live; the terminal gate (#2878) only reaches a running gateway and
            # its side agents once the process re-reads its config.
            failed = hermes.try_restart_hermes_consumers()
            note = (
                "Hermes restarted to apply the terminal posture"
                if not failed
                else "Hermes terminal posture changed; restart " + ", ".join(failed)
            )
            log.info(
                "components.hermes_terminal_gate_applied",
                job_id=job_id,
                restart_failed=failed,
            )
            result["detail"] = [*result.get("detail", []), note]


#: What `hal0 update` reports when the gateway unit was healed.
GATEWAY_HEALED_NOTE = _rel.GATEWAY_HEALED_NOTE


def _heal_gateway_unit(
    result: dict[str, Any],
    *,
    layout: Any,
    job_id: str | None,
    seam_factory: Callable[[], Any] | None = None,
    regen_reason: Callable[[], str | None] | None = None,
) -> None:
    """Regenerate a stale gateway unit through ``hal0-update hermes-gateway`` (#2615).

    The converged and upgraded paths' heal, through the same
    :func:`hermes_release.heal_gateway_unit` an activation runs before the
    new release's units start (#2894). It acts once the live release is
    v0.22-shape, and only when the unprivileged probe finds the unit stale
    (pre-0.22, or not hal0's launcher) or linger off. Never fails the
    converge: a failure, or a missing grant, adds the existing remedy to
    ``detail`` and the `hal0 update` notice and the doctor row stay as the
    fallback.
    """
    from hal0.agents import hermes_release as rel

    heal = rel.heal_gateway_unit(
        layout, job_id=job_id, seam_factory=seam_factory, regen_reason=regen_reason
    )
    note = heal.note
    if note and note not in result.get("detail", []):
        result["detail"] = [*result.get("detail", []), note]


def converge_hermes(
    *,
    job_id: str | None = None,
    apply: bool = True,
    runner: Runner | None = None,
    venv: Path | None = None,
    is_hal0_user: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Converge the live Hermes release onto the pin through the release builder.

    ADR-0032 decision 6: builds on any trigger (pre-layout venv, live ref or
    interpreter differs from the pin). Never waits for the builder lock: a
    held lock returns ``busy`` and the next ``hal0 update`` retries. Every
    activation stops and starts only the units that were running, so a
    gateway the operator stopped is never started.
    """
    from hal0.agents import hermes_provision as hermes
    from hal0.agents import hermes_release as rel

    runner = runner if runner is not None else subprocess.run
    is_hal0_user = is_hal0_user if is_hal0_user is not None else is_hal0_service_user
    venv = venv or hermes.HERMES_VENV_DEFAULT

    layout = rel.Layout.for_venv(venv)
    # A live release with a missing venvs/hermes link (a builder that died
    # mid-switch) is still provisioned: the builder relinks it.
    # A live release with a missing venvs/hermes link, or a release tree a
    # migration left without current (a builder that died mid-switch), is
    # still provisioned: the builder relinks or adopts it. Only a tree with no
    # complete release at all is "not provisioned".
    if (
        not (venv / "bin" / "python").exists()
        and rel.live_release(layout) is None
        and not rel.complete_releases(layout)
    ):
        return {"status": "skipped", "reason": "hermes not provisioned"}

    pinned = hermes._hermes_version_pin()
    installed = installed_hermes_pin(venv)
    result: dict[str, Any] = {"installed": installed, "pinned": pinned}
    try:
        target = rel.require_vetted(rel.read_pin().ref)
    except rel.ReleaseError as exc:
        return {**result, "status": "build_failed", "error": str(exc)}
    try:
        minor = rel.target_minor(target)
    except rel.ReleaseError as exc:
        return {**result, "status": "build_failed", "error": str(exc)}
    # Read before any drop to hal0: only root sees the gateway's secrets vault.
    platform_extras = rel.configured_extras()
    trigger = rel.build_trigger(layout, target, minor, platform_extras)
    if trigger is None:
        if apply:
            _config_backstop(result, job_id=job_id)
            _heal_gateway_unit(result, layout=layout, job_id=job_id)
        return {**result, "status": "converged"}
    result["trigger"] = trigger
    if not apply:
        log.warning(
            "components.hermes_stale",
            job_id=job_id,
            installed=installed,
            pinned=pinned,
            trigger=trigger,
            remedy="run 'hal0 update'",
        )
        return {**result, "status": "stale"}

    outcome = rel.ensure_release(
        caller="converge",
        venv=venv,
        wait=False,
        platform_extras=platform_extras,
        units=rel.SystemdUnits(run=runner, use_seam=is_hal0_user()),
    )
    if outcome.status == "busy":
        holder = outcome.holder or {}
        log.warning(
            "components.hermes_busy",
            job_id=job_id,
            holder_pid=holder.get("pid"),
            holder_caller=holder.get("caller"),
            holder_since=holder.get("started_at"),
        )
        return {**result, "status": "busy", "holder_pid": holder.get("pid"), "holder": holder}
    if outcome.status in ("build_failed", "activation_failed", "offline"):
        status = "activation_failed" if outcome.status == "activation_failed" else "build_failed"
        return {
            **result,
            "status": status,
            "error": outcome.error,
            "remedy": outcome.remedy or "run 'sudo hal0 agent reprovision hermes --repair'",
            "detail": list(outcome.warnings),
        }
    if outcome.status == "converged":
        _config_backstop(result, job_id=job_id)
        _heal_gateway_unit(result, layout=layout, job_id=job_id)
        return {**result, "status": "converged"}

    log.info(
        "components.hermes_upgraded",
        job_id=job_id,
        installed=installed,
        pinned=pinned,
        release=outcome.release_id,
        migrated_legacy=outcome.migrated_legacy,
    )
    out = {
        **result,
        "status": "upgraded",
        "from": installed,
        "to": pinned,
        "release": outcome.release_id,
    }
    if outcome.warnings:
        out["detail"] = list(outcome.warnings)
    _heal_gateway_unit(out, layout=layout, job_id=job_id)
    return out
