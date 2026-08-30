"""Sequential best-effort converge over the component catalog (spec §1).

Called as the final pass of ``run_post_activation_migrations`` (both
upgrade paths) and per-component by the retry route. Catalog order is the
converge order; one component failing never blocks the next; every result
lands in components.json. Only genuine bugs inside an arm are caught here
— arms themselves report operational failures as status dicts.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

import structlog

from hal0.components.registry import COMPONENTS, ComponentDef
from hal0.components.state import load_component_state, record_component_result

log = structlog.get_logger(__name__)

#: Statuses that carry operator-facing failure breadcrumbs (error/remedy —
#: what the dashboard Retry affordance reads). A boot-time diagnose-only
#: pass must not clobber one of these with a generic "stale"/"pending"
#: result that has no error/remedy of its own (M1, final-review).
_FAILURE_STATUSES = ("build_failed", "snapshot_failed", "rolled_back")


def _resolve_converge(comp: ComponentDef) -> Callable[..., dict[str, Any]]:
    """Re-resolve the arm callable by name in its defining module at call
    time rather than trusting the reference ``ComponentDef.converge``
    captured at catalog-build time.

    ``registry.COMPONENTS`` is a module-level tuple built once at import,
    so its ``converge`` fields are bound to the function OBJECTS that
    existed at that moment. ``unittest.mock.patch`` on the module-level
    name (e.g. ``hal0.components.registry._openwebui_converge``) reassigns
    that name in the module's namespace but cannot retroactively change an
    already-captured reference. Looking the name up again here, against
    the live module namespace, is what lets tests patch each arm's
    module-level wrapper.
    """
    fn = comp.converge
    module = sys.modules.get(getattr(fn, "__module__", None))
    name = getattr(fn, "__name__", None)
    if module is not None and name is not None and hasattr(module, name):
        return getattr(module, name)
    return fn


def _arm_kwargs(
    comp: ComponentDef, *, job_id: str | None, apply: bool,
    image_retag: bool, engine: bool, hermes_install: bool,
) -> dict[str, Any]:
    if comp.id == "hindsight":
        # engine_upgrade's own kwarg spelling; boot passes upgrade=False.
        return {"job_id": job_id, "upgrade": apply and engine}
    if comp.id == "runner-images":
        return {"job_id": job_id, "apply": apply and image_retag}
    if comp.id == "hermes":
        return {"job_id": job_id, "apply": apply and hermes_install}
    return {"job_id": job_id, "apply": apply}


def _is_diagnose_only(comp: ComponentDef, kwargs: dict[str, Any]) -> bool:
    """Whether ``kwargs`` (as built by :func:`_arm_kwargs`) runs a probe-only
    pass for ``comp`` rather than one that may actually converge it.

    hindsight's arm spells its apply flag ``upgrade`` instead of ``apply``
    (see ``_arm_kwargs``) — every other arm uses ``apply``.
    """
    if comp.id == "hindsight":
        return not kwargs.get("upgrade", True)
    return not kwargs.get("apply", True)


def converge_component(
    comp: ComponentDef, *, diagnose_only: bool = False, **kwargs: Any
) -> dict[str, Any]:
    try:
        result = _resolve_converge(comp)(**kwargs)
    except Exception as exc:  # genuine bug in an arm — isolate it
        log.warning("components.converge_arm_crashed", component=comp.id, error=str(exc))
        result = {"status": "build_failed", "error": str(exc)}

    # Boot-time diagnose (apply=False) runs on every daemon restart. If the
    # last recorded result for this component was a real failure
    # (build_failed/snapshot_failed/rolled_back — the statuses that carry
    # the error/remedy the dashboard's Retry affordance reads), a
    # diagnose-only result must not overwrite it: diagnose reports drift
    # ("stale"/"pending"), not the richer failure breadcrumb, so recording
    # it here would silently erase the operator-facing detail on every
    # restart until someone happens to retry. An apply=True pass (retry,
    # `hal0 update`) always records — it may be curing or re-confirming the
    # failure, and either way its result is the freshest truth.
    if diagnose_only:
        existing = load_component_state().get(comp.id) or {}
        if existing.get("status") in _FAILURE_STATUSES:
            log.info(
                "components.diagnose_skip_record",
                component=comp.id,
                existing_status=existing.get("status"),
            )
            return result

    record_component_result(comp.id, result)
    return result


def converge_components(
    *,
    job_id: str | None = None,
    apply: bool = True,
    image_retag: bool = True,
    engine: bool = True,
    hermes_install: bool = True,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for comp in COMPONENTS:
        kwargs = _arm_kwargs(
            comp, job_id=job_id, apply=apply,
            image_retag=image_retag, engine=engine, hermes_install=hermes_install,
        )
        diagnose_only = _is_diagnose_only(comp, kwargs)
        results[comp.id] = converge_component(comp, diagnose_only=diagnose_only, **kwargs)
    return results
