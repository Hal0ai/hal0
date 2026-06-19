"""FastAPI application factory.

The module-level `app` exists so `uvicorn hal0.api:app` works directly.
For tests and alternate entrypoints, call `create_app()`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import structlog
from fastapi import FastAPI

from hal0 import __version__
from hal0.activity import AuditStore
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
    activity as activity_routes,
)
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
    board as board_routes,
)
from hal0.api.routes import (
    capabilities as capabilities_routes,
)
from hal0.api.routes import (
    chat_templates as chat_templates_routes,
)
from hal0.api.routes import (
    comfyui,
    dashboard_layout,
    hardware,
    health,
    hf,
    images,
    installer,
    logs,
    models,
    npu,
    power,
    providers,
    services_health,
    settings,
    slots,
    throughput,
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
    mcp as mcp_routes,
)
from hal0.api.routes import (
    memory as memory_routes,
)
from hal0.api.routes import (
    memory_admin as memory_admin_routes,
)
from hal0.api.routes import (
    profiles as profiles_routes,
)
from hal0.api.routes import (
    proxmox as proxmox_routes,
)
from hal0.api.routes import (
    secrets as secrets_routes,
)
from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.config.loader import ConfigParseError, load_hal0_config, load_upstreams_config
from hal0.config.paths import activity_db
from hal0.dispatcher.router import Dispatcher
from hal0.events import EventBus
from hal0.hardware.probe import HardwareProbe
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
    autoregistration (R4 H2) and exists for ``/v1/models`` aggregation
    only — container slots register their own ``kind="remote"``
    upstreams for actual dispatch.

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
    """Return the set of model ids served by dispatchable container slots.

    A model counts as loaded when its slot is in the dispatchable
    ready-set (READY / SERVING / IDLE, per #696). Returns ``None`` when
    slot configs can't be read at all so callers can decide how to
    degrade — distinct from an empty set, which means "no slot is
    currently serving anything".
    """
    try:
        cfgs = await slot_manager.iter_configs()
    except Exception:  # pragma: no cover — defensive
        return None
    loaded: set[str] = set()
    for cfg in cfgs:
        name = str(cfg.get("name") or "").strip()
        if not name:
            continue
        model_id = _slot_model_id(cfg)
        if not model_id:
            continue
        try:
            if slot_manager.is_ready_for_dispatch(name):
                loaded.add(model_id)
        except Exception:
            continue
    return loaded


async def hal0_slot_alias_models(
    slot_manager: SlotManager,
    model_registry: ModelRegistry,
    *,
    now: int | None = None,
) -> list[dict[str, Any]]:
    """Build OpenAI ``model`` objects for every LOADED chat slot, alias-addressed.

    Each enabled chat slot (``type == "llm"``) whose configured model is
    currently being served surfaces as one model object whose ``id``
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

    Slots that are disabled, lack a configured model, or are not
    currently serving are omitted. If slot configs can't be read at all,
    no alias entries are emitted (we refuse to advertise a slot we can't
    confirm is serving) — the composite ``hal0`` model list still
    carries the raw model ids for direct addressing.
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
            # Model not in the registry (hand-staged, …) — fall back to
            # the bare model id for the display label.
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
    routing, so dispatch resolves the correct distinct model. This is a
    thin translation map, not a routing target.

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
        # FLM/NPU slots: the resolver matches against the loaded set (FLM's
        # advertised catalog of native ``family:size`` tags) and returns the
        # model id dispatched downstream. Both must be the colon tag
        # (``gemma4-it:e2b``), not hal0's ``-FLM`` catalog id — otherwise
        # ``hal0/utility``/``hal0/npu`` never match the slot and fall through
        # to the chat slot. Translate via the same map as the FLM provider.
        if (cfg.get("device") or "").strip() == "npu" or (cfg.get("backend") or "") == "flm":
            from hal0.providers.flm import flm_id_to_tag

            tag = flm_id_to_tag(model_id)
            if tag:
                model_id = tag
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

    The composite exists for ``/v1/models`` aggregation (one upstream
    advertising every registered chat-capable model id). It is never
    forwarded to — container slots register their own ``kind="remote"``
    upstreams for dispatch, and per-slot alias addressing is handled by
    an alias → model-id rewrite in the dispatch path (see
    :meth:`Dispatcher.dispatch`).

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
# in ``model.default`` plus embed-gemma:300m (``[npu] embed=true``) plus
# whisper-v3:turbo (``[npu] asr=true``; legacy ``[defaults] load_*`` keys
# still honoured, #733). Those auxiliary models don't show up in FLM's
# ``/v1/models`` response (it only lists chat tags), so the dispatcher's
# passthrough cache never learns about them and routes the canonical tags
# to nowhere. Seed the cache explicitly.
_FLM_EMBED_TAG = "embed-gemma:300m"
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
        is_flm = "flm" in (cfg.get("provider", ""), cfg.get("backend", ""))
        if not is_flm or not name:
            continue
        # Container-era schema is the [npu] table (what FLMProvider builds
        # the --asr/--embed argv from); [defaults] load_* is the pre-#733
        # legacy shape, kept so older tomls keep seeding.
        npu_table = cfg.get("npu") or {}
        defaults = cfg.get("defaults") or {}
        load_embed = npu_table.get("embed") or defaults.get("load_embed")
        load_asr = npu_table.get("asr") or defaults.get("load_asr")
        if load_embed and _FLM_EMBED_TAG not in bucket:
            bucket.append(_FLM_EMBED_TAG)
            log.info("slots.multiplex_seeded", slot=name, model=_FLM_EMBED_TAG)
        if load_asr and _FLM_ASR_TAG not in bucket:
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

    # Durable audit/activity store — the source of truth surfaced by
    # /api/activity. Constructed before the event bus so the bus can forward
    # every emitted event into it (the durable mirror). High-frequency
    # pull.progress is filtered out of the mirror so it can't evict
    # lifecycle history; the explicit audit_action() path carries the richer
    # user-action records with before/after + outcome.
    audit_store: AuditStore | None = None
    audit_epoch = uuid.uuid4().hex
    if hal0_cfg.activity.enabled:
        retention = int(
            os.environ.get("HAL0_ACTIVITY_RETENTION_DAYS") or hal0_cfg.activity.retention_days
        )
        audit_store = AuditStore(
            activity_db(),
            retention_days=retention,
            max_rows=hal0_cfg.activity.max_rows,
        )
        try:
            audit_store.init_schema()
            await audit_store.prune()
        except Exception as exc:  # init must never block startup
            log.warning("activity.init_failed", error=str(exc))
            audit_store = None

    async def _audit_sink(event: dict[str, Any]) -> None:
        if audit_store is None or event.get("type") == "pull.progress":
            return
        await audit_store.record_event(event)

    # SlotManager owns slot state.  Built before Dispatcher so it can be
    # threaded in and forward() can flip slots into SERVING per request.
    # Construct the event bus first so the SlotManager can side-channel
    # every transition through it — the footer subscribes to /api/events.
    event_bus = EventBus(sink=_audit_sink if audit_store is not None else None)
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
    # (so the dashboard distinguishes "warm but quiet" from "warm and
    # actively serving") AND hard-evicts slots idle past their TTL to free
    # host RAM (#902).  The global default evict TTL comes from
    # slots.idle_timeout_s; per-slot TOML idle_timeout_s overrides it and
    # idle_timeout_s = 0 pins a slot.  Defaults to 300s for tests.
    await slot_manager.start_idle_monitor(evict_after_s=hal0_cfg.slots.idle_timeout_s)

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

    # #732: re-register per-slot remote upstreams for containers that
    # survived the api restart (the registry is in-memory; the containers
    # are not). Prime each restored upstream's model cache so dispatch
    # routes immediately — no operator unload+load sweep.
    try:
        restored_slots = await slot_manager.reconcile_container_upstreams()
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("container.upstream_reconcile_failed", error=str(exc))
        restored_slots = []
    for restored_name in restored_slots:
        restored_upstream = upstreams.get(restored_name)
        if restored_upstream is None:
            continue
        try:
            await _fetch_and_cache(restored_upstream)
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("upstream.reconcile_prime_failed", slot=restored_name, error=str(exc))

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
    # Durable audit/activity store + a per-process epoch so the ActivityLog
    # can detect a restart and reset its cursor (events ids restart at 1).
    app.state.audit = audit_store
    app.state.audit_epoch = audit_epoch
    # Operator Board: thin audited proxy client to the Hermes kanban plugin
    # (loopback :9119). Constructed once per process; the board router funnels
    # every /api/board/* call through it. Resolves HERMES_DASHBOARD_BASE_URL +
    # the Hermes session bearer (env HERMES_SESSION_TOKEN) from from_env().
    from hal0.board import HermesKanbanClient

    app.state.hermes_kanban = HermesKanbanClient.from_env()
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
    capability_orchestrator = CapabilityOrchestrator(
        slot_manager=slot_manager,
        registry=model_registry,
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

    # GpuArbiter idle-restore loop (Phase D, Task D6). Auto-restores the
    # saved LLM set after the img (ComfyUI) slot idles out — window from the
    # img slot's ``[image].idle_restore_minutes`` (default 60; 0 = manual-only).
    # Mirrors the refresh_task pattern above: created at startup, cancelled +
    # awaited on shutdown via the AsyncExitStack. Guarded so an arbiter
    # construction failure never blocks API startup (omni-router precedent).
    gpu_arbiter_idle_task: asyncio.Task[None] | None = None
    try:
        gpu_arbiter_idle_task = asyncio.create_task(slot_manager.arbiter.run_idle_loop())
        log.info("gpu_arbiter.idle_loop_started")
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("gpu_arbiter.idle_loop_start_failed", error=str(exc))
    app.state.gpu_arbiter_idle_task = gpu_arbiter_idle_task

    async def _stop_gpu_arbiter_idle_loop() -> None:
        if gpu_arbiter_idle_task is not None:
            gpu_arbiter_idle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await gpu_arbiter_idle_task

    # OmniRouter (PR-16, plan §7 + ADR-0008 §8). Client-side OpenAI
    # tool-calling loop. Wired here so the /v1/chat/completions route
    # can pick it up via ``request.app.state.omni_router`` when a
    # request body carries ``omni: true``. The router holds a
    # dedicated httpx client so its lifetime is decoupled from the
    # dispatcher's pool. Chat completions re-enter hal0's own /v1
    # surface (#709) so the full dispatch chain — GpuArbiter
    # image-mode guard, readiness gates, container routing — applies
    # to omni traffic too.
    omni_router_client: httpx.AsyncClient | None = None
    try:
        from hal0.omni_router import OmniRouter

        omni_router_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0),
            follow_redirects=False,
        )
        api_base_url = os.environ.get("HAL0_SELF_BASE_URL", "http://127.0.0.1:8080")
        app.state.omni_router = OmniRouter(
            slot_manager=slot_manager,
            http_client=omni_router_client,
            api_base_url=api_base_url,
        )
        log.info("omni_router.attached", base_url=api_base_url)
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

    # NPU trio router (ADR-0008 §5 + ADR-0009). The containerized npu
    # slot's single ``flm serve`` process answers chat + STT + embed on
    # one static port; chat routes through the slot upstream like any
    # other slot, while v1.py's STT/embed routes post the two shadow
    # roles straight to the container when they detect an enabled
    # ``stt-npu`` / ``embed-npu`` slot record. Degrades cleanly when the
    # container isn't dispatchable (NpuTrioNotAvailable raised at
    # dispatch time so the user sees a clear envelope).
    try:
        from hal0.dispatcher.npu_trio import NpuTrioRouter

        app.state.npu_trio_router = NpuTrioRouter(slot_manager=slot_manager)
        log.info("npu_trio.attached")
    except Exception as exc:
        log.warning(
            "npu_trio.start_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        app.state.npu_trio_router = None

    try:
        async with AsyncExitStack() as stack:
            for mgr in managers:
                await stack.enter_async_context(mgr.run())
            stack.push_async_callback(_stop_refresh_task)
            stack.push_async_callback(_stop_gpu_arbiter_idle_loop)
            yield
    finally:
        if omni_router_client is not None:
            with contextlib.suppress(Exception):
                await omni_router_client.aclose()
        await slot_manager.stop_idle_monitor()
        await dispatcher.aclose()
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
    # tab (docker + systemd + ComfyUI HTTP), plus arbiter switchover controls.
    app.include_router(comfyui.router, prefix="/api/comfyui", tags=["comfyui"])
    app.include_router(models.router, prefix="/api/models", tags=["models"])
    # Issue #311: HuggingFace Hub discovery (search proxy). Sits next
    # to the models surface so the dashboard's "Search HF" button has a
    # backend to call; the inspect endpoint already lives under
    # /api/models/inspect and is a *different* flow (known coord →
    # variants) than this search proxy (free-text → coord candidates).
    app.include_router(hf.router, prefix="/api/hf", tags=["hf"])
    app.include_router(hardware.router, prefix="/api", tags=["hardware"])
    # Dashboard-overhaul backend endpoints (CONTRACTS.md §2):
    #   throughput.router → GET /api/stats/throughput/history (bucketed tps_events)
    #   services_health.router → GET /api/services/health
    #   dashboard_layout.router → GET/PUT /api/user/dashboard-layout (file-backed)
    app.include_router(throughput.router, prefix="/api", tags=["stats"])
    app.include_router(power.router, prefix="/api", tags=["stats"])
    app.include_router(services_health.router, prefix="/api/services", tags=["services"])
    app.include_router(dashboard_layout.router, prefix="/api/user", tags=["user"])
    app.include_router(logs.router, prefix="/api/logs", tags=["logs"])
    app.include_router(
        settings.router,
        prefix="/api/settings",
        tags=["settings"],
    )
    # Operator-managed secrets store (Settings → Secrets). Persists to the
    # same /etc/hal0/api.env file the provider-credential writer targets,
    # via the shared atomic mode-0600 writer in hal0.api._env_store. Values
    # are write-only — never returned, never logged.
    app.include_router(
        secrets_routes.router,
        prefix="/api/secrets",
        tags=["secrets"],
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
    # Hindsight engine admin surface (banks/graph/recall/operations…) —
    # the dashboard Memory view's data plane. Same prefix, separate router
    # so the engine-agnostic provider routes above stay engine-agnostic.
    app.include_router(
        memory_admin_routes.router,
        prefix="/api/memory",
        tags=["memory"],
    )

    # Operator Board (#board) — thin AUDITED proxy to the Hermes kanban plugin
    # + a hal0-native chat orchestrator. FROZEN FE↔BE contract (SPEC §4).
    # Mounted PRE-dashboard so /api/board/* (incl. the /events WS + /chat SSE)
    # is not shadowed by the SPA fallback.
    app.include_router(board_routes.router, prefix="/api/board", tags=["board"])

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

    # Backend introspection — live status + currently-loaded children
    # per backend (NPU / GPU-Vulkan / GPU-ROCm / CPU). Read-only and
    # used by the dashboard footer; admin-gated for consistency with
    # the rest of the capability surface.
    app.include_router(
        backends_routes.router,
        prefix="/api/backends",
        tags=["backends"],
    )

    # NPU trio swap-status (PR-20). One read-only endpoint deriving the
    # swap window from the npu container slot's lifecycle state so the
    # dashboard's "Swap incoming" banner has a single source of truth.
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

    # Chat-template catalog — bundled templates seeded into the model store at
    # startup; operator can add custom templates via POST. Read + write, public
    # (same rationale as profiles: admin-only by network convention, no creds).
    from hal0.templates import seed_chat_templates

    try:
        seed_chat_templates()
    except Exception as exc:  # pragma: no cover — defensive, store may be absent
        log.warning("hal0.chat_templates.seed_failed", error=str(exc))
    app.include_router(
        chat_templates_routes.router,
        prefix="/api/chat-templates",
        tags=["chat-templates"],
    )

    # Dashboard footer event surface — read-only, public for the same
    # reason as /api/status: the footer renders during first-run before
    # any credential exists. No mutating endpoints live on this router.
    app.include_router(events_routes.router, prefix="/api/events", tags=["events"])

    # Durable activity / audit surface — the source of truth for config
    # changes + state transitions, backing the slots-page ActivityLog.
    # Read-only + public for the same first-run reason as /api/events.
    app.include_router(activity_routes.router, prefix="/api/activity", tags=["activity"])

    # Unified journal panel (issue #323, epic #322 Phase 1). Serves
    # /api/events in the journal envelope for the dashboard's journal
    # panel. Read-only; same first-run rationale as /api/events.
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
