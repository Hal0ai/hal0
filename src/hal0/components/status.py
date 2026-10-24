"""Merged component status: catalog x components.json x live probes."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

import structlog

from hal0.components.registry import COMPONENTS
from hal0.components.state import load_component_state

# Keep in step with hal0.components.runner._FAILURE_STATUSES. ``busy`` (the
# Hermes builder lock was held) is deliberately absent: it is not a failure,
# so a row whose last result is ``busy`` derives from installed vs pinned and
# reads ``pending`` while the live ref still differs (ADR-0032 decision 6).
_FAILURE_STATUSES = frozenset(
    {"build_failed", "snapshot_failed", "rolled_back", "activation_failed"}
)
_PENDING_STATUSES = frozenset(
    {"pending", "stale", "build_failed", "snapshot_failed", "rolled_back", "activation_failed"}
)

log = structlog.get_logger(__name__)


def _resolve(fn: Callable[[], Any]) -> Callable[[], Any]:
    """Re-resolve a probe callable by name in its defining module at call
    time rather than trusting the reference ``ComponentDef.installed`` /
    ``ComponentDef.pinned`` captured at catalog-build time.

    Same trap as ``hal0.components.runner._resolve_converge``:
    ``registry.COMPONENTS`` is a module-level tuple built once at import,
    so its ``installed``/``pinned`` fields are bound to the function
    OBJECTS that existed at that moment. ``unittest.mock.patch`` on the
    module-level name (e.g. ``hal0.components.registry._hindsight_installed``)
    reassigns that name in the module's namespace but cannot retroactively
    change an already-captured reference. Looking the name up again here,
    against the live module namespace, is what lets tests patch each
    probe's module-level wrapper.
    """
    module = sys.modules.get(getattr(fn, "__module__", None))
    name = getattr(fn, "__name__", None)
    if module is not None and name is not None and hasattr(module, name):
        return getattr(module, name)
    return fn


def _hermes_needs_build() -> bool:
    from hal0.components.hermes_arm import hermes_build_trigger

    return hermes_build_trigger() is not None


def _pin_label(component_id: str) -> str | None:
    if component_id == "openwebui":
        from hal0.openwebui.image_pin import OPENWEBUI_PIN_LABEL

        return OPENWEBUI_PIN_LABEL
    return None


def _override(component_id: str) -> str | None:
    """The persisted operator override for ``component_id`` (``--target``), if any."""
    try:
        if component_id == "openwebui":
            from hal0.components.openwebui_arm import read_pin_override
        elif component_id == "hermes":
            from hal0.components.hermes_arm import read_pin_override
        else:
            return None
        return read_pin_override()
    except Exception as exc:
        log.warning("components.override_probe_failed", component=component_id, error=str(exc))
        return None


def _hermes_override_state(override: str, installed: str | None) -> tuple[str, str | None]:
    """``(status, note)`` of a Hermes row that carries a ``--target`` override."""
    from hal0.agents.hermes_provision import VETTED_HERMES_REFS
    from hal0.components.hermes_arm import UNVETTED_OVERRIDE_REMEDY, unvetted_override_error

    if override not in VETTED_HERMES_REFS:
        return "pending", f"{unvetted_override_error(override)}; {UNVETTED_OVERRIDE_REMEDY}"
    if installed == override[:12] and not _hermes_needs_build():
        return "override", None
    return "pending", None


def component_status_snapshot() -> dict[str, Any]:
    recorded = load_component_state()
    rows: list[dict[str, Any]] = []
    for comp in COMPONENTS:
        last = recorded.get(comp.id) or {}
        try:
            installed = _resolve(comp.installed)()
        except Exception as exc:
            log.warning("components.installed_probe_failed", component=comp.id, error=str(exc))
            installed = None
        try:
            pinned = _resolve(comp.pinned)()
        except Exception as exc:
            log.warning("components.pinned_probe_failed", component=comp.id, error=str(exc))
            pinned = "unknown"

        override = _override(comp.id)
        override_note: str | None = None
        if last.get("status") in _FAILURE_STATUSES:
            derived = last["status"]
        elif installed is None:
            derived = "not-installed"
        elif last.get("status") == "busy":
            # The last pass found the Hermes builder lock held and did not
            # run: not a failure, but not confirmed converged either.
            derived = "pending"
        elif comp.id == "hermes" and override is not None:
            derived, override_note = _hermes_override_state(override, installed)
        elif installed == pinned and comp.id == "hermes" and _hermes_needs_build():
            # A pre-layout host's stamp already equals the pin, but the next
            # update still has to migrate it (ADR-0032 decision 6).
            derived = "pending"
        elif installed == pinned:
            derived = "converged"
        else:
            derived = "pending"
            if comp.id == "openwebui" and override == installed:
                derived = "override"

        rows.append(
            {
                "id": comp.id,
                "name": comp.name,
                "kind": comp.kind,
                "service_id": comp.service_id,
                "installed": installed,
                "pinned": pinned,
                "pin_label": _pin_label(comp.id),
                # An operator --target that holds the row off the release pin
                # until --clear-override (null when none is set).
                "override": override,
                "status": derived,
                "error": last.get("error"),
                "remedy": last.get("remedy"),
                # An informational line from the last converge that is not a
                # failure (e.g. Hermes config keys applied on a converged box).
                "note": last.get("note") or override_note,
                "ts": last.get("ts"),
                "detail": last.get("detail") or [],
            }
        )
    pending = sum(1 for r in rows if r["status"] in _PENDING_STATUSES)
    return {"components": rows, "pending": pending}
