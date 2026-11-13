"""``GET /api/memory/health``: does memory need the operator, and what first?

The summary behind the Memory area's Health tab (ADR-0058, decision 12). The
facts come from :mod:`hal0.api.routes._memory_health_collect`, at a bounded
cost and cached for ten seconds; :func:`hal0.memory.health_path.health_path`
orders them into steps. The route reports and never repairs, and it answers
200 whatever state the engine is in.

Response::

    {enabled: true,
     verdict: "clear" | "attention" | "unreachable",
     checked_at, partial: [section names],
     engine: {reachable, version},
     extraction: {slot, mode, slot_resolves, llm_reachable, concurrency,
                  failures_6h, top_errors: [{message, count}]},
     embedder: {recorded: {provider, model}, configured: {provider, model},
                mixed, needs_switch, conflict, informational},
     queue: {waiting, failed, oldest_waiting_s, consolidation_pending,
             consolidation_failed,
             by_bank: [{bank_id, waiting, failed, oldest_waiting_s}], truncated},
     knowledge: {pages, pages_stale, models, models_stale, refresh_failed,
                 last_success_at},
     banks: {layout: "legacy_unified" | "legacy_split" | "two_bank", total, facts,
             list: [{bank_id, facts, documents}]},
     writes: null,
     steps: [{id, state: "current" | "waiting" | "clear", waits_on,
              reason_code, figures}]}

``partial`` names the sections whose reads did not all finish; their figures
are what was read, and the verdict is decided from them. With the engine
unreachable every section but ``engine`` is ``null`` and the one step is
``engine``. With memory off, or on an engine with no admin surface, the body
is ``{enabled, reason, checked_at, verdict: null, partial: [], steps: []}``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

from hal0.api.routes import _memory_health_collect as _collect
from hal0.config.loader import load_hal0_config
from hal0.memory.health_path import health_path

router = APIRouter()

REASON_MEMORY_DISABLED = "memory_disabled"
REASON_ENGINE_UNSUPPORTED = "engine_unsupported"


def _no_path(enabled: bool, reason: str) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "reason": reason,
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "verdict": None,
        "partial": [],
        "steps": [],
    }


def _engine_is_pgvector() -> bool:
    try:
        engine = getattr(load_hal0_config().memory, "engine", "hindsight")
    except Exception:
        return False
    return str(engine or "hindsight").lower() == "pgvector"


@router.get("/health")
async def memory_health(request: Request) -> dict[str, Any]:
    provider = getattr(request.app.state, "memory_provider", None)
    if provider is None:
        # [memory].enabled = false, or the provider failed to construct. The
        # same test GET /api/memory/engine reports as ``enabled: false``.
        return _no_path(False, REASON_MEMORY_DISABLED)
    if getattr(provider, "hindsight_client", None) is None and _engine_is_pgvector():
        # Configured onto the in-process engine: there are no banks,
        # operations or knowledge pages to report on. Without that setting a
        # missing client is the boot fallback, which the collector reports as
        # an unreachable engine.
        return _no_path(True, REASON_ENGINE_UNSUPPORTED)

    sections, facts = await _collect.collect_cached(request)
    path = health_path(facts)
    return {
        "enabled": True,
        "verdict": path.verdict,
        **sections,
        "steps": [step.as_dict() for step in path.steps],
    }


__all__ = ["router"]
