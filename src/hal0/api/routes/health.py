"""Health, status, metrics, features.

Routes mounted under /api:
  GET  /api/status              — overall liveness + summary (dashboard polls this)
  GET  /api/health/system       — deep health (slots, disk, ram)
  GET  /api/metrics             — JSON metrics
  GET  /api/metrics/prometheus  — Prometheus exposition (slot lifecycle state)
  GET  /api/features            — feature flags
  PUT  /api/features/{name}     — toggle feature flag

``/api/metrics/prometheus`` renders the per-slot lifecycle exposition
from :mod:`hal0.slots.metrics` (``hal0_slot_up`` / ``hal0_slot_state``
/ ``hal0_slots_ready_total``). Per-slot llama-server native metrics are
a follow-up (scrape each container's own ``/metrics``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

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
        _loaded_models,
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
    loaded_models = await _loaded_models(request)
    for entry in _synthesize_slots_from_upstreams(request, loaded_models=loaded_models):
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
        # 0.4: single source of truth for whether the memory subsystem is
        # live (gated by HAL0_MEMORY_ENABLED at create_app). The dashboard
        # reads this to show/hide the Agent → Memory nav so the UI and the
        # backend can never disagree. Reflects the real wrapper, so an init
        # failure also reads as off.
        "memory_enabled": getattr(request.app.state, "memory_provider", None) is not None,
    }


@router.get("/health/system")
async def health_system() -> dict[str, object]:
    return {"status": "ok", "checks": {}}


@router.get("/metrics")
async def metrics() -> dict[str, object]:
    return {"slots": {}, "hardware": {}, "dispatcher": {}}


@router.get("/metrics/prometheus")
async def metrics_prometheus(request: Request) -> Response:
    """Prometheus text-exposition surface over slot lifecycle state.

    Rendered by :func:`hal0.slots.metrics.render_slot_metrics` from the
    SlotManager's snapshots. When the SlotManager isn't wired (tests
    bypassing lifespan), returns an empty exposition body rather than
    500 — Prometheus treats that as "no series", which is the correct
    "no data yet" state.

    Public route by convention (no auth dependency declared). Operators
    behind a reverse proxy should restrict ``/api/metrics/prometheus``
    at the edge if they want to limit scraper access; hal0-internal
    enforcement would block standard Prometheus scrapers that don't
    speak hal0's bearer-token auth.
    """
    from hal0.slots.metrics import render_slot_metrics

    sm = getattr(request.app.state, "slot_manager", None)
    if sm is None:
        body = ""
    else:
        try:
            slots = await sm.list()
        except Exception:
            slots = []
        body = render_slot_metrics(slots)
    # Prometheus text format 0.0.4: ``text/plain; version=0.0.4; charset=utf-8``.
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")


@router.get("/features")
async def list_features() -> dict[str, bool]:
    return {}
