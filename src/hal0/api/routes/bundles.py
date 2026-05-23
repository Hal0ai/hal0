"""HTTP routes for the first-run bundle picker (ADR-0010 / PR-17).

Mounted under ``/api/bundles`` (see :mod:`hal0.api.__init__`):

  - ``GET  /api/bundles``           — picker payload: tiers + eligible
                                      subset + current marker status.
  - ``GET  /api/bundles/skip``      — record the "Skip — configure
                                      manually" branch and unblock the
                                      dashboard.
  - ``POST /api/bundles/{name}``    — apply a tier pick; persists the
                                      marker and forwards capability
                                      rows through CapabilityOrchestrator.

The routes are admin-gated at ``include_router`` time (see
``hal0.api.__init__``). No per-route auth helper is needed here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from hal0.api.deps import CapabilityOrchestratorDep
from hal0.bundles import eligibility
from hal0.bundles import store as bundle_store
from hal0.bundles import tiers as bundle_tiers
from hal0.errors import BadRequest, NotFound

router = APIRouter()


def _bundle_payload() -> dict[str, Any]:
    summaries = bundle_tiers.list_bundle_summaries()
    eligible = set(eligibility.eligible_tiers())
    choice = bundle_store.read_choice()
    return {
        "host_ram_gb": eligibility.host_ram_gb(),
        "tiers": [bundle.to_dict() for bundle in summaries],
        "eligible": [bundle.name for bundle in summaries if bundle.name in eligible],
        "choice": choice.to_dict() if choice is not None else None,
        "picker_pending": choice is None,
    }


@router.get("")
async def get_bundles() -> dict[str, Any]:
    """Return the picker payload: tier list + eligibility + marker."""

    return _bundle_payload()


@router.get("/skip")
async def skip_bundles() -> dict[str, Any]:
    """Record the "Skip — configure manually" decision.

    Idempotent — re-hitting the endpoint after the marker is dropped is
    a no-op (the marker rewrites with a fresh timestamp but the
    selection state is unchanged).
    """

    choice = bundle_store.mark_skipped()
    return {"ok": True, "choice": choice.to_dict()}


def _resolve_bundle_name(name: str) -> str:
    """Map a URL-supplied bundle name (any case) to the canonical entry."""

    if name in bundle_tiers.BUNDLES:
        return name
    lower = name.lower()
    for canonical in bundle_tiers.BUNDLES:
        if canonical.lower() == lower:
            return canonical
    raise NotFound(
        f"unknown bundle {name!r}",
        code="bundle.unknown",
        details={"name": name, "valid": list(bundle_tiers.BUNDLES)},
    )


@router.post("/{name}")
async def select_bundle(
    name: str,
    request: Request,
    orchestrator: CapabilityOrchestratorDep,
) -> dict[str, Any]:
    """Apply a bundle tier.

    Body: ``{"npu_opt_in": bool}`` — optional, only meaningful for
    tiers whose manifest ships ``npu_trio_shown=True`` (Pro / Max).
    For other tiers the flag is recorded verbatim but doesn't gate any
    side effects.
    """

    canonical = _resolve_bundle_name(name)
    manifest = bundle_tiers.load_bundle(canonical)

    try:
        body = await request.json()
    except Exception:
        body = {}
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise BadRequest(
            "request body must be a JSON object",
            code="request.not_an_object",
        )

    allowed_keys = {"npu_opt_in"}
    extras = set(body.keys()) - allowed_keys
    if extras:
        raise BadRequest(
            f"unexpected keys in body: {sorted(extras)}",
            code="bundle.unknown_fields",
            details={"allowed": sorted(allowed_keys), "unexpected": sorted(extras)},
        )

    npu_opt_in = bool(body.get("npu_opt_in", False))
    if npu_opt_in and not manifest.bundle.npu_trio_shown:
        # Explicit reject — clicking the NPU box on a tier that doesn't
        # surface it would be hand-edited body, not a wizard mistake.
        raise BadRequest(
            f"bundle {canonical!r} does not expose the NPU trio",
            code="bundle.npu_not_available",
            details={"name": canonical},
        )

    # Forward each capability-mappable row through the orchestrator
    # first so the marker reflects the actual applied state.
    apply_results = await bundle_store.apply_bundle_to_capabilities(manifest, orchestrator)
    assignments = tuple(apply_results)
    choice = bundle_store.mark_bundle_chosen(
        canonical, npu_opt_in=npu_opt_in, assignments=assignments
    )

    return {
        "ok": True,
        "choice": choice.to_dict(),
        "manifest": manifest.bundle.to_dict(),
        "applied": apply_results,
    }


__all__ = ["router"]
