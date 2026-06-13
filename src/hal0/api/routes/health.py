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

import os
import shutil
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import Response

from hal0 import __version__
from hal0.config import paths

log = structlog.get_logger(__name__)

router = APIRouter()

# Below this free-space floor a disk check reports "degraded" (not down —
# hal0 still serves, but model pulls / state writes are at risk).
_DISK_FREE_FLOOR_MB = 500


def _disk_free_mb(path: Path) -> int:
    """Free MiB on the filesystem hosting ``path`` (0 if unavailable).

    Walks up to the first existing parent so a not-yet-created state dir
    on a fresh install still reports the underlying filesystem's space.
    """
    try:
        target = path
        while not target.exists() and target != target.parent:
            target = target.parent
        return shutil.disk_usage(str(target)).free // (1024 * 1024)
    except OSError:
        return 0


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


@router.get("/health")
async def health() -> dict[str, Any]:
    """Lightweight liveness probe.

    Returns 200 the moment the API event loop is serving — deliberately
    does NO slot-manager, upstream, or disk work, so first-run consumers
    can poll it during the bootstrap window before any slot exists. Three
    of them hit ``/api/health``: the post-install hello in
    ``installer/install.sh``, the agent readiness wait in
    :mod:`hal0.cli.agent_shim`, and the ``hal0-agent@`` systemd watchdog.
    Until this route existed they all got a 404 (the API only served
    ``/api/status``), which surfaced to operators as a false
    "API not responding" at the end of every install.

    Deep health (disk / slots / event bus) lives at ``/api/health/system``;
    the dashboard summary at ``/api/status``.
    """
    return {"status": "ok", "name": "hal0", "version": __version__}


@router.get("/health/system")
async def health_system(request: Request) -> dict[str, Any]:
    """Deep health: disk headroom, slot manager, event bus.

    Always returns HTTP 200 with an honest payload — the dashboard reads
    ``status`` (``ok`` | ``degraded``) and the per-check ``checks`` map
    rather than relying on the HTTP status, so a single soft failure
    (low disk, slot manager not yet wired) surfaces without 5xx-ing the
    whole liveness poll.
    """
    checks: dict[str, Any] = {}
    degraded = False

    # ── disk headroom on the state + config roots ───────────────────────
    # HAL0_HOME (when set) reparents both roots, so checking var_lib()
    # covers the dev/test sandbox AND the production /var/lib/hal0 path.
    for label, root in (("state", paths.var_lib()), ("config", paths.etc())):
        free_mb = _disk_free_mb(root)
        ok = free_mb >= _DISK_FREE_FLOOR_MB
        checks[f"disk_{label}"] = {
            "ok": ok,
            "free_mb": free_mb,
            "floor_mb": _DISK_FREE_FLOOR_MB,
            "path": str(root),
        }
        degraded = degraded or not ok

    # ── slot manager responsive ─────────────────────────────────────────
    sm = getattr(request.app.state, "slot_manager", None)
    if sm is None:
        checks["slot_manager"] = {"ok": False, "detail": "not wired"}
        degraded = True
    else:
        try:
            slots = await sm.list()
            checks["slot_manager"] = {"ok": True, "slots": len(slots)}
        except Exception as exc:
            checks["slot_manager"] = {"ok": False, "detail": str(exc)}
            degraded = True

    # ── event bus alive ─────────────────────────────────────────────────
    event_bus = getattr(request.app.state, "events", None)
    checks["event_bus"] = {"ok": event_bus is not None}
    degraded = degraded or event_bus is None

    return {
        "status": "degraded" if degraded else "ok",
        "checks": checks,
    }


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
async def list_features(request: Request) -> dict[str, Any]:
    """Runtime feature gates the dashboard branches on.

    Flat ``feature → bool | str`` map:

      - ``comfyui_switchover``: image-gen engine switchover is unlocked
        (``HAL0_COMFYUI_SWITCHOVER_ENABLED=1``).
      - ``memory``: a memory provider is wired (HAL0_MEMORY_ENABLED + a
        successful init).
      - ``memory_engine``: the configured engine name (``hindsight`` |
        ``cognee`` | ``mem0`` | …) — a string, not a bool.
      - ``npu``: an NPU was detected by the (cached) hardware probe.
      - ``mcp_supervisor``: the MCP process supervisor (start/stop/
        restart) — not implemented yet (pending ADR-0015), always false.
    """
    features: dict[str, Any] = {
        "comfyui_switchover": os.environ.get("HAL0_COMFYUI_SWITCHOVER_ENABLED", "") == "1",
        "memory": getattr(request.app.state, "memory_provider", None) is not None,
        "mcp_supervisor": False,
    }

    # memory engine name — read from hal0.toml; default to the schema
    # default rather than 500-ing the whole feature map on a parse error.
    try:
        from hal0.config.loader import load_hal0_config

        features["memory_engine"] = load_hal0_config().memory.engine
    except Exception:
        features["memory_engine"] = "unknown"

    # NPU presence via the cached install-time probe (cheap: reads
    # hardware.json, never shells out on the request path).
    try:
        from hal0.config.loader import load_hardware_info

        features["npu"] = bool(load_hardware_info().npu.present)
    except Exception:
        features["npu"] = False

    return features
