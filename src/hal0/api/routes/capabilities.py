"""HTTP routes for the dashboard's Capability slots section.

Mounted under ``/api/capabilities`` (see :mod:`hal0.api.__init__`):

  - ``GET  /api/capabilities`` — backends + per-slot catalogs + persisted
    selections enriched with live slot status.
  - ``POST /api/capabilities/{slot}/{child}`` — partial selection update
    that reconciles slot lifecycle via :class:`CapabilityOrchestrator`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from hal0.api._audit import record_action
from hal0.api.deps import CapabilityOrchestratorDep
from hal0.capabilities.orchestrator import LEGAL_SLOTS, legal_children
from hal0.errors import BadRequest

router = APIRouter()


@router.get("")
async def get_capabilities(orchestrator: CapabilityOrchestratorDep) -> dict[str, Any]:
    """Return the full dashboard payload.

    Shape::

        {
          "backends": [{...}],
          "catalogs": { "embed": {...}, "voice": {...}, "img": {...} },
          "selections": { "embed": {...}, "voice": {...}, "img": {...} }
        }
    """
    return await orchestrator.get_state()


@router.post("/{slot}/{child}")
async def apply_capability(
    slot: str,
    child: str,
    request: Request,
    orchestrator: CapabilityOrchestratorDep,
) -> dict[str, Any]:
    """Apply a partial selection update to one (slot, child) pair.

    Body: any subset of ``{ "backend", "provider", "model", "enabled" }``.
    Returns ``{ "ok": true, "selection": { ...current selection... } }``.
    """
    if slot not in LEGAL_SLOTS:
        raise BadRequest(
            f"unknown capability slot {slot!r}",
            code="capability.unknown_slot",
            details={"slot": slot, "legal": list(LEGAL_SLOTS)},
        )
    if child not in legal_children(slot):
        raise BadRequest(
            f"child {child!r} not valid for capability {slot!r}",
            code="capability.unknown_child",
            details={"slot": slot, "child": child, "legal": legal_children(slot)},
        )

    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest(
            "request body must be valid JSON",
            code="request.invalid_json",
            details={"error": str(exc)},
        ) from exc
    if not isinstance(body, dict):
        raise BadRequest(
            "request body must be a JSON object",
            code="request.not_an_object",
        )

    # Whitelist the keys we accept; unknown keys surface as a 400 rather
    # than silently sneaking through the orchestrator's merge.
    allowed_keys = {"backend", "provider", "model", "enabled"}
    extras = set(body.keys()) - allowed_keys
    if extras:
        raise BadRequest(
            f"unexpected keys in body: {sorted(extras)}",
            code="capability.unknown_fields",
            details={"allowed": sorted(allowed_keys), "unexpected": sorted(extras)},
        )

    async with record_action(
        request,
        category="capability",
        action="capability.apply",
        target=f"{slot}/{child}",
    ) as rec:
        selection = await orchestrator.apply(slot, child, body)
        rec.after = {
            "slot": slot,
            "child": child,
            **{k: body[k] for k in ("backend", "provider", "model", "enabled") if k in body},
        }
    return {"ok": True, "selection": selection}


__all__ = ["router"]
