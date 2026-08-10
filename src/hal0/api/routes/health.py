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

import contextlib
import shutil
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import Response

from hal0 import __version__
from hal0.config import paths
from hal0.slots.state import SlotState

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


def _memory_degraded(request: Request) -> bool | None:
    """Return the memory degraded state for /api/status.

    True  → memory enabled but NOT healthy. Two shapes, deliberately reported
            as one flag: the boot-degrade ladder swapped in the volatile
            in-memory fallback (Hindsight was down at boot), OR the live
            Hindsight daemon has stopped answering since boot (#1301 — the
            provider tracks that itself, so this stays true after the boot
            probe's answer goes stale).
    False → memory enabled and the engine is answering.
    None  → memory is disabled (no provider wired).
    """
    provider = getattr(request.app.state, "memory_provider", None)
    if provider is None:
        return None
    return bool(getattr(provider, "degraded", False))


async def _extraction_target_resolves(request: Request, provider: Any) -> bool | None:
    """Does the memory engine's configured extraction slot resolve to a
    model-bound llm slot right now? (#1792)

    Hindsight's native fact-extraction calls back into hal0's own
    OpenAI-compat dispatcher (``HINDSIGHT_API_LLM_MODEL=hal0/<slot>``); on a
    fresh install every llm slot ships model-less (WS-E #1107), so that call
    404s ``dispatch.no_route`` until the operator loads a model — a
    deterministic, hal0-side fact, not something that needs to be inferred
    from Hindsight's own error text.

    Returns ``None`` when this can't be determined (no slot manager wired —
    e.g. a test app that bypasses the normal lifespan, or a provider with no
    ``extraction_slot`` of its own): callers must treat that as "unknown",
    never as evidence either way.
    """
    if getattr(request.app.state, "slot_manager", None) is None:
        return None
    extraction_slot = getattr(provider, "extraction_slot", None)
    if not extraction_slot:
        return None
    from hal0.api.routes.memory import _enabled_llm_slots

    try:
        available = await _enabled_llm_slots(request)
    except Exception:  # pragma: no cover — defensive, matches _enabled_llm_slots' own fail-soft
        return None
    return extraction_slot in available


async def _memory_write_health(request: Request) -> dict[str, Any] | None:
    """Retain-pipeline health for /api/status, or None when unavailable.

    Deliberately a SECOND signal rather than a widening of
    :func:`_memory_degraded` (#1420). That flag means "the daemon is
    answering", and on the box that produced the issue it was correct: the
    daemon accepted every retain with a ``200`` + ``operation_id`` and served
    recalls fine, while fact extraction failed asynchronously and nothing
    durable had landed in 8 days. Conflating the two would break #1301's
    contract and report the read path — which genuinely worked — as broken.

    None when memory is off, or when the wired provider has no retain pipeline
    to report on (the volatile PgVector fallback, a third-party provider). A
    missing signal must never read as a green one.

    Fail-soft: the probe behind this is TTL-cached inside the provider and
    swallows engine errors, and any unexpected raise degrades to None rather
    than 500ing the dashboard's poll.

    #1792: when the engine's operation counters show writes degraded AND the
    configured extraction slot has no model bound yet, that is the ENTIRE
    explanation — every failure in that window is the same dispatch.no_route
    404, not an operator-actionable outage — so this downgrades the verdict
    to a "waiting" state instead of FAILING. Conversely, once the slot
    resolves, this opportunistically kicks a bounded auto-retry sweep so
    whatever dead-lettered during the window recovers without a manual
    ``hal0 memory ops retry``.
    """
    provider = getattr(request.app.state, "memory_provider", None)
    if provider is None:
        return None
    probe = getattr(provider, "write_health", None)
    if probe is None:
        return None
    try:
        health = await probe()
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("hal0.memory.write_health_failed", error=str(exc))
        return None
    if not isinstance(health, dict):
        return None

    resolves = await _extraction_target_resolves(request, provider)
    if resolves is False and health.get("degraded"):
        health = dict(health)
        health["degraded"] = False
        health["reason"] = "no_chat_model"
        health["waiting_on"] = "chat_model"
    elif resolves is True:
        retry = getattr(provider, "maybe_auto_retry_dead_letters", None)
        if retry is not None:
            try:
                retried = await retry()
            except Exception as exc:  # pragma: no cover — defensive, never blocks /api/status
                log.warning("hal0.memory.auto_retry_dead_letters_failed", error=str(exc))
                retried = None
            if retried:
                with contextlib.suppress(Exception):
                    fresh = await probe(max_age_s=0)
                    if isinstance(fresh, dict):
                        health = fresh
    return health


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
        overlay_cached_enrichment,
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
    # These entries are bare (no container probe — /api/status must stay cheap).
    # Overlay list_slots' last-good container state so the dashboard union sees
    # a coherent dot + metrics instead of flickering back to the FSM-only view
    # whenever this poll beats the slower /api/slots one. Pure dict overlay.
    real_entries = overlay_cached_enrichment(request, real_entries)
    real_names = {entry["name"] for entry in real_entries}

    slot_list: list[dict[str, Any]] = list(real_entries)
    loaded_models = await _loaded_models(request)
    for entry in _synthesize_slots_from_upstreams(request, loaded_models=loaded_models):
        if entry["name"] not in real_names:
            slot_list.append(entry)

    upstream_summary = [{"name": u.name, "kind": u.kind, "url": u.url} for u in upstreams.list()]
    memory_write_health = await _memory_write_health(request)

    return {
        "name": "hal0",
        "version": __version__,
        "status": "ok",
        "hardware": None,  # populated by /api/hardware on demand
        "slots": slot_list,
        "upstreams": upstream_summary,
        # Single source of truth for whether the memory subsystem is live
        # (gated by [memory].enabled at create_app — see 'hal0 memory
        # enable'/'disable'). The dashboard reads this to show/hide the
        # Agent → Memory nav so the UI and the backend can never disagree.
        # Reflects the real wrapper, so an init failure also reads as off.
        "memory_enabled": getattr(request.app.state, "memory_provider", None) is not None,
        # True  → memory is enabled but running on the volatile in-memory
        #         PgVectorProvider fallback (writes will be lost on restart).
        # False → memory is enabled and using a real durable provider.
        # None  → memory is disabled ([memory].enabled=false or init failed).
        "memory_degraded": _memory_degraded(request),
        # #1420: a SECOND, independent signal for the retain pipeline. The
        # flag above tracks daemon reachability only, and a reachable daemon
        # accepts a retain into a queue whose LLM extraction step can be dead
        # — reads keep working while every write is silently dropped.
        # True  → writes were recently observed to fail (raised retain, or the
        #         engine's failed-operation counter climbing).
        # False → the retain pipeline looks healthy — OR (#1792) it's in the
        #         expected no-chat-model window on a fresh install, in which
        #         case ``memory_write_health.waiting_on == "chat_model"``.
        # None  → memory disabled, or the provider has no retain pipeline.
        "memory_write_degraded": (
            None if memory_write_health is None else bool(memory_write_health.get("degraded"))
        ),
        # Operator detail for `hal0 memory status`: the reason plus the
        # engine's own failed/pending/processing operation counts — the only
        # thing on this surface that can tell a backlogged box from a clean one.
        "memory_write_health": memory_write_health,
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
            # B2: a slot stuck in ERROR is a real degradation — the previous
            # check reported ok=True regardless, so a systemd-FAILED slot
            # rendered the whole system "ok". Surface the errored slots so the
            # dashboard chip + tooltip can tell the truth.
            errored = [s.name for s in slots if s.state == SlotState.ERROR]
            sm_ok = not errored
            checks["slot_manager"] = {
                "ok": sm_ok,
                "slots": len(slots),
                "errored": errored,
            }
            degraded = degraded or not sm_ok
        except Exception as exc:
            checks["slot_manager"] = {"ok": False, "detail": str(exc)}
            degraded = True

    # ── event bus alive ─────────────────────────────────────────────────
    event_bus = getattr(request.app.state, "events", None)
    checks["event_bus"] = {"ok": event_bus is not None}
    degraded = degraded or event_bus is None

    # ── MCP servers mounted ─────────────────────────────────────────────
    # ``create_app`` deliberately survives a mount failure so the API still
    # serves, which means nothing else would ever tell an operator that the
    # agent control surface is gone. A fastapi minor bump once silently emptied
    # the admin route map and unmounted /mcp/admin + /mcp/memory for 21 boots
    # while every health endpoint reported ``ok``. Surface it here.
    mount_error = getattr(request.app.state, "mcp_mount_error", None)
    servers = sorted(getattr(request.app.state, "mcp_servers", None) or {})
    mcp_ok = not mount_error and bool(servers)
    checks["mcp_mount"] = {"ok": mcp_ok, "servers": servers}
    if mount_error:
        checks["mcp_mount"]["detail"] = str(mount_error)
    elif not servers:
        checks["mcp_mount"]["detail"] = "no MCP servers mounted"
    degraded = degraded or not mcp_ok

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

      - ``comfyui_switchover``: image-gen engine switchover route is present.
      - ``memory``: a memory provider is wired ([memory].enabled + a
        successful init).
      - ``memory_engine``: the configured engine name (``hindsight`` |
        ``pgvector`` | ``mem0`` | …) — a string, not a bool.
      - ``npu``: an NPU was detected by the (cached) hardware probe.
      - ``mcp_supervisor``: the MCP process supervisor (start/stop/
        restart) — not implemented yet, always false.
    """
    features: dict[str, Any] = {
        "comfyui_switchover": True,
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
