"""Merged component status: catalog x components.json x live probes."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

import structlog

from hal0.components.registry import COMPONENTS
from hal0.components.state import load_component_state

_FAILURE_STATUSES = frozenset({"build_failed", "snapshot_failed", "rolled_back"})
_PENDING_STATUSES = frozenset(
    {"pending", "stale", "build_failed", "snapshot_failed", "rolled_back"}
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


def _pin_label(component_id: str) -> str | None:
    if component_id == "openwebui":
        from hal0.openwebui.image_pin import OPENWEBUI_PIN_LABEL

        return OPENWEBUI_PIN_LABEL
    return None


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

        if last.get("status") in _FAILURE_STATUSES:
            derived = last["status"]
        elif installed is None:
            derived = "not-installed"
        elif installed == pinned:
            derived = "converged"
        else:
            derived = "pending"
            if comp.id == "openwebui":
                from hal0.components.openwebui_arm import read_pin_override

                if read_pin_override() == installed:
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
                "status": derived,
                "error": last.get("error"),
                "remedy": last.get("remedy"),
                "ts": last.get("ts"),
                "detail": last.get("detail") or [],
            }
        )
    pending = sum(1 for r in rows if r["status"] in _PENDING_STATUSES)
    return {"components": rows, "pending": pending}
