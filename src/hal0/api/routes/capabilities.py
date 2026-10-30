"""HTTP routes for the dashboard's Capability slots section.

Mounted under ``/api/capabilities`` (see :mod:`hal0.api.__init__`):

  - ``GET  /api/capabilities`` — backends + per-slot catalogs + persisted
    selections enriched with live slot status.
  - ``POST /api/capabilities/{slot}/{child}`` — partial selection update
    that reconciles slot lifecycle via :class:`CapabilityOrchestrator`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query, Request

from hal0.api._audit import record_action
from hal0.api.deps import CapabilityOrchestratorDep
from hal0.capabilities.orchestrator import LEGAL_SLOTS, legal_children
from hal0.errors import BadRequest

router = APIRouter()

#: Capability slots whose selection feeds OpenWebUI's dynamic env blocks
#: (RAG embeddings / image generation — see hal0.openwebui.wiring). "voice"
#: is excluded: STT/TTS are prewired unconditionally in env_writer's static
#: defaults, so a voice apply never changes what OpenWebUI needs re-rendered.
_OWUI_WIRED_SLOTS = frozenset({"embed", "img"})


#: Top-level payload sections ``?section=`` can select; the settle keys below
#: ride along with every selection.
_SECTIONS = ("backends", "catalogs", "selections")
_ALWAYS_KEPT = ("backends_settled", "backends_retry_in_s")


def _summarize_catalog_entry(entry: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"id": entry.get("id")}
    if "display_name" in entry:
        out["display_name"] = entry["display_name"]
    out["downloaded"] = any(b.get("downloaded") for b in entry.get("backends") or [])
    return out


def narrow_capabilities(
    state: dict[str, Any], *, section: str | None = None, summary: bool | None = None
) -> dict[str, Any]:
    """Select sections of, and optionally summarize, the capabilities payload.

    Pure: ``state`` is not mutated. ``section`` is a comma list of
    ``backends`` / ``catalogs`` / ``selections``; only those top-level keys
    are returned, plus ``backends_settled`` and ``backends_retry_in_s``
    (always kept). An unknown name raises ``capability.unknown_section``.

    ``summary`` (true) reduces every catalog child's entry list
    (``catalogs[slot][child]``) to ``{"count": N, "entries": [...]}``. Each
    summarized entry keeps its identity keys: ``id``, ``display_name`` (only
    when the entry has one) and ``downloaded`` (true when any of the entry's
    backends has the weights). Backend rows, selections and the settle keys
    are left unchanged.
    """
    out = state
    names = [n for n in (p.strip() for p in (section or "").split(",")) if n]
    for name in names:
        if name not in _SECTIONS:
            raise BadRequest(
                f"unknown capabilities section {name!r}",
                code="capability.unknown_section",
                details={"section": name, "legal": list(_SECTIONS)},
            )
    if names:
        keep = {*names, *_ALWAYS_KEPT}
        out = {k: v for k, v in state.items() if k in keep}
    if summary and "catalogs" in out:
        out = {
            **out,
            "catalogs": {
                slot: {
                    child: {
                        "count": len(entries),
                        "entries": [_summarize_catalog_entry(e) for e in entries],
                    }
                    for child, entries in children.items()
                }
                for slot, children in out["catalogs"].items()
            },
        }
    return out


@router.get("")
async def get_capabilities(
    orchestrator: CapabilityOrchestratorDep,
    section: str | None = Query(
        None, description="Comma list of backends, catalogs, selections to return"
    ),
    summary: bool | None = Query(
        None, description="Reduce each catalog's entries to id, display_name and downloaded"
    ),
) -> dict[str, Any]:
    """Return the full dashboard payload.

    Shape::

        {
          "backends": [{...}],
          "backends_settled": true,
          "backends_retry_in_s": 0,
          "catalogs": { "embed": {...}, "voice": {...}, "img": {...} },
          "selections": { "embed": {...}, "voice": {...}, "img": {...} }
        }

    ``backends_settled`` is False while the FLM-image probe has no answer
    yet (shortly after boot, or after an FLM pull's reset), so ``backends``
    may still change; the dashboard re-polls until it is True (#1974).
    ``backends_retry_in_s`` is the whole seconds until the next probe is due:
    0 while the cache is cold, a probe is in flight, or the answer is settled;
    the rest of the 30 s retry window while only an unanswerable result is
    held. The dashboard re-polls every ``max(2, backends_retry_in_s)`` seconds
    while unsettled.

    ``section`` and ``summary`` shrink the payload (see
    :func:`narrow_capabilities`); with neither, the state is returned as is.
    """
    state = await orchestrator.get_state()
    if section is None and summary is None:
        return state
    return narrow_capabilities(state, section=section, summary=summary)


@router.post("/{slot}/{child}")
async def apply_capability(
    slot: str,
    child: str,
    request: Request,
    orchestrator: CapabilityOrchestratorDep,
    background_tasks: BackgroundTasks,
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
    if slot in _OWUI_WIRED_SLOTS:
        from hal0.components.openwebui_arm import reconcile_openwebui_env_background

        # Runs after the response is sent — re-rendering OpenWebUI's env
        # (and restarting it, only if that render actually changed
        # something) is not this endpoint's own result.
        background_tasks.add_task(reconcile_openwebui_env_background)
    return {"ok": True, "selection": selection}


__all__ = ["router"]
