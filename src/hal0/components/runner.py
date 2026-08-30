"""Sequential best-effort converge over the component catalog (spec §1).

Called as the final pass of ``run_post_activation_migrations`` (both
upgrade paths) and per-component by the retry route. Catalog order is the
converge order; one component failing never blocks the next; every result
lands in components.json. Only genuine bugs inside an arm are caught here
— arms themselves report operational failures as status dicts.
"""

from __future__ import annotations

import sys
from typing import Any, Callable

import structlog

from hal0.components.registry import COMPONENTS, ComponentDef
from hal0.components.state import record_component_result

log = structlog.get_logger(__name__)


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


def converge_component(comp: ComponentDef, **kwargs: Any) -> dict[str, Any]:
    try:
        result = _resolve_converge(comp)(**kwargs)
    except Exception as exc:  # genuine bug in an arm — isolate it
        log.warning("components.converge_arm_crashed", component=comp.id, error=str(exc))
        result = {"status": "build_failed", "error": str(exc)}
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
        results[comp.id] = converge_component(comp, **kwargs)
    return results
