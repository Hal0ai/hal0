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

    # Merge real SlotManager-backed entries with synthetic upstream-backed
    # ones — same shape /api/slots returns.  Without this, dynamically
    # created slots ("hal0 slot create" or the UI New Slot modal) don't
    # appear in the dashboard's polled view because the synthesise path
    # only knows about upstreams; they only show up after a page reload
    # picks them up via /api/slots directly.
    from hal0.api.routes.slots import (
        _get_slot_manager,
        _slot_to_dict,
        _synthesize_slots_from_upstreams,
    )

    try:
        sm = _get_slot_manager(request)
        real_slots = await sm.list()
        real_entries = [_slot_to_dict(s, request) for s in real_slots]
    except Exception:
        # If SlotManager isn't wired (test paths bypassing lifespan,
        # bootstrap window), fall back to synthetic-only so /api/status
        # still serves something useful instead of 500-ing the dashboard.
        real_entries = []
    real_names = {entry["name"] for entry in real_entries}

    slot_list: list[dict[str, Any]] = list(real_entries)
    for entry in _synthesize_slots_from_upstreams(request):
        if entry["name"] not in real_names:
            slot_list.append(entry)

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
