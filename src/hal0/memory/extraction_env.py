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

Privileged operation: writing under ``/etc/systemd/system`` + restarting a unit needs
root. When hal0-api runs unprivileged the write/restart will fail; this module is
best-effort and returns a status dict describing what happened rather than raising, so
the API can surface a partial result instead of 500ing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: systemd drop-in directory + file for the hindsight-api extraction model override.
DROP_IN_DIR = Path("/etc/systemd/system/hindsight-api.service.d")
DROP_IN_PATH = DROP_IN_DIR / "extraction-model.conf"
SERVICE = "hindsight-api"

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


def render_drop_in(slot: str, timeout_s: int = DEFAULT_LLM_TIMEOUT_S) -> str:
    """Return the drop-in contents pinning extraction to ``hal0/<slot>`` + timeout."""
    return _DROP_IN_TEMPLATE.format(slot=slot, timeout_s=int(timeout_s))


def apply_extraction_slot(
    slot: str,
    *,
    timeout_s: int = DEFAULT_LLM_TIMEOUT_S,
    restart: bool = True,
) -> dict[str, Any]:
    """Write the drop-in for ``slot`` and (best-effort) restart hindsight-api.

    Returns a status dict::

        {"slot", "model", "timeout_s", "drop_in", "written",
         "daemon_reloaded", "restarted", "error"}

    ``error`` is ``None`` on full success. The write is atomic (temp + rename) so a
    crash mid-write never leaves a half-written override that would wedge the unit.
    """
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
        DROP_IN_DIR.mkdir(parents=True, exist_ok=True)
        tmp = DROP_IN_PATH.with_suffix(".conf.tmp")
        tmp.write_text(render_drop_in(slot, timeout_s), encoding="utf-8")
        tmp.replace(DROP_IN_PATH)
        result["written"] = True
    except OSError as exc:
        result["error"] = f"could not write {DROP_IN_PATH}: {exc}"
        log.warning("hal0.memory.extraction_dropin_write_failed", slot=slot, error=str(exc))
        return result

    if not restart:
        return result

    for step, args in (
        ("daemon_reloaded", ["systemctl", "daemon-reload"]),
        ("restarted", ["systemctl", "restart", SERVICE]),
    ):
        try:
            subprocess.run(args, check=True, capture_output=True, text=True, timeout=60)
            result[step] = True
        except (OSError, subprocess.SubprocessError) as exc:
            stderr = getattr(exc, "stderr", "") or ""
            result["error"] = (
                f"{' '.join(args)} failed: {exc}{(' — ' + stderr.strip()) if stderr else ''}"
            )
            log.warning(
                "hal0.memory.extraction_restart_failed",
                slot=slot,
                step=step,
                error=str(exc),
            )
            return result

    log.info("hal0.memory.extraction_slot_applied", slot=slot, model=model)
    return result


__all__ = ["DROP_IN_PATH", "apply_extraction_slot", "render_drop_in"]
