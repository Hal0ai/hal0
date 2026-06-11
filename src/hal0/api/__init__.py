"""FastAPI application factory.

The module-level `app` exists so `uvicorn hal0.api:app` works directly.
For tests and alternate entrypoints, call `create_app()`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from fastapi import FastAPI

if TYPE_CHECKING:
    from hal0.lemonade.idle import IdleDriver
    from hal0.lemonade.metrics_shim import MetricsShim

from hal0 import __version__
from hal0.api.agents import (
    budget as agents_budget_routes,
)
from hal0.api.agents import (
    memory_stats as agents_memory_stats_routes,
)
from hal0.api.agents import (
    personas as agents_personas_routes,
)
from hal0.api.agents import (
    restart as agents_restart_routes,
)
from hal0.api.agents.chat_proxy import router as chat_proxy_router
from hal0.api.middleware import error_codes, log_scrub, request_id
from hal0.api.openrouter import router as openrouter_auth_router
from hal0.api.plugins import router as plugin_manifest_router
from hal0.api.routes import (
    agents as agents_routes,
)
from hal0.api.routes import (
    approvals as approvals_routes,
)
from hal0.api.routes import (
    backends as backends_routes,
)
from hal0.api.routes import (
    bundles as bundles_routes,
)
from hal0.api.routes import (
    capabilities as capabilities_routes,
)
from hal0.api.routes import (
    comfyui,
    hardware,
    health,
    hf,
    images,
    installer,
    logs,
    models,
    npu,
    providers,
    settings,
    slots,
    updater,
    v1,
)
from hal0.api.routes import (
    config as config_routes,
)
from hal0.api.routes import (
    events as events_routes,
)
from hal0.api.routes import (
    journal as journal_routes,
)
from hal0.api.routes import (
    lemonade_admin as lemonade_admin_routes,
)
from hal0.api.routes import (
    lemonade_logs as lemonade_logs_routes,
)
from hal0.api.routes import (
    lemonade_proxy as lemonade_proxy_routes,
)
from hal0.api.routes import (
    mcp as mcp_routes,
)
from hal0.api.routes import (
    memory as memory_routes,
)
from hal0.api.routes import (
    profiles as profiles_routes,
)
from hal0.api.routes import (
    proxmox as proxmox_routes,
)
from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.config.loader import ConfigParseError, load_hal0_config, load_upstreams_config
from hal0.dispatcher.router import Dispatcher
from hal0.events import EventBus
from hal0.hardware.probe import HardwareProbe
from hal0.journal import LemondLogRing, start_lemond_bridge
from hal0.registry.discover import scan_and_register
from hal0.registry.store import ModelRegistry
from hal0.slots.manager import SlotManager
from hal0.upstreams.registry import Upstream, UpstreamRegistry

log = structlog.get_logger(__name__)


# Module-level cache for the composite ``hal0`` upstream's aggregated
# /v1/models response. Keyed by the upstream name; value is a tuple of
# (expires_monotonic, model_ids). The TTL (default 5s) keeps repeated
# ``/v1/models`` fans-out cheap during the cold-start race window
# (R4 H3) without making the catalogue stale enough that a freshly
# loaded slot stays invisible to Hermes for long. Use
# ``time.monotonic()`` rather than ``functools.lru_cache`` because the
# stdlib LRU has no time-based expiry.
_HAL0_MODEL_CACHE: dict[str, tuple[float, list[str]]] = {}
_HAL0_MODEL_CACHE_TTL_SECONDS = 5.0


def _hal0_model_cache_clear() -> None:
    """Punch the composite upstream's cached model list.

    Exposed so slot-swap / slot-restart paths can invalidate the cache
    when they know the next call will see a different model. Tests
    also call this to keep state isolated between cases.
    """
    _HAL0_MODEL_CACHE.clear()


async def _fetch_hal0_composite_models(
    upstream: Upstream,
    slot_manager: SlotManager,
    *,
    now: Callable[[], float] = time.monotonic,
    ttl_seconds: float = _HAL0_MODEL_CACHE_TTL_SECONDS,
) -> list[str]:
    """Aggregate every ready chat-capable slot's model id under one upstream.

    The composite ``hal0`` upstream replaces the previous per-slot
    autoregistration (R4 H2): Lemonade serialises chat loading on a
    single port, so ``chat`` and ``agent`` both produced
    ``Upstream(url="http://127.0.0.1:8001/v1")`` and ``/v1/models``
    deduplication credited whichever entry iterated first, leaving the
    other looking empty in the dashboard.

    The returned list is sorted + deduplicated and cached for
    ``ttl_seconds`` to keep the cold-start fan-out cheap while still
    picking up new slots within a handful of seconds.
    """
    cached = _HAL0_MODEL_CACHE.get(upstream.name)
    monotonic_now = now()
    if cached is not None and cached[0] > monotonic_now:
        return list(cached[1])

    try:
        cfgs = await slot_manager.iter_configs()
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("upstream.hal0_composite_iter_failed", error=str(exc))
        cfgs = []

    seen: set[str] = set()
    models: list[str] = []
    for cfg in cfgs:
        if (cfg.get("type") or "").lower() != "llm":
            continue
        # Slot TOML conventions vary: real on-disk TOMLs put the model id
        # under nested ``[model] default``; live /api/slots payloads
        # expose it as ``model_default``; test fixtures sometimes pass
        # ``model_id`` directly. Check every shape so the composite
        # listing works regardless of the entry's origin.
        model_section = cfg.get("model") or {}
        defaults = cfg.get("defaults") or {}
        model_id = (
            cfg.get("model_default")
            or cfg.get("model_id")
            or (model_section.get("default") if isinstance(model_section, dict) else None)
            or defaults.get("model")
            or ""
        )
        if not isinstance(model_id, str) or not model_id:
            continue
        if model_id in seen:
            continue
        seen.add(model_id)
        models.append(model_id)

    models.sort()
    _HAL0_MODEL_CACHE[upstream.name] = (monotonic_now + ttl_seconds, list(models))
    return models


def _slot_model_id(cfg: dict[str, Any]) -> str:
    """Extract a chat slot's configured model id from a raw config dict.

    Slot TOML conventions vary: real on-disk TOMLs put the model id under
    nested ``[model] default``; live /api/slots payloads expose it as
    ``model_default``; test fixtures sometimes pass ``model_id`` directly.
    Check every shape so callers work regardless of the entry's origin.
    Mirrors the lookup inlined in :func:`_fetch_hal0_composite_models`.
    """
    model_section = cfg.get("model") or {}
    defaults = cfg.get("defaults") or {}
    model_id = (
        cfg.get("model_default")
        or cfg.get("model_id")
        or (model_section.get("default") if isinstance(model_section, dict) else None)
        or defaults.get("model")
        or ""
    )
    return model_id if isinstance(model_id, str) else ""


def _coerce_ctx(raw: Any) -> int | None:
    """Coerce a context-size value to a positive int, or ``None``."""
    if raw is None:
        return None
    try:
        ctx = int(raw)
    except (TypeError, ValueError):
        return None
    return ctx if ctx > 0 else None


def _slot_ctx_size(
    cfg: dict[str, Any],
    model_registry: ModelRegistry | None = None,
    model_id: str = "",
) -> int | None:
    """Resolve a slot's context length.

    The on-disk slot TOMLs are inconsistent about the key name:
    ``agent.toml`` uses ``[model] ctx_size`` while ``utility.toml``
    uses ``[model] context_size`` and ``chat.toml`` pins neither. Read
    BOTH keys (plus a couple of flat shapes seen in live /api/slots
    payloads), then fall back to the model registry entry's
    ``defaults.context_size`` so a slot that doesn't pin a ctx still
    advertises the model's native window. Returns ``None`` only when no
    source yields a positive value.
    """
    model_section = cfg.get("model") or {}
    defaults = cfg.get("defaults") or {}

    # Probe order: nested [model] ctx_size / context_size, then flat keys,
    # then a nested defaults table (live payload shape).
    for source, keys in (
        (model_section, ("ctx_size", "context_size")),
        (cfg, ("ctx_size", "context_size")),
        (defaults, ("ctx_size", "context_size")),
    ):
        if not isinstance(source, dict):
            continue
        for key in keys:
            ctx = _coerce_ctx(source.get(key))
            if ctx is not None:
                return ctx

    # Registry fallback — the model's declared default context window.
    if model_registry is not None and model_id:
        try:
            entry = model_registry.get(model_id)
        except Exception:
            entry = None
        if entry is not None:
            entry_defaults = getattr(entry, "defaults", None)
            ctx = _coerce_ctx(getattr(entry_defaults, "context_size", None))
            if ctx is not None:
                return ctx
    return None


async def _loaded_model_ids(slot_manager: SlotManager) -> set[str] | None:
    """Return the set of model ids lemond currently reports as loaded.

    Probes lemond's ``/v1/health`` once (mirrors
    ``api.routes.slots._lemonade_state_enrichment``) and collects every
    ``model_name`` under ``loaded`` / ``all_models_loaded``. Returns
    ``None`` when the health probe can't be performed at all (no lemonade
    provider / unexpected error) so callers can decide how to degrade —
    distinct from an empty set, which means "lemond is up and nothing is
    loaded".
    """
    _ = slot_manager  # signature symmetry with the other slot helpers
    try:
        from hal0.lemonade.errors import LemonadeError
        from hal0.providers import lemonade_provider

        try:
            health = await lemonade_provider().client().health()
        except LemonadeError:
            return None
    except Exception:  # pragma: no cover — defensive (import/provider wiring)
        return None
    if not isinstance(health, dict):
        return None
    loaded: set[str] = set()
    for key in ("loaded", "all_models_loaded"):
        entries = health.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                name = entry.get("model_name")
                if isinstance(name, str) and name:
                    loaded.add(name)
    return loaded


async def hal0_slot_alias_models(
    slot_manager: SlotManager,
    model_registry: ModelRegistry,
    *,
    now: int | None = None,
) -> list[dict[str, Any]]:
    """Build OpenAI ``model`` objects for every LOADED chat slot, alias-addressed.

    Each enabled chat slot (``type == "llm"``) whose configured model is
    currently loaded in lemond surfaces as one model object whose ``id``
    is the slot **alias = slot name** (e.g. ``chat``, ``agent``,
    ``utility``). The alias is the stable handle: it does not change when
    the underlying model is swapped, so callers can pin a co-resident slot
    without tracking the GGUF filename.

    Fields:

    * ``id`` — slot name (the stable alias).
    * ``name`` — ``"<slot> · <model display name>"``; the display name is
      pulled from the model registry when the slot's model id is
      registered, falling back to the bare model id otherwise.
    * ``context_length`` — the slot's configured context window (reading
      either ``ctx_size`` or ``context_size`` from the slot TOML), falling
      back to the model registry entry's ``defaults.context_size``.
    * ``owned_by`` — ``"hal0"``.

    Slots that are disabled, lack a configured model, or whose model is
    not currently loaded in lemond are omitted. If the lemond health probe
    can't run at all, no alias entries are emitted (we refuse to advertise
    a slot we can't confirm is serving) — the composite ``hal0`` model
    list still carries the raw model ids for direct addressing.
    """
    created = int(time.time()) if now is None else now
    try:
        cfgs = await slot_manager.iter_configs()
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("v1.slot_alias_iter_failed", error=str(exc))
        return []

    loaded = await _loaded_model_ids(slot_manager)
    if loaded is None:
        # Can't confirm what's loaded → emit nothing rather than advertise
        # slots that may be cold. The composite still lists raw model ids.
        return []

    out: list[dict[str, Any]] = []
    for cfg in cfgs:
        if (cfg.get("type") or "").lower() != "llm":
            continue
        if cfg.get("enabled") is False:
            continue
        slot_name = str(cfg.get("name") or "").strip()
        if not slot_name:
            continue
        model_id = _slot_model_id(cfg)
        if not model_id:
            continue
        if model_id not in loaded:
            continue

        display = model_id
        try:
            entry = model_registry.get(model_id)
            registry_name = getattr(entry, "name", "")
            if isinstance(registry_name, str) and registry_name.strip():
                display = registry_name.strip()
        except Exception:
            # Model not in the registry (pulled via lemond, hand-loaded,
            # …) — fall back to the bare model id for the display label.
            display = model_id

        obj: dict[str, Any] = {
            "id": slot_name,
            "object": "model",
            "created": created,
            "owned_by": "hal0",
            "name": f"{slot_name} · {display}",
        }
        ctx = _slot_ctx_size(cfg, model_registry, model_id)
        if ctx is not None:
            obj["context_length"] = ctx
        out.append(obj)
    return out


async def hal0_chat_slot_alias_map(slot_manager: SlotManager) -> dict[str, str]:
    """Return ``{slot_alias: model_id}`` for enabled chat slots.

    The slot **alias** is the slot name (``chat`` / ``agent`` /
    ``utility``; back-compat: ``primary`` / ``agent-hermes``). Used by
    the ``/v1`` route layer to translate an
    alias-addressed request into the slot's configured model id before
    routing, so the request reaches lemond (which serves chat models by
    name) with the correct distinct model. This is a thin translation map,
    not a routing target — the chat slots are NOT independently addressable
    on their TOML ports.

    Best-effort: returns ``{}`` on any failure so the route layer forwards
    the request untranslated rather than 500ing. Disabled slots and slots
    with no configured model are skipped.
    """
    try:
        cfgs = await slot_manager.iter_configs()
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("v1.chat_slot_alias_map_iter_failed", error=str(exc))
        return {}
    out: dict[str, str] = {}
    for cfg in cfgs:
        if (cfg.get("type") or "").lower() != "llm":
            continue
        if cfg.get("enabled") is False:
            continue
        slot_name = str(cfg.get("name") or "").strip()
        if not slot_name:
            continue
        model_id = _slot_model_id(cfg)
        if model_id:
            out.setdefault(slot_name, model_id)
    # Inject back-compat aliases (primary → chat's model_id, agent-hermes →
    # agent's model_id) so requests using old slot names still reach the right
    # model after the rename. Use setdefault so a literal slot still named
    # "primary" on-disk (pre-migration) takes precedence.
    from hal0.slots.manager import SLOT_ALIASES

    for old_name, new_name in SLOT_ALIASES.items():
        if old_name not in out and new_name in out:
            out[old_name] = out[new_name]
    return out


async def hal0_llm_slot_views(
    slot_manager: SlotManager,
    model_registry: ModelRegistry | None = None,
) -> list[dict[str, Any]]:
    """Return one dict per enabled llm slot: {name, role, device, model_id, context_length}.

    Source for normalize.LiveSlotResolver's SlotView list. Mirrors
    hal0_chat_slot_alias_map's iteration but carries role + device + context.
    """
    try:
        cfgs = await slot_manager.iter_configs()
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("v1.llm_slot_views_iter_failed", error=str(exc))
        return []
    out: list[dict[str, Any]] = []
    for cfg in cfgs:
        if (cfg.get("type") or "").lower() != "llm":
            continue
        if cfg.get("enabled") is False:
            continue
        name = str(cfg.get("name") or "").strip()
        model_id = _slot_model_id(cfg)
        if not name or not model_id:
            continue
        out.append(
            {
                "name": name,
                "role": cfg.get("role"),
                "device": (cfg.get("device") or "").strip(),
                "model_id": model_id,
                "context_length": int(_slot_ctx_size(cfg, model_registry, model_id) or 0),
            }
        )
    return out


async def hal0_chat_slot_model_ids(slot_manager: SlotManager) -> set[str]:
    """Return the configured model ids of every enabled chat slot.

    Used by ``GET /v1/models`` to suppress raw chat model-id rows from the
    composite ``hal0`` upstream so each chat slot is represented exactly
    once — by its alias entry (see :func:`hal0_slot_alias_models`). Unlike
    the alias builder this does NOT filter on loaded state: a chat model
    that the composite advertises must be deduped regardless of whether
    it's currently warm, so the catalog never shows both an alias and a
    bare ``id=<model_id>`` row for the same slot.

    Best-effort: returns an empty set on any failure so the catalog
    degrades to "no dedup" rather than 500ing.
    """
    try:
        cfgs = await slot_manager.iter_configs()
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("v1.chat_slot_model_ids_iter_failed", error=str(exc))
        return set()
    out: set[str] = set()
    for cfg in cfgs:
        if (cfg.get("type") or "").lower() != "llm":
            continue
        if cfg.get("enabled") is False:
            continue
        model_id = _slot_model_id(cfg)
        if model_id:
            out.add(model_id)
    return out


async def _autoregister_slot_upstreams(
    registry: UpstreamRegistry,
    slot_manager: SlotManager,
) -> None:
    """Register a single composite ``hal0`` upstream.

    Lemonade serialises chat loading and serves EVERY chat model by name
    from one process (``127.0.0.1:13305``) with co-residency
    (``max_loaded_models``). The chat slots are therefore NOT independently
    addressable on their TOML ports — registering one ``kind="slot"``
    upstream per chat slot (pointed at those ports) produces dead targets
    and collisions (``chat`` + ``agent`` both pin ``port=8001``).
    So we register exactly ONE composite ``hal0`` upstream and let the
    existing lemonade fall-through serve chat models by name; per-slot
    addressing is handled by an alias → model-id rewrite in the dispatch
    path (see :meth:`Dispatcher.dispatch`), not by separate upstreams.

    The composite upstream:

    * Points at hal0-api's own ``/v1`` surface (``127.0.0.1:8080/v1``)
      so the dispatcher's prompt-cache + dispatch path stays in the
      loop instead of every consumer talking directly to the
      slot-local llama-server.
    * Advertises ALL chat-capable slot models through one
      ``/v1/models`` response (aggregated by
      :func:`_fetch_hal0_composite_models`).
    * Has its model cache invalidated whenever a slot swaps or
      restarts — see ``/api/slots/{name}/{swap,restart}``.

    Skipped if an explicit ``upstreams.toml`` entry already claims the
    name ``hal0`` so operator overrides win. Operator-defined real slot
    upstreams (any other names) are left untouched.
    """
    if registry.get("hal0") is not None:
        log.info("slots.autoregister_skipped", upstream="hal0", reason="already_registered")
        return
    registry.upsert(
        Upstream(
            name="hal0",
            kind="slot",
            url="http://127.0.0.1:8080/v1",
            slot_name=None,
            auth_style="none",
            warmup_strategy="none",
            advertise_models=True,
        )
    )
    log.info("slots.autoregistered_composite", upstream="hal0")
    # Prime the model cache so the first request after startup doesn't
    # have to pay the slot-iteration cost. Best-effort — failures are
    # already logged inside the fetch helper.
    upstream = registry.get("hal0")
    if upstream is not None:
        await _fetch_hal0_composite_models(upstream, slot_manager)


# ── FLM multiplex model seeding ────────────────────────────────────────────
# An FLM slot can serve up to three models from one process — the chat tag
# in ``model.default`` plus embed-gemma (when ``defaults.load_embed=true``)
# plus whisper-v3:turbo (when ``defaults.load_asr=true``). Those auxiliary
# models don't show up in FLM's ``/v1/models`` response (it only lists chat
# tags), so the dispatcher's passthrough cache never learns about them and
# routes ``model: "embed-gemma"`` to nowhere. Seed the cache explicitly.
_FLM_EMBED_TAG = "embed-gemma"
_FLM_ASR_TAG = "whisper-v3:turbo"


async def _refresh_model_cache_on_ready(
    event_bus: EventBus,
    upstreams: UpstreamRegistry,
    fetch_and_cache: Callable[[Upstream], Awaitable[list[str]]],
) -> None:
    """Re-fetch ``model_cache[slot]`` whenever a slot transitions to ready.

    The cache backs ``Dispatcher.dispatch`` Step 2 passthrough. When a slot's
    loaded GGUF changes (model swap, restart with a new config), the cache
    must follow — otherwise the dispatcher matches by stale ids and routes
    ``/v1/chat/completions`` to whichever slot last advertised that filename.
    SlotManager already emits ``slot.state`` events; subscribing here keeps
    the cache aligned without coupling the manager to app state.
    """
    async with event_bus.subscribe() as q:
        while True:
            event = await q.get()
            if event.get("type") != "slot.state":
                continue
            data = event.get("data") or {}
            if data.get("to") != "ready":
                continue
            slot_name = data.get("slot")
            if not isinstance(slot_name, str) or not slot_name:
                continue
            # Per-slot upstreams used to exist; now a single composite
            # ``hal0`` entry aggregates every chat-capable slot. When any
            # slot flips ready, punch the composite TTL cache so the
            # next /v1/models call rediscovers the new lineup. Fall back
            # to a slot-named lookup for backwards compatibility (tests
            # or operator-managed upstreams.toml that still mirror the
            # legacy layout).
            _hal0_model_cache_clear()
            upstream = upstreams.get(slot_name) or upstreams.get("hal0")
            if upstream is None:
                continue
            try:
                await fetch_and_cache(upstream)
            except Exception as exc:  # pragma: no cover — defensive
                log.warning(
                    "model_cache.refresh_failed",
                    slot=slot_name,
                    error=str(exc),
                )


async def _seed_multiplex_models(
    registry: UpstreamRegistry,
    slot_manager: SlotManager,
    model_cache: dict[str, list[str]],
) -> None:
    """Add FLM multiplex tags (embed-gemma, whisper-v3:turbo) to the model
    cache for slots whose config opts into the matching multiplex.

    Idempotent — appends only when missing. Runs after
    ``_autoregister_slot_upstreams``. Since the composite ``hal0``
    upstream replaces per-slot registrations (R4 H2), the multiplex
    tags are merged into the ``hal0`` cache bucket so the dispatcher's
    passthrough match still picks them up.
    """
    try:
        cfgs = await slot_manager.iter_configs()
    except Exception as exc:
        log.warning("slots.multiplex_seed_failed", error=str(exc))
        return
    bucket = model_cache.setdefault("hal0", [])
    for cfg in cfgs:
        name = cfg.get("name", "")
        provider = cfg.get("provider", "")
        if provider != "flm" or not name:
            continue
        defaults = cfg.get("defaults") or {}
        if defaults.get("load_embed") and _FLM_EMBED_TAG not in bucket:
            bucket.append(_FLM_EMBED_TAG)
            log.info("slots.multiplex_seeded", slot=name, model=_FLM_EMBED_TAG)
        if defaults.get("load_asr") and _FLM_ASR_TAG not in bucket:
            bucket.append(_FLM_ASR_TAG)
            log.info("slots.multiplex_seeded", slot=name, model=_FLM_ASR_TAG)


def _hydrate_upstreams(registry: UpstreamRegistry) -> None:
    """Populate the upstream registry from /etc/hal0/upstreams.toml.

    Missing file is fine — fresh installs have an empty registry until
    the user adds an upstream via the UI or `hal0 upstream add`.  Malformed
    files surface a typed ConfigParseError that propagates to the lifespan;
    we log+continue rather than crashing the API so the UI can still load
    and show the config error to the user.
    """
    try:
        cfg = load_upstreams_config()
    except ConfigParseError as exc:
        log.warning("upstreams.config_parse_failed", error=str(exc))
        return
    for entry in cfg.upstream:
        try:
            registry.upsert(
                Upstream(
                    name=entry.name,
                    kind=entry.kind,
                    url=entry.url,
                    auth_style=entry.auth_style,
                    auth_value_env=entry.auth_value_env,
                    timeout_seconds=entry.timeout_seconds,
                    slot_name=entry.slot_name,
                    warmup_strategy=entry.warmup_strategy,
                    advertise_models=entry.advertise_models,
                )
            )
        except Exception as exc:
            log.warning(
                "upstreams.entry_skipped",
                name=entry.name,
                error=str(exc),
                error_type=type(exc).__name__,
            )


async def _start_lemonade_metrics_shim(app: FastAPI) -> MetricsShim | None:
    """Start the Lemonade metrics shim (PR-12, ADR-0008 §3).

    Polls ``GET /v1/stats`` + ``GET /v1/health`` on a 5s cadence and
    holds the latest snapshot on ``app.state.lemonade_metrics_shim`` so
    the ``GET /api/metrics/prometheus`` route can read it synchronously
    without blocking on a fresh upstream call per scrape.

    Reuses the LemonadeClient attached by the idle driver — sharing the
    client matches the pattern in :mod:`hal0.dispatcher.router` and
    avoids opening a second connection pool against lemond. If the idle
    driver failed to start (no client on app.state), the shim is
    skipped: the shim is purely observability; a busted Lemonade config
    must not block API startup.

    Failures here never block lifespan progression — log + continue, the
    /api/metrics/prometheus endpoint will simply return an empty
    exposition body until the shim attaches successfully on a future
    restart.
    """
    client = getattr(app.state, "lemonade_client", None)
    if client is None:
        log.info("lemonade.metrics.skipped_no_client")
        return None
    try:
        from hal0.lemonade.metrics_shim import MetricsShim

        shim = MetricsShim(client)
        await shim.start()
        app.state.lemonade_metrics_shim = shim
        log.info("lemonade.metrics.shim_attached")
        return shim
    except Exception as exc:  # pragma: no cover — defensive
        log.warning(
            "lemonade.metrics.shim_start_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


def _build_idle_ttl_provider(
    slot_manager: SlotManager,
) -> Callable[[], dict[str, float]]:
    """Build the per-model idle-TTL provider for the IdleDriver (issue #414).

    Returns a callable the driver invokes once per tick to get the
    current ``lemond model_name`` → ``idle_timeout_s`` map, derived from
    each slot's ``[model] default`` and its ``idle_timeout_s``. Rebuilt
    every call so a config change (PUT slot config) is picked up on the
    next tick without restarting the driver.

    The driver's resolver is synchronous and runs inside the running
    event loop, so the provider delegates to ``SlotManager``'s
    synchronous TOML reader rather than awaiting ``iter_configs``. A
    model with ``idle_timeout_s == 0`` maps to 0, which the driver
    treats as "never evict". Unconfigured models aren't in the map and
    fall back to the driver's global default (300s).
    """

    def _provider() -> dict[str, float]:
        return slot_manager.idle_timeout_by_model()

    return _provider


async def _start_lemonade_idle_driver(
    app: FastAPI,
    slot_manager: SlotManager,
    *,
    global_idle_timeout_s: float = 300.0,
) -> IdleDriver | None:
    """Start the Lemonade idle-unload driver.

    v0.2 (ADR-0008 §1): Lemonade is the sole inference backend; this
    driver always starts. PR-10 removed the prior ``HAL0_BACKEND``
    gate — the v0.1.x toolbox path no longer exists.

    The driver consumes a per-model TTL provider (issue #414) so each
    slot's configured ``idle_timeout_s`` actually drives eviction
    instead of a single hardcoded 300s global.

    ``global_idle_timeout_s`` is the fleet-level fallback from
    ``[slots].idle_timeout_s`` in hal0.toml (default 300 s).
    Individual slot TOML values override this via the TTL provider.

    Failures here MUST NOT block API startup — a busted Lemonade
    config shouldn't keep the dashboard from coming up so the user
    can fix it. The driver itself is also resilient to transient
    lemond unavailability (see ``lemonade/idle.py`` docstring).

    See ADR-0007 §Related, ADR-0008 §1.
    """
    import os

    try:
        from hal0.lemonade.client import LemonadeClient
        from hal0.lemonade.idle import IdleDriver

        client = LemonadeClient(
            api_key=os.environ.get("LEMONADE_API_KEY") or None,
        )
        driver = IdleDriver(
            client,
            idle_timeout_s=global_idle_timeout_s,
            ttl_provider=_build_idle_ttl_provider(slot_manager),
        )
        await driver.start()
        # Stash on app.state so /api/health surfaces + tests can find it.
        app.state.lemonade_client = client
        app.state.lemonade_idle_driver = driver
        log.info("lemonade.idle.driver_attached")
        return driver
    except Exception as exc:  # pragma: no cover — defensive
        log.warning(
            "lemonade.idle.driver_start_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("hal0.api.startup", version=__version__)

    upstreams = UpstreamRegistry()
    _hydrate_upstreams(upstreams)
    model_registry = ModelRegistry()
    hardware_probe = HardwareProbe()

    # Cache the parsed top-level config so request handlers don't repeatedly
    # re-read hal0.toml. The /api/settings PUT path keeps this in sync.
    try:
        hal0_cfg = load_hal0_config()
    except ConfigParseError as exc:
        log.warning("hal0.config.parse_failed", error=str(exc))
        from hal0.config.schema import Hal0Config

        hal0_cfg = Hal0Config()

    # Auto-scan configured model roots so a fresh /mnt/ai-models drop-in
    # shows up in the registry without operator intervention.  Failures
    # here must NOT block startup — the API still has to come up so the
    # user can fix the offending root.
    if hal0_cfg.models.auto_scan_on_start:
        try:
            scan_result = scan_and_register(model_registry, hal0_cfg.models)
            log.info(
                "models.auto_scan_complete",
                added=len(scan_result.get("added", [])),
                skipped=len(scan_result.get("skipped", [])),
                roots=len(scan_result.get("scanned_roots", [])),
            )
        except Exception as exc:
            log.warning("models.auto_scan_failed", error=str(exc))

    # Keep Lemonade's server_models.json in sync with the registry. The hook is
    # wired AFTER the startup auto-scan so a multi-model scan triggers a single
    # regeneration (the explicit one below) rather than one per added model.
    # Every subsequent runtime mutation (pull, register, remove) regenerates the
    # catalog via ModelRegistry.on_change — fixing the drift where curated models
    # were invisible to Lemonade until a manual `hal0 capabilities sync`.
    from pathlib import Path as _Path

    from hal0.lemonade.server_models_gen import write_server_models

    _server_models_path = _Path(
        os.environ.get("HAL0_SERVER_MODELS_PATH", "/opt/lemonade/resources/server_models.json")
    )

    def _regen_server_models() -> None:
        write_server_models(model_registry.registry_file, _server_models_path)

    model_registry.on_change = _regen_server_models
    # One-shot sync so startup scan results land in the catalog immediately,
    # without waiting for the next mutation. Best-effort: a failure (e.g. the
    # Lemonade resources dir is absent on a dev box) must not block startup.
    try:
        _regen_server_models()
    except Exception as exc:
        log.warning("server_models.startup_regen_failed", error=str(exc))

    # Shared in-process /v1/models cache.  The dispatcher's cold-cache
    # prefetch path needs cached_models() and fetch_models() to share
    # state — without this, prefetch fans out then re-checks the cache
    # and finds it empty, and every request 404s.
    # NOTE: no TTL yet; cache persists for the life of the process.
    # A TTL / invalidation strategy lands when the dispatcher gets its
    # own cache layer.
    model_cache: dict[str, list[str]] = {}

    async def _fetch_and_cache(u: Upstream) -> list[str]:
        # The composite ``hal0`` upstream aggregates its model list from
        # the slot catalogue rather than hitting its own URL — that URL
        # is hal0-api itself, so going over HTTP would re-enter the same
        # /v1/models handler and infinite-recurse. The helper applies a
        # 5s TTL keyed on the upstream name.
        if u.kind == "slot" and u.slot_name is None and u.name == "hal0":
            models = await _fetch_hal0_composite_models(u, slot_manager)
        else:
            models = await upstreams.fetch_models(u.name)
        # Preserve multiplex tags seeded at startup (e.g. embed-gemma /
        # whisper-v3:turbo on FLM slots). Without this, the dispatcher's
        # cold-cache prefetch overwrites the seeded entries and embed /
        # asr routing breaks until process restart.
        existing = model_cache.get(u.name, [])
        merged = list(models)
        for tag in existing:
            if tag not in merged:
                merged.append(tag)
        model_cache[u.name] = merged
        return merged

    # SlotManager owns slot state.  Built before Dispatcher so it can be
    # threaded in and forward() can flip slots into SERVING per request.
    # Construct the event bus first so the SlotManager can side-channel
    # every transition through it — the footer subscribes to /api/events.
    event_bus = EventBus()
    slot_manager = SlotManager(event_bus=event_bus, upstreams_registry=upstreams)

    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=model_registry,
        cached_models=lambda name: model_cache.get(name, []),
        fetch_models=_fetch_and_cache,
        slot_manager=slot_manager,
    )

    # One-shot reconciliation: clear pre-fix stuck ERROR on slots whose
    # only problem was an empty model.default. After fix(slots): empty
    # default is OFFLINE+CTA, not ERROR; this pass migrates existing
    # state.json snapshots forward so the dashboard doesn't render red
    # until the operator clicks each slot.
    await slot_manager.reconcile_unconfigured_slots()

    # Idle monitor — demotes READY → IDLE after the configured timeout
    # so the dashboard distinguishes "warm but quiet" from "warm and
    # actively serving" without operator help.  Defaults to 300s; the
    # constructor accepts overrides for tests.
    await slot_manager.start_idle_monitor()

    # Auto-register one composite ``hal0`` upstream so the dispatcher can
    # route ``model: <slot_name>`` requests without requiring the user to
    # write both a slot TOML AND a matching upstreams.toml entry.
    # Explicit upstreams.toml entries (hydrated above) win — autoregister
    # skips when the ``hal0`` name is already taken.
    await _autoregister_slot_upstreams(upstreams, slot_manager)
    # Prime the shared model_cache for the composite upstream so the
    # dispatcher's cold-cache prefetch and /v1/models handler can read it
    # synchronously immediately after startup.
    hal0_upstream = upstreams.get("hal0")
    if hal0_upstream is not None:
        try:
            await _fetch_and_cache(hal0_upstream)
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("upstream.hal0_prime_failed", error=str(exc))
    await _seed_multiplex_models(upstreams, slot_manager, model_cache)

    from hal0.hardware import HardwareStats

    app.state.upstreams = upstreams
    app.state.model_registry = model_registry
    app.state.hal0_config = hal0_cfg
    app.state.hardware_probe = hardware_probe
    app.state.hardware_stats = HardwareStats()
    # Model-pull job registry — keyed by model_id, value is the
    # ``PullJob`` dataclass holding live progress + cancel flags. SSE
    # and status routes snapshot ``as_dict()`` rather than hold the
    # dataclass across event-loop ticks.
    app.state.model_pull_jobs = {}
    # Container image-pull job registry — keyed by slot name, value is a
    # dict with keys: state (pulling|completed|failed), layer, total_layers,
    # error, and a threading.Event for SSE fan-out.
    app.state.slot_pull_jobs = {}
    # Dashboard footer event bus. Constructed above (so SlotManager could
    # be wired with the same instance); published on app.state here so
    # request handlers can reach it via ``request.app.state.events``.
    app.state.events = event_bus
    # Lemond log ring (issue #323 / epic #322 Phase 1). Mirrors the
    # EventBus ring + fan-out for lemond log lines so the unified
    # /api/journal endpoints can backfill + live-tail both sources via
    # one envelope. The background bridge task is started below alongside
    # the other lemonade-bound lifespan tasks.
    lemond_log_ring = LemondLogRing()
    app.state.lemond_log_ring = lemond_log_ring
    await event_bus.emit(
        "system.restart",
        "info",
        "system",
        f"hal0 {__version__} starting",
        data={"version": __version__},
    )
    # /api/upstreams hands the dashboard the cached model list so the
    # "models advertised" column reflects live state without an extra
    # round trip per upstream.
    app.state.upstream_models = model_cache
    app.state.dispatcher = dispatcher
    app.state.slot_manager = slot_manager
    app.state.model_cache = model_cache

    # Capability orchestrator — overlay that maps the dashboard's
    # capability-grouped children (embed/voice/img) onto regular slots.
    # The orchestrator is intentionally constructed AFTER the slot
    # manager + registry are ready so initialize_if_missing() can lift
    # current slot config into capabilities.toml on first boot.
    # NPU Phase 2: pass a zero-arg callable returning a LemonadeClient so
    # the orchestrator's device=npu embed/stt path can read/write lemond
    # ``flm_args`` (drive the FLM trio) instead of spawning a standalone FLM
    # process. Local import keeps orchestrator import cheap.
    def _lemonade_client():  # type: ignore[no-untyped-def]
        from hal0.providers import lemonade_provider

        return lemonade_provider().client()

    capability_orchestrator = CapabilityOrchestrator(
        slot_manager=slot_manager,
        registry=model_registry,
        lemonade_provider=_lemonade_client,
    )
    try:
        await capability_orchestrator.initialize_if_missing()
    except Exception as exc:
        # Never let an overlay seeding failure block API startup — the
        # dashboard can still hit GET /api/capabilities and see empty
        # selections, which is the correct "blank slate" UX.
        log.warning("capabilities.init_failed", error=str(exc))
    app.state.capability_orchestrator = capability_orchestrator
    # Tracks the most recent model id sent to each upstream so the
    # dashboard's synthetic slot reflects current usage instead of the
    # first-non-alias from the catalog. Populated by v1 routes after
    # dispatch resolves.
    app.state.last_used_model = {}
    # Per-slot rolling window of (monotonic_ts, tokens_in_chunk) tuples
    # measured on the streaming forward path. Keyed by the dispatcher's
    # `call.upstream_name` (a slot name for local slots, an upstream id
    # for remote providers) so /api/slots/metrics can attribute current
    # tok/s to the right SlotCard. A defaultdict so any new slot name
    # picks up its own bounded deque without route-side bookkeeping.
    import collections

    def _new_tps_deque() -> collections.deque[tuple[float, int]]:
        return collections.deque(maxlen=4096)

    app.state.tps_events = collections.defaultdict(_new_tps_deque)

    def _new_ttft_deque() -> collections.deque[tuple[float, float]]:
        return collections.deque(maxlen=128)

    app.state.ttft_events = collections.defaultdict(_new_ttft_deque)

    log.info(
        "hal0.api.upstreams_loaded",
        count=len(upstreams.list()),
        names=[u.name for u in upstreams.list()],
    )

    # Each mounted FastMCP server has a ``StreamableHTTPSessionManager``
    # whose anyio task group must be started inside an async-context
    # before any request can be dispatched. Mounted sub-apps don't get
    # their own lifespans run automatically, so we enter each manager's
    # ``run()`` ctxmgr from the parent lifespan via an AsyncExitStack.
    # Without this every /mcp/* request fails with
    # ``Task group is not initialized``.
    from contextlib import AsyncExitStack

    managers = getattr(app.state, "mcp_session_managers", []) or []

    refresh_task = asyncio.create_task(
        _refresh_model_cache_on_ready(event_bus, upstreams, _fetch_and_cache)
    )

    async def _stop_refresh_task() -> None:
        refresh_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await refresh_task

    # Lemond log bridge (issue #323). Long-running task forwarding
    # LemonadeClient.stream_logs() into the ring so the journal panel
    # has backfill across reconnects. The task is resilient to lemond
    # bouncing — it reconnects with exponential backoff internally.
    lemond_bridge_task = start_lemond_bridge(lemond_log_ring)

    async def _stop_lemond_bridge() -> None:
        lemond_bridge_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await lemond_bridge_task

    # Lemonade idle-unload driver (ADR-0007 §Related, ADR-0008 §1). v0.2
    # makes Lemonade the sole backend, so this driver always starts —
    # the prior ``HAL0_BACKEND=lemonade`` gate retired in PR-10. Stored
    # on app.state so tests + future shutdown hooks can introspect it.
    lemonade_idle_driver = await _start_lemonade_idle_driver(
        app,
        slot_manager,
        global_idle_timeout_s=float(hal0_cfg.slots.idle_timeout_s),
    )
    # Lemonade metrics shim (PR-12, plan §10.1 + §11). Shares the
    # ``app.state.lemonade_client`` attached by the idle driver so we
    # don't double up on connection pools against lemond. Provides the
    # snapshot the /api/metrics/prometheus route reads.
    lemonade_metrics_shim = await _start_lemonade_metrics_shim(app)

    # OmniRouter (PR-16, plan §7 + ADR-0008 §8). Client-side OpenAI
    # tool-calling loop. Wired here so the /v1/chat/completions route
    # can pick it up via ``request.app.state.omni_router`` when a
    # request body carries ``omni: true``. The router holds a
    # dedicated httpx client so its lifetime is decoupled from the
    # LemonadeClient (which owns its own connection pool for the
    # control plane).
    omni_router_client: httpx.AsyncClient | None = None
    try:
        from hal0.omni_router import OmniRouter

        omni_router_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0),
            follow_redirects=False,
        )
        lemonade_base_url = os.environ.get("LEMONADE_BASE_URL", "http://127.0.0.1:13305")
        app.state.omni_router = OmniRouter(
            slot_manager=slot_manager,
            http_client=omni_router_client,
            lemonade_base_url=lemonade_base_url,
        )
        log.info("omni_router.attached", base_url=lemonade_base_url)
    except Exception as exc:
        # Never let OmniRouter failure block API startup — the chat
        # route falls back to direct dispatch when ``omni_router`` is
        # absent, which is the same behaviour as the pre-PR-16 baseline.
        log.warning(
            "omni_router.start_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        app.state.omni_router = None

    # FLM trio router (PR-19, plan §5 + ADR-0008 §5 + ADR-0009). When an
    # NPU chat slot is loaded with ``flm.args = "--asr 1 --embed 1"``,
    # Lemonade only registers the chat model — STT + embed run on the
    # same FLM child but aren't reachable through Lemonade's dispatcher.
    # This router discovers the FLM child's ``backend_url`` from
    # ``/v1/health`` and posts directly when v1.py's STT/embed routes
    # detect an enabled ``stt-npu`` / ``embed-npu`` slot. Falls back
    # cleanly when the FLM chat isn't loaded (FLMTrioNotAvailable raised
    # at dispatch time so the user sees a clear envelope).
    flm_trio_router = None
    try:
        from hal0.dispatcher.flm_trio import FLMTrioRouter

        lemonade_client_for_trio = getattr(app.state, "lemonade_client", None)
        if lemonade_client_for_trio is not None:
            flm_trio_router = FLMTrioRouter(
                lemonade_client=lemonade_client_for_trio,
            )
            app.state.flm_trio_router = flm_trio_router
            log.info("flm_trio.attached")
        else:
            # No lemonade client → no trio routing. v1.py's gating check
            # treats a missing router as "trio not available" and falls
            # through to the normal Lemonade dispatch path, which 404s
            # for stt-npu/embed-npu requests (since Lemonade has no
            # such models registered) — that 404 is the expected
            # behaviour when NPU isn't wired up.
            app.state.flm_trio_router = None
            log.info("flm_trio.skipped_no_lemonade_client")
    except Exception as exc:
        log.warning(
            "flm_trio.start_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        app.state.flm_trio_router = None

    try:
        async with AsyncExitStack() as stack:
            for mgr in managers:
                await stack.enter_async_context(mgr.run())
            stack.push_async_callback(_stop_refresh_task)
            stack.push_async_callback(_stop_lemond_bridge)
            yield
    finally:
        if lemonade_metrics_shim is not None:
            await lemonade_metrics_shim.stop()
        if lemonade_idle_driver is not None:
            await lemonade_idle_driver.stop()
        if omni_router_client is not None:
            with contextlib.suppress(Exception):
                await omni_router_client.aclose()
        await slot_manager.stop_idle_monitor()
        await dispatcher.aclose()
        with contextlib.suppress(Exception):
            await lemonade_proxy_routes.aclose_client()
        with contextlib.suppress(Exception):
            await comfyui.aclose_client()
        log.info("hal0.api.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="hal0",
        version=__version__,
        description="Open-source home AI inference platform",
        lifespan=lifespan,
        # OpenAPI docs at /api/docs to keep `/docs` reserved for the UI later
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    request_id.install(app)
    error_codes.install(app)
    # PR-9 (DA-sec-ops MUST-FIX #3): strip query strings from the
    # uvicorn access log so a future sensitive parameter never lands
    # in journald.
    log_scrub.install(app)

    # /v1 is split into a public probe (GET /v1/models + /v1/models/{id})
    # and a writer surface that requires auth. The split lives in v1.py
    # via v1.public_router (probes) + v1.router (inference). OpenAI
    # clients historically GET /v1/models before sending an Authorization
    # header — keeping that probe auth-free preserves SDK compatibility.
    app.include_router(v1.public_router, prefix="/v1", tags=["v1"])
    app.include_router(v1.router, prefix="/v1", tags=["v1"])

    # Issue #212: Lemonade reverse-proxy catch-all on /v1/{path:path}.
    # Mounted AFTER the dispatcher-owned v1 routers so every explicit
    # inference path (chat, completions, embeddings, rerankings, audio,
    # images, models) keeps its dispatcher handler; only un-covered
    # paths (/v1/health, /v1/stats, /v1/load, /v1/unload, /v1/system-info,
    # /v1/params, …) fall through to Lemonade. Same admin auth as the
    # rest of the writer /v1 surface.
    app.include_router(
        lemonade_proxy_routes.router,
        prefix="/v1",
        tags=["v1", "lemonade-proxy"],
    )

    # /api/install drives the first-run wizard. Auth was removed in ADR-0012
    # so these endpoints are open; the installer surface is admin-only by
    # convention (network-level access control).
    app.include_router(
        installer.router,
        prefix="/api/install",
        tags=["installer"],
    )
    app.include_router(slots.router, prefix="/api/slots", tags=["slots"])
    # Read-only ComfyUI "generation engine" status for the slots-page Image-Gen
    # tab (docker + systemd + ComfyUI HTTP), plus the feature-gated switchover.
    app.include_router(comfyui.router, prefix="/api/comfyui", tags=["comfyui"])
    app.include_router(models.router, prefix="/api/models", tags=["models"])
    # Issue #311: HuggingFace Hub discovery (search proxy). Sits next
    # to the models surface so the dashboard's "Search HF" button has a
    # backend to call; the inspect endpoint already lives under
    # /api/models/inspect and is a *different* flow (known coord →
    # variants) than this search proxy (free-text → coord candidates).
    app.include_router(hf.router, prefix="/api/hf", tags=["hf"])
    app.include_router(hardware.router, prefix="/api", tags=["hardware"])
    app.include_router(logs.router, prefix="/api/logs", tags=["logs"])
    # PR-11: Lemonade log proxy — surfaces the /logs/stream WS as SSE
    # streams the dashboard consumes for the journal panel (PR-14) and
    # the nuclear-evict toast banner. Same admin auth as the rest of
    # the slot surface.
    app.include_router(
        lemonade_logs_routes.router,
        prefix="/api/lemonade",
        tags=["lemonade", "logs"],
    )
    # PR-13: Lemonade admin panel — GET /api/lemonade/config + POST
    # /api/lemonade/config wrap lemond's /internal/config + /internal/set
    # so the Settings → Lemonade admin panel can read + edit runtime
    # config. Auth removed in ADR-0012; access is open on the local
    # network.
    app.include_router(
        lemonade_admin_routes.router,
        prefix="/api/lemonade",
        tags=["lemonade", "admin"],
    )
    app.include_router(
        settings.router,
        prefix="/api/settings",
        tags=["settings"],
    )
    # Proxmox integration sub-router (config file at /etc/hal0/proxmox.json).
    # Mounted as a sibling under /api/settings/proxmox so the dashboard's
    # Settings panel can read/write it without touching hal0.toml.
    app.include_router(
        proxmox_routes.router,
        prefix="/api/settings/proxmox",
        tags=["settings", "proxmox"],
    )
    # ADR-0014 memory.graph gate + status. Mounted under /api/memory
    # so the dashboard Memory tab + `hal0 memory graph` CLI both read
    # + write through one surface. Constructed early enough that the
    # dashboard SPA fallback doesn't shadow these paths.
    app.include_router(
        memory_routes.router,
        prefix="/api/memory",
        tags=["memory"],
    )

    app.include_router(providers.router, prefix="/api", tags=["providers"])
    app.include_router(
        updater.router,
        prefix="/api/updates",
        tags=["updater"],
    )

    # Capability slots overlay — operator-facing grouping of embed /
    # voice / img children on top of the SlotManager. Admin-gated like
    # the slots router itself; selections trigger underlying slot
    # lifecycle operations.
    app.include_router(
        capabilities_routes.router,
        prefix="/api/capabilities",
        tags=["capabilities"],
    )

    # First-run bundle picker (ADR-0010 / PR-17). Admin-gated for
    # consistency with the rest of the capability surface; the picker
    # writes capabilities.toml entries via the orchestrator and drops
    # a marker file so the dashboard hides the picker on subsequent
    # loads. Plan §8 + §11 PR-17 own the user-facing UX.
    app.include_router(
        bundles_routes.router,
        prefix="/api/bundles",
        tags=["bundles"],
    )

    # Backend introspection — live status + currently-loaded children
    # per backend (NPU / GPU-Vulkan / GPU-ROCm / CPU). Read-only and
    # used by the dashboard footer; admin-gated for consistency with
    # the rest of the capability surface.
    app.include_router(
        backends_routes.router,
        prefix="/api/backends",
        tags=["backends"],
    )

    # NPU trio swap-status (PR-20). One read-only endpoint that merges
    # the configured NPU LLM slot model with lemond's /v1/health.loaded[]
    # so the dashboard's "Swap incoming" banner has a single source of
    # truth. Admin-gated alongside the rest of the capability surface.
    app.include_router(
        npu.router,
        prefix="/api/npu",
        tags=["npu"],
    )

    # Health + config/urls routers carry endpoints that are entirely
    # public (e.g. /api/status, /api/config/urls). Auth was removed in
    # ADR-0012; all endpoints on this server are open on the local network.
    app.include_router(health.router, prefix="/api", tags=["health"])
    app.include_router(config_routes.router, prefix="/api/config", tags=["config"])

    # Profile catalog — read-only, no auth (ADR-0012). Returns every profile
    # from /etc/hal0/profiles.toml (falling back to the built-in seeds on a
    # fresh install). Profiles are P1 container-runtime templates (issue #653).
    app.include_router(profiles_routes.router, prefix="/api/profiles", tags=["profiles"])

    # Dashboard footer event surface — read-only, public for the same
    # reason as /api/status: the footer renders during first-run before
    # any credential exists. No mutating endpoints live on this router.
    app.include_router(events_routes.router, prefix="/api/events", tags=["events"])

    # Unified journal panel (issue #323, epic #322 Phase 1). Merges
    # /api/events + /api/lemonade/logs/stream into one shape for the
    # dashboard's journal panel. Read-only; same first-run rationale
    # as /api/events.
    app.include_router(
        journal_routes.router,
        prefix="/api/journal",
        tags=["journal"],
    )

    # Image cache — generated PNGs from /v1/images/generations.  Admin
    # auth gate: cached PNGs live at predictable /api/images/cache/<uuid>
    # URLs and could leak prompts via filename if exposed publicly.
    app.include_router(images.router, prefix="/api/images", tags=["images"])

    # Bundled-agent lifecycle (ADR-0004 §2). Install / uninstall / list /
    # status. Single-pick + atomic switch enforced inside AgentManager.
    app.include_router(
        agents_routes.router,
        prefix="/api/agents",
        tags=["agents"],
    )

    # Agent personas (v0.3 PR-4). Per-agent persona TOML browse + activate
    # under the SAME ``/api/agents`` prefix so the dashboard's agent view
    # nests personas under the agent it belongs to. Routes are
    # parameterized by agent id; v0.3 only resolves ``"hermes"``.
    app.include_router(
        agents_personas_routes.router,
        prefix="/api/agents",
        tags=["agents", "personas"],
    )

    # Per-persona spending-cap primitive (Phase 0 OpenRouter prereq).
    # GET/PUT the budget block + check/charge endpoints so the V1
    # OpenRouter provider has a gate from day 1. Same /api/agents
    # prefix as the personas router so the dashboard's persona editor
    # can call both without juggling base URLs.
    app.include_router(
        agents_budget_routes.router,
        prefix="/api/agents",
        tags=["agents", "personas", "budget"],
    )

    # Agent service restart (v0.3 PR-11). Wraps systemctl restart of the
    # hal0-agent@<id>.service template unit. Flagged as missing during
    # PR-6/PR-8/PR-10 integration: the sidecar agent block + the
    # service-status chip both want a one-click restart action. Audit
    # log emitted on every invocation via the ``hal0.agents.audit``
    # logger; matches the slot-restart pattern.
    app.include_router(
        agents_restart_routes.router,
        prefix="/api/agents",
        tags=["agents", "restart"],
    )

    # Agent memory stats (v0.3 PR-11). GET /api/agents/{id}/memory/stats
    # returns the counts the dashboard sidecar memory chip renders.
    # Fallback to ``available=false`` when the wrapper isn't initialised,
    # so a hal0 install without Cognee still renders sensibly.
    app.include_router(
        agents_memory_stats_routes.router,
        prefix="/api/agents",
        tags=["agents", "memory"],
    )

    # PR-9: chat WS proxy + session REST shim. Bridges the browser to
    # the hermes dashboard process bound to 127.0.0.1:9119 (per PR-5's
    # systemd ExecStart). Origin allowlist + HMAC session cookie
    # enforced on every WS upgrade (DA-sec-ops MUST-FIX #2). Embed
    # token rides outbound in Authorization: Bearer, never in a query
    # string (MUST-FIX #3).
    app.include_router(
        chat_proxy_router,
        prefix="/api/agents",
        tags=["agents", "chat-proxy"],
    )

    # Approval inbox (ADR-0004 §5). The dashboard bell, the MCP admin
    # server's gated-tool enqueue, and the ``hal0 agent approvals``
    # CLI all read from the same lifespan-scoped ApprovalQueue. GETs
    # require any token; POST approve/deny require admin (writer)
    # scope — declared inside the route module.
    app.include_router(
        approvals_routes.router,
        prefix="/api/agent/approvals",
        tags=["approvals"],
    )

    # MCP introspection (issue #206). Read-only view of hosted MCP
    # servers, connected clients (audit-derived), the installable
    # catalog, and an SSE tail of ``mcp.tool.*`` events. The lifecycle
    # mutations (install / uninstall / restart / config-write) stub at
    # 501 — ADR-0013's ``mcp_client.py`` work owns those.
    app.include_router(
        mcp_routes.router,
        prefix="/api/mcp",
        tags=["mcp"],
    )

    # OpenRouter OAuth callback scaffold (ADR-0020, Phase 0). The route
    # is registered so V1 (the OpenRouter-as-Hermes-upstream PR) inherits
    # the loopback guard from day 1; the handler currently returns 501
    # with a pointer to ADR-0020. Router declares absolute paths so no
    # prefix is needed here.
    app.include_router(
        openrouter_auth_router,
        tags=["openrouter", "auth"],
    )

    # Hermes dashboard plugin host (v0.3 PR-7). hal0-api proxies the
    # upstream manifest list + the per-plugin static-asset surface so
    # the v3 dashboard can mount upstream's plugin bundles (kanban
    # today) inside an ``<AgentView>`` tab without crossing the
    # loopback boundary directly. The router declares its own absolute
    # paths (``/api/dashboard/plugins`` + ``/dashboard-plugins/...``);
    # mounted BEFORE ``_mount_dashboard`` so the SPA fallback doesn't
    # shadow them.
    app.include_router(
        plugin_manifest_router,
        tags=["plugins"],
    )

    # ── MCP servers (ADR-0004 §4 + ADR-0005 §2) ─────────────────────
    # Mounted BEFORE _mount_dashboard so the dashboard's SPA fallback
    # doesn't shadow /mcp/* paths. ApprovalQueue + CogneeWrapper are
    # constructed eagerly here (no async setup needed for either) so
    # the mount can wire them in immediately.
    from hal0.mcp import ApprovalQueue

    app.state.approval_queue = ApprovalQueue()

    memory_provider = None
    # 0.4 release gate — memory subsystem deferred. The memory engine
    # (Cognee), its MCP server (/mcp/memory), the REST surface
    # (/api/memory/*), and the dashboard's Agent → Memory tab ship
    # DISABLED by default and return in a later release once the two-tier
    # brain redesign (Hindsight + hal0-wiki) lands. Set HAL0_MEMORY_ENABLED=1
    # to reintroduce it with NO code change — every downstream caller
    # (admin MCP routing, /api/memory/* routes, the Hermes memory provider,
    # per-agent memory stats) already degrades to a no-op / 503 when
    # app.state.memory_provider is None, so flipping the flag is the whole
    # toggle. Default off so behaviour is identical on fresh AND upgraded
    # installs (api.env is not rewritten on upgrade).
    if os.environ.get("HAL0_MEMORY_ENABLED", "0") != "1":
        log.info("hal0.memory.disabled", reason="HAL0_MEMORY_ENABLED!=1")
    else:
        try:
            from hal0.memory import provider_from_config

            memory_provider = provider_from_config(load_hal0_config())
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("hal0.memory.init_failed", error=str(exc))
    app.state.memory_provider = memory_provider

    # In-process memory dispatcher (Phase 8 closeout, ADR-0004 §7).
    # When Cognee is up, instantiate one MemoryDispatcher and hand it to
    # mount_mcp_servers so the admin MCP server's ``memory_*`` tools hit
    # Cognee directly instead of looping back through HTTP to
    # ``/mcp/memory``. The same client-id + private-mode resolvers the
    # memory MCP uses thread through the dispatcher so audit grounding
    # and namespace promotion stay identical across transports.
    memory_dispatcher = None
    if memory_provider is not None:
        try:
            from hal0.api.mcp_mount import client_id_resolver, private_resolver
            from hal0.dispatcher.memory_dispatcher import MemoryDispatcher

            memory_dispatcher = MemoryDispatcher(
                memory_provider,
                client_id_resolver=client_id_resolver,
                private_resolver=private_resolver,
            )
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("hal0.memory.dispatcher_init_failed", error=str(exc))
    app.state.memory_dispatcher = memory_dispatcher

    try:
        from hal0.api.mcp_mount import mount_mcp_servers

        mount_mcp_servers(
            app,
            approval_queue=app.state.approval_queue,
            memory_provider=memory_provider,
            memory_dispatcher=memory_dispatcher,
        )
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("hal0.mcp.mount_failed", error=str(exc))

    _mount_dashboard(app)

    return app


def _mount_dashboard(app: FastAPI) -> None:
    """Serve the built Vue dashboard at ``/`` with SPA fallback.

    Resolution order for ``ui/dist`` (the built Vue bundle):
      1. ``HAL0_UI_DIST`` env override (used by tests + dev installs).
      2. ``/usr/lib/hal0/ui/dist`` (FHS install path per PLAN §2).
      3. ``<repo>/ui/dist`` (editable install — find by walking up from
         this file).

    If none exist (e.g. backend-only smoke tests), skip silently — the
    api still serves ``/api/*`` and ``/v1/*`` as before.

    SPA fallback: any GET that doesn't match a route, doesn't start with
    ``/api`` or ``/v1``, and isn't a static asset returns ``index.html``
    so client-side routing (``/slots``, ``/firstrun`` etc.) survives a
    page reload.
    """
    import os
    from pathlib import Path

    from fastapi.responses import FileResponse, Response
    from fastapi.staticfiles import StaticFiles

    candidates: list[Path] = []
    env_dir = os.environ.get("HAL0_UI_DIST", "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(Path("/usr/lib/hal0/ui/dist"))
    here = Path(__file__).resolve()
    for parent in here.parents:
        repo_dist = parent / "ui" / "dist"
        if repo_dist.exists():
            candidates.append(repo_dist)
            break

    dist = next((p for p in candidates if p.is_dir() and (p / "index.html").is_file()), None)
    if dist is None:
        log.info("dashboard.dist_not_found", searched=[str(c) for c in candidates])
        return

    log.info("dashboard.mounted", dist=str(dist))
    index = dist / "index.html"
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    brand_dir = dist / "brand"
    if brand_dir.is_dir():
        app.mount("/brand", StaticFiles(directory=brand_dir), name="brand")

    @app.get("/favicon.svg", include_in_schema=False)
    async def _favicon() -> Response:
        return FileResponse(dist / "favicon.svg")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa(full_path: str) -> Response:
        # Don't shadow API routes — those return 404 normally if missing.
        if full_path.startswith("api/") or full_path.startswith("v1/"):
            return Response(status_code=404)
        return FileResponse(index)


app = create_app()
