"""Propagate the memory graph extraction slot to hindsight-api (ADR-0023).

Hindsight builds its graph natively via its own extraction LLM, configured by the
``HINDSIGHT_API_LLM_MODEL`` env in the ``hindsight-api.service`` unit. To make the
target operator-selectable WITHOUT hand-editing the installer-owned base unit, hal0
owns a systemd **drop-in**::

    /etc/systemd/system/hindsight-api.service.d/extraction-model.conf
        [Service]
        Environment=HINDSIGHT_API_LLM_MODEL=hal0/<slot>

and runs ``systemctl daemon-reload`` + ``systemctl restart hindsight-api`` so the
engine picks up the new target. The slot is addressed as the ``hal0/<slot>`` virtual
(resolved by the dispatcher to that slot's model — ADR-0023 §2), so the value tracks
the slot, never a hardcoded model id.

Privileged operation, routed through the seam (#1641): both the drop-in write and
the restart are genuinely-root, and hal0-api runs as the unprivileged ``hal0``
service user (``User=hal0``). The original implementation wrote
``/etc/systemd/system`` directly and shelled out to a bare ``systemctl``, so on
every standard install the write died with ``EPERM`` (the ``.d`` dir is
``root:root``) and the restart, had it been reached, would have hit polkit's
"Interactive authentication required" — while ``hal0.toml`` recorded the new slot
regardless, so the dashboard reported an override that was never applied. Every
step now goes through :class:`hal0.system.SystemCtlSeam`:

* the drop-in write -> ``hal0-systemctl write-hindsight-dropin`` (body on stdin,
  the path is a root-side literal — the ``write-gateway-dropin`` posture);
* ``daemon-reload`` -> the seam's own verb;
* the restart -> ``svc-restart hindsight`` (the wrapper's closed companion-unit
  map), which is why the unit is spelled ``hindsight-api.service`` here.

Off the ``hal0`` service account (root, a dev shell, CI, the unit tests) the seam
is a passthrough and everything runs directly, exactly as before.

Still best-effort: this returns a status dict describing what happened rather than
raising, so the API can surface a partial result instead of 500ing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import structlog

from hal0.system.seam import SystemCtlSeam

log = structlog.get_logger(__name__)

#: systemd drop-in directory + file for the hindsight-api extraction model override.
DROP_IN_DIR = Path("/etc/systemd/system/hindsight-api.service.d")
DROP_IN_PATH = DROP_IN_DIR / "extraction-model.conf"
#: Full unit name — ``COMPANION_SERVICE_UNITS`` keys on it to reach the wrapper's
#: ``svc-restart hindsight`` arm. A bare ``hindsight-api`` would miss that map and
#: fall through to an unprivileged (polkit-blocked) systemctl.
SERVICE = "hindsight-api.service"

#: Bound on each seam call so a wedged unit can never pin an event-loop thread.
_SYSTEMCTL_TIMEOUT_S = 60.0

#: Default daemon LLM timeout (seconds) — mirrors MemoryGraphConfig.llm_timeout_s.
DEFAULT_LLM_TIMEOUT_S = 300

_DROP_IN_TEMPLATE = (
    "# Managed by hal0 (ADR-0023 — memory.graph.extraction_slot / llm_timeout_s).\n"
    "# Overrides HINDSIGHT_API_LLM_MODEL and HINDSIGHT_API_LLM_TIMEOUT in the base\n"
    "# hindsight-api.service unit. Do not edit by hand; set via `hal0 memory graph\n"
    "# enable --slot <name>` or the dashboard, which rewrites this file and\n"
    "# restarts the service.\n"
    "[Service]\n"
    "Environment=HINDSIGHT_API_LLM_MODEL=hal0/{slot}\n"
    "Environment=HINDSIGHT_API_LLM_TIMEOUT={timeout_s}\n"
)


def _detail(exc: BaseException) -> str:
    """``str(exc)`` plus the child's stderr when there is one.

    A seam failure's *cause* almost always lives in stderr — ``sudo: a password
    is required`` (no grant), ``hal0-systemctl: bad cmd`` (stale wrapper) — while
    ``str(exc)`` is just the exit code. Both go to the operator.
    """
    stderr = getattr(exc, "stderr", "") or ""
    if isinstance(stderr, bytes):  # TimeoutExpired can carry raw bytes
        stderr = stderr.decode("utf-8", "replace")
    stderr = stderr.strip()
    return f"{exc}{(' — ' + stderr) if stderr else ''}"


#: The wrapper's own ``die()`` exit code (installer/wrappers/hal0-systemctl).
_SEAM_USAGE_RC = 64

_STALE_WRAPPER_HINT = (
    " — the installed /usr/lib/hal0/bin/hal0-systemctl predates this verb; "
    "re-run the installer (`sudo bash install.sh`) to refresh the seam wrapper"
)


def _stale_wrapper_hint(exc: BaseException) -> str:
    """Remediation text when the seam rejected the verb outright.

    ``hal0 update`` swaps the release tree and re-pips the venv but does not
    reinstall ``${LIB_DIR}/bin/*`` — only ``install.sh`` does. So new Python can
    meet an old wrapper, which answers a verb it doesn't know with
    ``hal0-systemctl: bad cmd: ...`` and exit 64. That is a fixable operator
    condition, not a bug report, so say how to fix it.
    """
    rc = getattr(exc, "returncode", None)
    if rc == _SEAM_USAGE_RC or "bad cmd" in _detail(exc):
        return _STALE_WRAPPER_HINT
    return ""


def render_drop_in(slot: str, timeout_s: int = DEFAULT_LLM_TIMEOUT_S) -> str:
    """Return the drop-in contents pinning extraction to ``hal0/<slot>`` + timeout."""
    return _DROP_IN_TEMPLATE.format(slot=slot, timeout_s=int(timeout_s))


def drop_in_matches(slot: str, timeout_s: int = DEFAULT_LLM_TIMEOUT_S) -> bool:
    """True when the on-disk drop-in already reflects ``(slot, timeout_s)``.

    #1682 review: comparing only against ``hal0.toml`` is not enough to
    decide propagation is unnecessary. A host hit by the pre-seam write bug
    (or any other silent failure) can have ``hal0.toml`` already recording
    this exact slot while the drop-in was never actually written or still
    names something else — re-requesting the *same* slot would then never
    reconcile.

    The drop-in is 0644 root:root (world-readable, #1641), so this is a
    plain, unprivileged read — no seam round trip. Read as explicit UTF-8
    (#1717 review): the file is always written UTF-8 by
    :class:`~hal0.system.seam.SystemCtlSeam`, and the template's em dash
    would otherwise be locale-dependent — under e.g. ``LC_ALL=C`` a bare
    ``Path.read_text()`` can fail to decode a byte-for-byte correct file,
    misreporting a healthy drop-in as drift on every enabled graph PUT.
    Any read failure (missing file on a fresh install or a host that never
    propagated, a permission oddity, or genuinely undecodable content —
    ``Path.read_text()`` raises ``UnicodeDecodeError``, not ``OSError``)
    counts as "does not match": propagate and let the atomic rewrite
    repair it, instead of an otherwise-idempotent PUT 500ing.
    """
    try:
        return DROP_IN_PATH.read_text(encoding="utf-8") == render_drop_in(slot, timeout_s)
    except (OSError, UnicodeDecodeError):
        return False


def apply_extraction_slot(
    slot: str,
    *,
    timeout_s: int = DEFAULT_LLM_TIMEOUT_S,
    restart: bool = True,
    seam: SystemCtlSeam | None = None,
) -> dict[str, Any]:
    """Write the drop-in for ``slot`` and (best-effort) restart hindsight-api.

    Returns a status dict::

        {"slot", "model", "timeout_s", "drop_in", "written",
         "daemon_reloaded", "restarted", "error"}

    ``error`` is ``None`` on full success. The write is atomic (temp + rename) so a
    crash mid-write never leaves a half-written override that would wedge the unit.

    ``seam`` is an injection point (default-constructed = production behaviour), so
    the privileged routing is unit-testable without sudo, a real ``hal0`` user, or
    a writable ``/etc``. Blocking: callers on the event loop must hop a thread
    (``asyncio.to_thread``) — the restart waits on a hindsight-api cold start.
    """
    seam = seam if seam is not None else SystemCtlSeam()
    model = f"hal0/{slot}"
    result: dict[str, Any] = {
        "slot": slot,
        "model": model,
        "timeout_s": int(timeout_s),
        "drop_in": str(DROP_IN_PATH),
        "written": False,
        "daemon_reloaded": False,
        "restarted": False,
        "error": None,
    }

    try:
        seam.write_hindsight_dropin(
            render_drop_in(slot, timeout_s),
            path=DROP_IN_PATH,
            timeout=_SYSTEMCTL_TIMEOUT_S,
        )
        result["written"] = True
    except (OSError, subprocess.SubprocessError) as exc:
        result["error"] = (
            f"could not write {DROP_IN_PATH}: {_detail(exc)}{_stale_wrapper_hint(exc)}"
        )
        log.warning("hal0.memory.extraction_dropin_write_failed", slot=slot, error=str(exc))
        return result

    if not restart:
        return result

    for step, args in (
        ("daemon_reloaded", ("systemctl", "daemon-reload")),
        ("restarted", ("systemctl", "restart", SERVICE)),
    ):
        try:
            seam.systemctl(*args, check=True, timeout=_SYSTEMCTL_TIMEOUT_S)
            result[step] = True
        except (OSError, subprocess.SubprocessError) as exc:
            result["error"] = f"{' '.join(args)} failed: {_detail(exc)}"
            log.warning(
                "hal0.memory.extraction_restart_failed",
                slot=slot,
                step=step,
                error=str(exc),
            )
            return result

    log.info("hal0.memory.extraction_slot_applied", slot=slot, model=model)
    return result


__all__ = ["DROP_IN_PATH", "apply_extraction_slot", "drop_in_matches", "render_drop_in"]
