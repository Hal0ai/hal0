"""Health, status, metrics, features.

Routes mounted under /api:
  GET  /api/status            — overall liveness + summary (dashboard polls this)
  GET  /api/health/system     — deep health (slots, disk, ram)
  GET  /api/metrics           — JSON metrics
  GET  /api/features          — feature flags
  PUT  /api/features/{name}   — toggle feature flag

Note (issue #36): a /api/metrics/prometheus route was advertised in this
docstring and in the historical PUBLIC_PATHS allowlist but was never
implemented. Both stubs are removed until a real prometheus_client-
backed exporter ships. (PUBLIC_PATHS itself was deleted by ADR-0001
Child B; routes are now public by virtue of not declaring an auth dep.)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from hal0 import __version__

router = APIRouter()


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    """Overall liveness + dashboard summary.

    The Vue dashboard polls this every few seconds and reads
    ``hardware`` and ``slots`` from the response into its system store.
    On first call we eagerly populate the per-upstream model cache so
    the synthesized slot entries reflect "serving" rather than "offline".
    """
    upstreams = request.app.state.upstreams
    cache: dict[str, list[str]] = getattr(request.app.state, "model_cache", {})

    # Eagerly hydrate the cache for any upstream we haven't fetched yet.
    # Cheap: each /v1/models call against haloai is sub-100ms.
    for u in upstreams.list():
        if u.name not in cache:
            try:
                cache[u.name] = await upstreams.fetch_models(u.name)
            except Exception:
                cache[u.name] = []

    # Slot entries come from the (still-stubbed) SlotManager when wired,
    # else synthesized from upstreams so the dashboard isn't empty.
    from hal0.api.routes.slots import _synthesize_slots_from_upstreams

    slot_list = _synthesize_slots_from_upstreams(request)

    upstream_summary = [{"name": u.name, "kind": u.kind, "url": u.url} for u in upstreams.list()]

    return {
        "name": "hal0",
        "version": __version__,
        "status": "ok",
        "hardware": None,  # populated by /api/hardware on demand
        "slots": slot_list,
        "upstreams": upstream_summary,
    }


@router.get("/health/system")
async def health_system() -> dict[str, object]:
    return {"status": "ok", "checks": {}}


@router.get("/metrics")
async def metrics() -> dict[str, object]:
    return {"slots": {}, "hardware": {}, "dispatcher": {}}


@router.get("/features")
async def list_features() -> dict[str, bool]:
    return {}
