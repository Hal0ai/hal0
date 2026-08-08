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
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog
from fastapi import FastAPI

from hal0 import __version__
from hal0.activity import AuditStore
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
from hal0.api.auth import AuthEnforcementMiddleware
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
    auth as auth_routes,
)
from hal0.api.routes import (
    backends as backends_routes,
)
from hal0.api.routes import (
    benchmarks as benchmarks_routes,
)
from hal0.api.routes import (
    board as board_routes,
)
from hal0.api.routes import (
    brain as brain_routes,
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
    doctor,
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
    meta as meta_routes,
)
from hal0.api.routes import (
    ports as ports_routes,
)
from hal0.api.routes import (
    profiles as profiles_routes,
)
from hal0.api.routes import (
    proxmox as proxmox_routes,
)
from hal0.api.routes import (
    realtime as realtime_routes,
)
from hal0.api.routes import (
    runner_images as runner_images_routes,
)
from hal0.api.routes import (
    secrets as secrets_routes,
)
from hal0.api.routes import (
    services as services_routes,
)
from hal0.api.routes import (
    stacks as stacks_routes,
)
from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.config.loader import ConfigParseError, load_hal0_config, load_upstreams_config
from hal0.config.paths import activity_db
from hal0.dispatcher.router import Dispatcher
from hal0.events import EventBus
from hal0.hardware.probe import HardwareProbe
from hal0.observability import sentry
from hal0.registry.discover import scan_and_register
from hal0.registry.model import _BLESSED_PREFIX
from hal0.registry.runner_image_store import RunnerImageStore
from hal0.registry.store import ModelRegistry
from hal0.slots.manager import SlotManager
from hal0.upstreams.registry import Upstream, UpstreamRegistry, upstream_from_entry

log = structlog.get_logger(__name__)


# Module-level cache for the composite model catalogue's aggregated
# /v1/models response — a direct read over slot config, not a registered
# Upstream. Keyed by a fixed cache key (there's only ever one composite);
# value is a tuple of (expires_monotonic, model_ids). The TTL (default 5s)
# keeps repeated ``/v1/models`` fans-out cheap during the cold-start race
# window (R4 H3) without making the catalogue stale enough that a freshly
# loaded slot stays invisible to Hermes for long. Use
# ``time.monotonic()`` rather than ``functools.lru_cache`` because the
# stdlib LRU has no time-based expiry.
_HAL0_MODEL_CACHE: dict[str, tuple[float, list[str]]] = {}
_HAL0_MODEL_CACHE_TTL_SECONDS = 5.0
# Fixed key into ``_HAL0_MODEL_CACHE`` — matches the ``model_cache["hal0"]``
# bucket used by /v1/models and the dashboard's synthetic slot tile.
_HAL0_COMPOSITE_CACHE_KEY = "hal0"


def _hal0_model_cache_clear() -> None:
    """Punch the composite catalogue's cached model list.

    Exposed so slot-swap / slot-restart paths can invalidate the cache
    when they know the next call will see a different model. Tests
    also call this to keep state isolated between cases.
    """
    _HAL0_MODEL_CACHE.clear()


async def _fetch_hal0_composite_models(
    slot_manager: SlotManager,
    *,
    now: Callable[[], float] = time.monotonic,
    ttl_seconds: float = _HAL0_MODEL_CACHE_TTL_SECONDS,
) -> list[str]:
    """Aggregate every ready chat-capable slot's model id into one catalogue.

    Direct read over the live slot config — no pseudo-upstream is
    registered in the routing table for this. Container slots register
    their own ``kind="remote"`` upstreams for actual dispatch; this
    aggregation exists purely so ``/v1/models`` and the dashboard's
    synthetic ``hal0`` tile can list every configured chat model in one
    place without an operator having to wire a matching upstreams.toml
    entry per slot.

    The returned list is sorted + deduplicated and cached for
    ``ttl_seconds`` to keep the cold-start fan-out cheap while still
    picking up new slots within a handful of seconds.
    """
    cached = _HAL0_MODEL_CACHE.get(_HAL0_COMPOSITE_CACHE_KEY)
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
    _HAL0_MODEL_CACHE[_HAL0_COMPOSITE_CACHE_KEY] = (monotonic_now + ttl_seconds, list(models))
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


def _model_recipe(entry: Any) -> str | None:
    """Return the curated "recipe" bucket name for a registry ``Model``.

    Mirrors :func:`hal0.registry.model._derive_ns`'s blessed-path rule
    (issue #220: a path is "blessed" iff it sits under
    ``<_BLESSED_PREFIX><recipe>/<capability>/...``) without importing that
    private helper's name-mangled internals — this only reads the same
    published ``_BLESSED_PREFIX`` constant, so the two stay in lockstep by
    construction. Hand-pulled ("pulled") models have no recipe → ``None``.
    """
    path = (getattr(entry, "path", "") or "").strip()
    if not path or not path.startswith(_BLESSED_PREFIX):
        return None
    parts = path[len(_BLESSED_PREFIX) :].split("/")
    if len(parts) < 3 or not parts[0] or not parts[1]:
        return None
    return parts[0]


def hal0_apply_registry_detail(obj: dict[str, Any], entry: Any) -> None:
    """Fold extra registry-row fields (§21.5) onto an OpenAI ``model`` object.

    ``entry`` is a :class:`hal0.registry.model.Model` (or ``None`` when the
    id doesn't resolve in the registry — a hand-staged file, a remote
    passthrough id, …), in which case this is a no-op: the caller's base
    object (id/object/created/owned_by/…) already stands on its own.

    Adds, only when present on ``entry``:

    * ``labels`` — the model's ``capabilities`` list (the same "labels"
      vocabulary ``POST /api/models/add-from-path`` accepts, e.g.
      ``["chat", "vision"]``).
    * ``checkpoint`` — the quantisation label (``Model.quant``, e.g.
      ``"Q4_K_M"``).
    * ``recipe`` — the curated bucket name for a blessed model (see
      :func:`_model_recipe`); omitted for hand-pulled models.
    """
    if entry is None:
        return
    capabilities = getattr(entry, "capabilities", None)
    if capabilities:
        obj["labels"] = list(capabilities)
    quant = getattr(entry, "quant", None)
    if quant:
        obj["checkpoint"] = quant
    recipe = _model_recipe(entry)
    if recipe:
        obj["recipe"] = recipe


async def hal0_slot_alias_models(
    slot_manager: SlotManager,
    model_registry: ModelRegistry,
    *,
    now: int | None = None,
) -> list[dict[str, Any]]:
    """Build OpenAI ``model`` objects for every model-bound chat slot, alias-addressed.

    Each chat slot (``type == "llm"``) with a configured model
    surfaces as one model object whose ``id`` is the slot **alias = slot
    name** (e.g. ``chat``, ``agent``, ``utility``). The alias is the stable
    handle: it does not change when the underlying model is swapped, so
    callers can pin a co-resident slot without tracking the GGUF filename.

    Both warm and cold slots are advertised — dispatch cold-loads on demand
    when a request addresses a cold slot by alias, so restricting discovery
    to only warm slots needlessly hid slots the gateway would happily serve.

    Fields:

    * ``id`` — slot name (the stable alias).
    * ``name`` — ``"<slot> · <model display name>"``; the display name is
      pulled from the model registry when the slot's model id is
      registered, falling back to the bare model id otherwise.
    * ``context_length`` / ``max_context_window`` — the slot's configured
      context window (reading either ``ctx_size`` or ``context_size`` from
      the slot TOML), falling back to the model registry entry's
      ``defaults.context_size``. Both keys carry the same value — some
      OpenAI-compat clients probe ``max_context_window`` instead of the
      canonical ``context_length``.
    * ``owned_by`` — ``"hal0"``.
    * ``downloaded`` — always ``True``: a bound llm slot's configured
      model is, by construction, a real file already resident on this
      host (§21.5).
    * ``labels`` / ``checkpoint`` / ``recipe`` — extra registry-row detail
      (§21.5), only present when the slot's model id resolves in the
      model registry (a hand-staged model that isn't registered omits
      them, same fallback as ``display`` above).

    Slots that are disabled or lack a configured model are omitted.
    """
    created = int(time.time()) if now is None else now
    try:
        cfgs = await slot_manager.iter_configs()
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("v1.slot_alias_iter_failed", error=str(exc))
        return []

    out: list[dict[str, Any]] = []
    for cfg in cfgs:
        if (cfg.get("type") or "").lower() != "llm":
            continue
        slot_name = str(cfg.get("name") or "").strip()
        if not slot_name:
            continue
        model_id = _slot_model_id(cfg)
        if not model_id:
            continue

        display = model_id
        entry: Any = None
        try:
            entry = model_registry.get(model_id)
            registry_name = getattr(entry, "name", "")
            if isinstance(registry_name, str) and registry_name.strip():
                display = registry_name.strip()
        except Exception:
            # Model not in the registry (hand-staged, …) — fall back to
            # the bare model id for the display label.
            entry = None
            display = model_id

        obj: dict[str, Any] = {
            "id": slot_name,
            "object": "model",
            "created": created,
            "owned_by": "hal0",
            "name": f"{slot_name} · {display}",
            # A live llm slot's model is always a real local file
            # (§21.5) — unlike the raw upstream-catalog rows below, there's
            # no "advertised but not pulled" state for a slot alias.
            "downloaded": True,
        }
        ctx = _slot_ctx_size(cfg, model_registry, model_id)
        if ctx is not None:
            obj["context_length"] = ctx
            # Alias some OpenAI-compat clients look for (§21.5); same value.
            obj["max_context_window"] = ctx
        hal0_apply_registry_detail(obj, entry)
        out.append(obj)
    return out


async def hal0_chat_slot_alias_map(slot_manager: SlotManager) -> dict[str, str]:
    """Return ``{slot_alias: model_id}`` for model-bound llm slots.

    The slot **alias** is the slot name. ADR-0023: the canonical llm roles
    are ``agent`` (default anchor) + ``utility`` (helper); any other bound
    llm slot is included by its own name (back-compat alias: ``agent-hermes``).
    Used by the ``/v1`` route layer to translate an alias-addressed request
    into the slot's configured model id before routing, so dispatch resolves
    the correct distinct model. This is a thin translation map, not a routing
    target.

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
        slot_name = str(cfg.get("name") or "").strip()
        if not slot_name:
            continue
        model_id = _slot_model_id(cfg)
        if model_id:
            out.setdefault(slot_name, model_id)
    # Inject back-compat aliases (ADR-0023: only agent-hermes → agent's model_id
    # remains) so requests using old slot names still reach the right model.
    # A literal slot still named like an alias on-disk takes precedence (it was
    # added above via setdefault, so the alias injection below is skipped).
    from hal0.slots.manager import SLOT_ALIASES

    for old_name, new_name in SLOT_ALIASES.items():
        if old_name not in out and new_name in out:
            out[old_name] = out[new_name]
    return out


async def hal0_llm_slot_views(
    slot_manager: SlotManager,
    model_registry: ModelRegistry | None = None,
) -> list[dict[str, Any]]:
    """Return one dict per model-bound llm slot: {name, device, model_id, context_length}.

    Source for normalize.LiveSlotResolver's SlotView list. Mirrors
    hal0_chat_slot_alias_map's iteration but carries device + context (the
    legacy ``role`` field was retired — slot identity is the name).
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
                "device": (cfg.get("device") or "").strip(),
                "model_id": model_id,
                "context_length": int(_slot_ctx_size(cfg, model_registry, model_id) or 0),
            }
        )
    return out


async def hal0_chat_slot_model_ids(slot_manager: SlotManager) -> set[str]:
    """Return the configured model ids of every model-bound chat slot.

    Used by ``GET /v1/models`` to suppress raw chat model-id rows from the
    direct-read composite catalogue so each chat slot is represented exactly
    once — by its alias entry (see :func:`hal0_slot_alias_models`). Unlike
    the alias builder this does NOT filter on loaded state: a chat model
    that the composite catalogue advertises must be deduped regardless of
    whether it's currently warm, so the catalog never shows both an alias
    and a bare ``id=<model_id>`` row for the same slot.

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
        model_id = _slot_model_id(cfg)
        if model_id:
            out.add(model_id)
    return out


async def _prime_hal0_composite_cache(
    upstreams: UpstreamRegistry,
    slot_manager: SlotManager,
    model_cache: dict[str, list[str]],
) -> None:
    """Warm the ``model_cache["hal0"]`` bucket via a direct slot-config read.

    No pseudo-upstream is registered for this — ``/v1/models`` and the
    dashboard's synthetic ``hal0`` tile both read ``model_cache["hal0"]``
    directly (see :func:`_fetch_hal0_composite_models`). Priming it here
    means the first request after startup doesn't have to pay the
    slot-iteration cost.

    Merges rather than replaces: any tag already in the bucket that the
    fresh catalogue read doesn't reproduce (e.g. FLM multiplex tags seeded
    by :func:`_seed_multiplex_models`) is preserved, mirroring
    ``_fetch_and_cache``'s merge behaviour for ordinary upstreams.

    Skipped if an explicit ``upstreams.toml`` entry already claims the
    name ``hal0`` — operator overrides win, and their entry's model cache
    is populated the same way as any other remote upstream (dispatch
    prefetch / ``/api/status`` hydration). Best-effort: failures are
    already logged inside the fetch helper.
    """
    if upstreams.get("hal0") is not None:
        log.info("slots.hal0_composite_skipped", reason="explicit_upstream_registered")
        return
    models = await _fetch_hal0_composite_models(slot_manager)
    existing = model_cache.get("hal0", [])
    merged = list(models)
    for tag in existing:
        if tag not in merged:
            merged.append(tag)
    model_cache["hal0"] = merged


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
    slot_manager: SlotManager,
    fetch_and_cache: Callable[[Upstream], Awaitable[list[str]]],
    model_cache: dict[str, list[str]],
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
            upstream = upstreams.get(slot_name)
            if upstream is not None:
                try:
                    await fetch_and_cache(upstream)
                except Exception as exc:  # pragma: no cover — defensive
                    log.warning(
                        "model_cache.refresh_failed",
                        slot=slot_name,
                        error=str(exc),
                    )
            # Every chat slot's model id also lives in the direct-read
            # composite catalogue (no per-slot upstream needed). Punch its
            # TTL cache and re-prime so the next /v1/models call and the
            # dashboard's synthetic ``hal0`` tile both rediscover the new
            # lineup right away.
            _hal0_model_cache_clear()
            try:
                await _prime_hal0_composite_cache(upstreams, slot_manager, model_cache)
            except Exception as exc:  # pragma: no cover — defensive
                log.warning(
                    "model_cache.hal0_composite_refresh_failed",
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
    ``_prime_hal0_composite_cache``. The multiplex tags are merged into the
    ``hal0`` cache bucket (the direct-read composite catalogue) so the
    dispatcher's passthrough match still picks them up.
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
            registry.upsert(upstream_from_entry(entry))
        except Exception as exc:
            log.warning(
                "upstreams.entry_skipped",
                name=entry.name,
                error=str(exc),
                error_type=type(exc).__name__,
            )


def _auto_resume_interrupted_pulls(app: FastAPI) -> None:
    """Auto-resume pulls that were mid-flight when a prior process died.

    A persisted pull-job snapshot in a non-terminal state (``queued`` or
    ``running``) means the process that owned it never reached a terminal
    state — either a pre-#1225 build that got SIGKILLed by systemd
    mid-download, or a genuine crash. ``run_pull`` only durably persists a
    snapshot at pull START (``queued``) and again at a TERMINAL state
    (``completed``/``failed``/``cancelled``) — there is no mid-flight
    "running" flush — so ``queued`` left on disk is the normal footprint
    of an interrupted download, not just a job that never started.

    The download's own resume sidecar (hal0.registry.pull's Range-request
    support) means picking the pull back up is just calling ``run_pull``
    again with the same ``(model_id, hf_repo, hf_filename)``; this does
    that automatically instead of leaving it to the operator to notice and
    re-POST.

    Scope: only plain HF pulls whose registry row still carries
    ``hf_repo`` + ``hf_filename``, and whose bytes aren't already sitting
    on disk (a terminal persist can itself fail-soft, leaving a stale
    non-terminal snapshot for a pull that actually completed — nothing to
    resume there). FLM/NPU tag pulls and ad-hoc (hand-registered, no HF
    coords) entries are left alone — there is no safe way to resume those
    here, and the reconciliation in
    ``routes.models._reconcile_persisted_pull_job`` already surfaces them
    as ``failed`` (or ``completed``, if the bytes actually landed) on the
    next status poll.
    """
    from pathlib import Path

    from hal0.registry.curated import get_curated
    from hal0.registry.pull import list_persisted_jobs, make_job, persist_pull_job

    registry = app.state.model_registry
    jobs: dict[str, Any] = app.state.model_pull_jobs
    event_bus = getattr(app.state, "events", None)

    for snapshot in list_persisted_jobs():
        if snapshot.get("state") not in ("queued", "running"):
            continue
        model_id = snapshot.get("model_id")
        if not isinstance(model_id, str) or not model_id or model_id in jobs:
            continue
        try:
            entry = registry.get(model_id)
        except Exception:
            continue
        hf_repo = (getattr(entry, "hf_repo", "") or "").strip()
        hf_file = (getattr(entry, "hf_filename", "") or "").strip()
        if not hf_repo or not hf_file:
            continue
        existing_path = (getattr(entry, "path", "") or "").strip()
        if existing_path and Path(existing_path).exists():
            continue  # bytes already landed — the terminal persist just didn't

        caps = list(getattr(entry, "capabilities", None) or [])
        capability = caps[0] if caps else None
        curated = get_curated(model_id)
        comfyui_subdir = (getattr(curated, "comfyui_subdir", "") or "").strip() or None

        job = make_job(model_id)
        jobs[model_id] = job
        persist_pull_job(job)

        from hal0.api.routes.models import _run_pull_with_events, _schedule_pull_task

        hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        _schedule_pull_task(
            app.state,
            model_id,
            _run_pull_with_events(
                job,
                hf_repo=hf_repo,
                hf_file=hf_file,
                registry=registry,
                hf_token=hf_token,
                event_bus=event_bus,
                capability=capability,
                comfyui_subdir=comfyui_subdir,
            ),
        )
        log.info("model.pull_auto_resumed", model_id=model_id, hf_repo=hf_repo, hf_file=hf_file)


_PULL_SHUTDOWN_TIMEOUT_S = 10.0


async def _shutdown_pull_jobs(app: FastAPI, timeout_s: float = _PULL_SHUTDOWN_TIMEOUT_S) -> None:
    """Cancel every in-flight model pull so shutdown doesn't wait on them.

    Issue #1225: pulls run as detached ``asyncio.Task``\\ s (see
    ``routes.models._schedule_pull_task``) precisely so a live download
    doesn't keep an HTTP connection open for the whole transfer — but a
    detached task still has to be told to stop. Cancelling raises
    ``asyncio.CancelledError`` inside the download's chunk loop, which
    (per ``_download_one``'s contract) preserves the partial file and
    writes a resume sidecar, so the next pull attempt — a manual re-POST,
    or the startup auto-resume above — continues from the last complete
    chunk rather than restarting from zero. Bounded so shutdown itself
    stays fast even if a task is slow to unwind.
    """
    app.state.shutting_down.set()
    tasks: dict[str, asyncio.Task[None]] = getattr(app.state, "model_pull_tasks", {}) or {}
    live = [t for t in tasks.values() if not t.done()]
    if not live:
        return
    log.info("hal0.api.shutdown_cancelling_pulls", count=len(live))
    for t in live:
        t.cancel()
    _done, pending = await asyncio.wait(live, timeout=timeout_s)
    if pending:
        log.warning("hal0.api.shutdown_pull_cancel_timed_out", count=len(pending))


@dataclass
class BootPhaseRecord:
    """One boot phase's outcome — surfaced via ``app.state.boot_report``."""

    name: str
    status: str = "ok"  # "ok" | "skip" | "error"
    duration_ms: float = 0.0
    detail: str | None = None


@dataclass
class BootReport:
    """Structured, additive record of what each boot phase did.

    Attached to ``app.state.boot_report`` so a degraded boot is observable
    (which phase ran, how long it took, whether it errored) without changing
    any existing startup behaviour. The report only observes; it never alters
    control flow.
    """

    phases: list[BootPhaseRecord] = field(default_factory=list)

    def record(
        self,
        name: str,
        status: str = "ok",
        duration_ms: float = 0.0,
        detail: str | None = None,
    ) -> None:
        self.phases.append(
            BootPhaseRecord(name=name, status=status, duration_ms=duration_ms, detail=detail)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "phases": [
                {
                    "name": p.name,
                    "status": p.status,
                    "duration_ms": round(p.duration_ms, 3),
                    "detail": p.detail,
                }
                for p in self.phases
            ]
        }


@dataclass
class BootState:
    """Typed container for the ``app.state`` members owned by :func:`lifespan`.

    Replaces the loose ``app.state.<attr> = ...`` soup with a single typed
    record of every runtime object the boot sequence constructs. Each boot
    phase populates its fields here and mirrors the published subset onto
    ``app.state`` (the Starlette read surface every request handler uses) at
    the exact point the original monolithic lifespan did — so behaviour and
    boot order stay byte-for-byte equivalent.

    NOTE: ``app.state`` also carries members owned by ``create_app()`` and the
    routers (e.g. ``memory_provider``, ``mcp_servers``, ``mcp_session_managers``,
    ``approval_queue``, ``realtime_backends``, ``board_store``); those are
    deliberately NOT in this container — this owns only the lifespan-set facts.
    """

    # --- published to app.state (readers use request.app.state.<name>) ---
    upstreams: UpstreamRegistry | None = None
    model_registry: ModelRegistry | None = None
    hal0_config: Any = None
    hardware_probe: HardwareProbe | None = None
    hardware_stats: Any = None
    model_pull_jobs: dict[str, Any] = field(default_factory=dict)
    model_pull_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    runner_image_registry: RunnerImageStore | None = None
    runner_image_pull_jobs: dict[str, Any] = field(default_factory=dict)
    runner_image_pull_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    shutting_down: asyncio.Event | None = None
    slot_pull_jobs: dict[str, Any] = field(default_factory=dict)
    events: EventBus | None = None
    audit: AuditStore | None = None
    audit_epoch: str = ""
    hermes_kanban: Any = None
    upstream_models: dict[str, list[str]] = field(default_factory=dict)
    dispatcher: Dispatcher | None = None
    slot_manager: SlotManager | None = None
    model_cache: dict[str, list[str]] = field(default_factory=dict)
    capability_orchestrator: CapabilityOrchestrator | None = None
    last_used_model: dict[str, str] = field(default_factory=dict)
    tps_events: Any = None
    ttft_events: Any = None
    slot_throughput: dict[str, float] = field(default_factory=dict)
    slot_kv_occupancy: dict[str, float] = field(default_factory=dict)
    slot_request_count: dict[str, int] = field(default_factory=dict)
    slot_last_used: dict[str, float] = field(default_factory=dict)
    metrics_service: Any = None
    metrics_seam: Any = None
    gpu_arbiter_idle_task: asyncio.Task[None] | None = None
    omni_router: Any = None
    npu_trio_router: Any = None

    # --- boot-internal working set (NOT published to app.state) ---
    identity_store: object | None = None
    port_authority: object | None = None
    audit_sink: Any = None
    fetch_and_cache: Any = None
    managers: list[Any] = field(default_factory=list)
    refresh_task: asyncio.Task[None] | None = None
    stop_refresh_task: Any = None
    stop_gpu_arbiter_idle_loop: Any = None
    memory_reprobe_task: asyncio.Task[None] | None = None
    stop_memory_reprobe_task: Any = None
    omni_router_client: httpx.AsyncClient | None = None
    boot_report: BootReport = field(default_factory=BootReport)


async def _run_boot_phase(
    report: BootReport,
    name: str,
    phase: Callable[[], Awaitable[None]],
) -> None:
    """Run one boot phase, recording its outcome + timing on ``report``.

    Re-raises on failure so a phase that must fail the boot (the original
    un-guarded structural steps) still does — the BootReport only observes,
    it never swallows.
    """
    start = time.monotonic()
    try:
        await phase()
    except Exception as exc:
        report.record(name, "error", (time.monotonic() - start) * 1000.0, type(exc).__name__)
        raise
    report.record(name, "ok", (time.monotonic() - start) * 1000.0)


async def _boot_registries(app: FastAPI, ctx: BootState) -> None:
    """Phase — core registries + hardware probe + parsed config + model auto-scan."""
    ctx.upstreams = UpstreamRegistry()
    _hydrate_upstreams(ctx.upstreams)
    ctx.model_registry = ModelRegistry()
    ctx.runner_image_registry = RunnerImageStore()
    ctx.hardware_probe = HardwareProbe()

    # Cache the parsed top-level config so request handlers don't repeatedly
    # re-read hal0.toml. The /api/settings PUT path keeps this in sync.
    try:
        ctx.hal0_config = load_hal0_config()
    except ConfigParseError as exc:
        log.warning("hal0.config.parse_failed", error=str(exc))
        from hal0.config.schema import Hal0Config

        ctx.hal0_config = Hal0Config()

    # Auto-scan configured model roots so a fresh /mnt/ai-models drop-in
    # shows up in the registry without operator intervention.  Failures
    # here must NOT block startup — the API still has to come up so the
    # user can fix the offending root.
    if ctx.hal0_config.models.auto_scan_on_start:
        try:
            scan_result = scan_and_register(ctx.model_registry, ctx.hal0_config.models)
            log.info(
                "models.auto_scan_complete",
                added=len(scan_result.get("added", [])),
                skipped=len(scan_result.get("skipped", [])),
                roots=len(scan_result.get("scanned_roots", [])),
            )
        except Exception as exc:
            log.warning("models.auto_scan_failed", error=str(exc))


async def _boot_model_cache(app: FastAPI, ctx: BootState) -> None:
    """Phase — shared in-process /v1/models cache + its fetch-and-merge helper.

    The dispatcher's cold-cache prefetch path needs ``cached_models()`` and
    ``fetch_models()`` to share state — without this, prefetch fans out then
    re-checks the cache and finds it empty, and every request 404s. No TTL
    yet; ``ctx.model_cache`` persists for the life of the process.
    """

    async def _fetch_and_cache(u: Upstream) -> list[str]:
        models = await ctx.upstreams.fetch_models(u.name)
        # Preserve multiplex tags seeded at startup (e.g. embed-gemma /
        # whisper-v3:turbo on FLM slots). Without this, the dispatcher's
        # cold-cache prefetch overwrites the seeded entries and embed /
        # asr routing breaks until process restart.
        existing = ctx.model_cache.get(u.name, [])
        merged = list(models)
        for tag in existing:
            if tag not in merged:
                merged.append(tag)
        ctx.model_cache[u.name] = merged
        return merged

    ctx.fetch_and_cache = _fetch_and_cache


async def _boot_audit_store(app: FastAPI, ctx: BootState) -> None:
    """Phase — durable audit/activity store + the event-bus sink closure.

    Constructed before the event bus so the bus can forward every emitted
    event into it (the durable mirror). High-frequency pull.progress is
    filtered out of the mirror so it can't evict lifecycle history.
    """
    ctx.audit_epoch = uuid.uuid4().hex
    audit_store: AuditStore | None = None
    if ctx.hal0_config.activity.enabled:
        retention = int(
            os.environ.get("HAL0_ACTIVITY_RETENTION_DAYS")
            or ctx.hal0_config.activity.retention_days
        )
        audit_store = AuditStore(
            activity_db(),
            retention_days=retention,
            max_rows=ctx.hal0_config.activity.max_rows,
        )
        try:
            audit_store.init_schema()
            await audit_store.prune()
        except Exception as exc:  # init must never block startup
            log.warning("activity.init_failed", error=str(exc))
            audit_store = None
    ctx.audit = audit_store

    async def _audit_sink(event: dict[str, Any]) -> None:
        if ctx.audit is None or event.get("type") == "pull.progress":
            return
        await ctx.audit.record_event(event)

    ctx.audit_sink = _audit_sink


async def _boot_slot_manager(app: FastAPI, ctx: BootState) -> None:
    """Phase — event bus + stable-id identity/port authority + SlotManager.

    The event bus is constructed first so the SlotManager can side-channel
    every transition through it (the footer subscribes to /api/events).
    SlotManager is built before the Dispatcher so it can be threaded in.
    """
    ctx.events = EventBus(sink=ctx.audit_sink if ctx.audit is not None else None)
    # rework §11.1/§11.2: wire the stable-id identity bridge + the single port
    # authority into the manager. Best-effort — a DB/init hiccup degrades to the
    # existing name-keyed/TOML-port behaviour rather than blocking boot.
    ctx.identity_store = None
    ctx.port_authority = None
    try:
        from hal0.config.schema import _SLOT_PORT_MAX, _SLOT_PORT_MIN
        from hal0.ports.authority import PortAuthority
        from hal0.slots.identity import SlotIdentityStore

        ctx.identity_store = SlotIdentityStore()
        ctx.port_authority = PortAuthority(
            pool=(_SLOT_PORT_MIN, _SLOT_PORT_MAX),
            reserved={8080: "api"},
        )
        with contextlib.suppress(Exception):
            ctx.port_authority.reserve(8080, label="api")
    except Exception as _exc:  # pragma: no cover - defensive boot guard
        log.warning("slot.identity_store_init_failed", extra={"error": str(_exc)})
        ctx.identity_store = None
        ctx.port_authority = None
    ctx.slot_manager = SlotManager(
        event_bus=ctx.events,
        upstreams_registry=ctx.upstreams,
        identity_store=ctx.identity_store,
        port_authority=ctx.port_authority,
        preload_evict_enabled=ctx.hal0_config.slots.preload_evict_enabled,
        preload_evict_headroom_mb=ctx.hal0_config.slots.preload_evict_headroom_mb,
    )


async def _boot_dispatcher(app: FastAPI, ctx: BootState) -> None:
    """Phase — Dispatcher wired to the shared model cache + slot manager."""
    ctx.dispatcher = Dispatcher(
        upstream_registry=ctx.upstreams,
        model_registry=ctx.model_registry,
        prefetch_timeout_s=ctx.hal0_config.dispatcher.prefetch_timeout_s,
        direct_read_timeout_s=ctx.hal0_config.dispatcher.direct_read_timeout_s,
        prefetch_parallel_cap=ctx.hal0_config.dispatcher.prefetch_parallel_cap,
        cached_models=lambda name: ctx.model_cache.get(name, []),
        fetch_models=ctx.fetch_and_cache,
        slot_manager=ctx.slot_manager,
    )


async def _boot_slot_reconcile(app: FastAPI, ctx: BootState) -> None:
    """Phase — one-shot slot reconciliation passes + idle-monitor start.

    ORDERING: ``migrate_slot_dir`` (#1369) → ``reconcile_unconfigured_slots``
    → ``reconcile_npu_trio_slots`` → ``fold_identity`` (folds identity for the
    trio shadows just reconciled) → ``start_idle_monitor``. Otherwise preserved
    exactly from the monolithic boot.
    """
    # #1369 one-shot sweep, FIRST: every pass below reads ``model.default`` to
    # decide what is configured, so a slot the operator had switched off with
    # ``enabled = false`` (whose model is therefore still bound on disk) would
    # be reconciled as live for one boot. Idempotent — after the first sweep
    # there is no ``enabled`` key left to find. Best-effort: a config-dir
    # problem must not block startup.
    try:
        from hal0.config import paths as _enabled_sweep_paths
        from hal0.config.migrations.slot_enabled_removal import migrate_slot_dir

        migrated = migrate_slot_dir(_enabled_sweep_paths.slots_config_dir())
        if migrated:
            log.info("slot.enabled_removal_swept", slots=migrated)
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("slot.enabled_removal_failed", error=str(exc))

    # One-shot: fold retired behaviour tags (mtp/vision → typed fields) and
    # strip the curated "type" chips' tags from registry rows — nothing
    # routes on Model.tags any more (see hal0.registry.tag_retirement).
    try:
        from hal0.registry.tag_retirement import retire_model_type_tags

        retired = retire_model_type_tags(ctx.model_registry)
        if retired:
            log.info("registry.type_tags_retired_sweep", models=retired)
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("registry.type_tags_retirement_failed", error=str(exc))

    # One-shot reconciliation: clear pre-fix stuck ERROR on slots whose
    # only problem was an empty model.default. After fix(slots): empty
    # default is OFFLINE+CTA, not ERROR; this pass migrates existing
    # state.json snapshots forward so the dashboard doesn't render red
    # until the operator clicks each slot.
    await ctx.slot_manager.reconcile_unconfigured_slots()

    # Reconcile the FLM-trio shadow slots (flm-stt / flm-embed) to canon:
    # rename legacy stt-npu/embed-npu records, normalize device/profile/port/
    # served_by/type, and seed any missing shadow so the slots page + trio
    # dispatch are coherent on fresh installs and after upgrades. No-op when
    # there is no container NPU anchor; best-effort so it never blocks startup.
    try:
        await ctx.slot_manager.reconcile_npu_trio_slots()
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("slot.reconcile_trio_failed", error=str(exc))

    # rework §11.1/§11.2 boot fold: ensure a stable-id ``slot`` row + a seeded
    # ``port_claim`` for every configured slot (incl. the trio shadows just
    # reconciled above). Idempotent + additive — no artefact/unit rename. No-op
    # when the identity store isn't wired. Best-effort so it never blocks boot.
    try:
        await ctx.slot_manager.fold_identity()
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("slot.identity_fold_failed", error=str(exc))

    # Idle monitor — demotes READY → IDLE after the configured timeout
    # (so the dashboard distinguishes "warm but quiet" from "warm and
    # actively serving") AND hard-evicts slots idle past their TTL to free
    # host RAM (#902).  The global default evict TTL comes from
    # slots.idle_timeout_s; per-slot TOML idle_timeout_s overrides it and
    # idle_timeout_s = 0 pins a slot.  Defaults to 300s for tests.
    await ctx.slot_manager.start_idle_monitor(
        evict_after_s=ctx.hal0_config.slots.idle_timeout_s,
        evict_pressure_mb=ctx.hal0_config.slots.evict_pressure_mb,
    )


async def _boot_model_priming(app: FastAPI, ctx: BootState) -> None:
    """Phase — prime composite catalogue, seed multiplex tags, restore upstreams."""
    # Prime the direct-read composite model catalogue (``model_cache["hal0"]``)
    # so /v1/models and the dashboard's synthetic ``hal0`` tile can read it
    # synchronously immediately after startup. Explicit upstreams.toml
    # entries (hydrated above) win — priming skips when the ``hal0`` name
    # is already taken by a real upstream.
    try:
        await _prime_hal0_composite_cache(ctx.upstreams, ctx.slot_manager, ctx.model_cache)
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("upstream.hal0_prime_failed", error=str(exc))
    await _seed_multiplex_models(ctx.upstreams, ctx.slot_manager, ctx.model_cache)

    # #732: re-register per-slot remote upstreams for containers that
    # survived the api restart (the registry is in-memory; the containers
    # are not). Prime each restored upstream's model cache so dispatch
    # routes immediately — no operator unload+load sweep.
    try:
        restored_slots = await ctx.slot_manager.reconcile_container_upstreams()
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("container.upstream_reconcile_failed", error=str(exc))
        restored_slots = []
    for restored_name in restored_slots:
        restored_upstream = ctx.upstreams.get(restored_name)
        if restored_upstream is None:
            continue
        try:
            await ctx.fetch_and_cache(restored_upstream)
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("upstream.reconcile_prime_failed", slot=restored_name, error=str(exc))


async def _boot_pull_registry(app: FastAPI, ctx: BootState) -> None:
    """Phase — publish core registries + pull-job registries, sweep, auto-resume.

    ORDERING INVARIANT: the registries + pull dicts are published to
    ``app.state`` BEFORE ``_auto_resume_interrupted_pulls(app)`` (which reads
    ``app.state.model_registry`` / ``model_pull_jobs`` / ``model_pull_tasks``),
    and ``app.state.events`` is deliberately NOT published until the next
    phase — auto-resume reads ``getattr(app.state, "events", None)`` and MUST
    observe ``None`` here (resumed pulls emit no events), matching the
    original monolithic order.
    """
    from hal0.hardware import HardwareStats

    ctx.hardware_stats = HardwareStats()
    app.state.upstreams = ctx.upstreams
    app.state.model_registry = ctx.model_registry
    app.state.hal0_config = ctx.hal0_config
    app.state.hardware_probe = ctx.hardware_probe
    app.state.hardware_stats = ctx.hardware_stats
    # Model-pull job registry — keyed by model_id, value is the
    # ``PullJob`` dataclass holding live progress + cancel flags. SSE
    # and status routes snapshot ``as_dict()`` rather than hold the
    # dataclass across event-loop ticks.
    app.state.model_pull_jobs = ctx.model_pull_jobs
    # Task handles for the SAME keys (issue #1225). A pull is launched as a
    # detached ``asyncio.Task`` (routes.models._schedule_pull_task), not a
    # Starlette BackgroundTask, specifically so its HTTP request can return
    # immediately instead of keeping the connection open for the whole
    # download — this dict is what lets the shutdown path below (
    # _shutdown_pull_jobs) find and cancel any still-running pull.
    app.state.model_pull_tasks = ctx.model_pull_tasks
    # Runner-image catalogue store + its own pull-job/task dicts, same
    # shape as the model-pull pair above but keyed by GHCR repo path
    # (feat/runner-image-catalogue).
    app.state.runner_image_registry = ctx.runner_image_registry
    app.state.runner_image_pull_jobs = ctx.runner_image_pull_jobs
    app.state.runner_image_pull_tasks = ctx.runner_image_pull_tasks
    # Flipped at the START of shutdown (see the lifespan finally block) so
    # long-lived generators (the pull SSE stream) can notice a restart is in
    # progress and close promptly instead of blocking uvicorn's connection
    # drain indefinitely.
    ctx.shutting_down = asyncio.Event()
    app.state.shutting_down = ctx.shutting_down
    # Reap orphaned *.part staging files left by a SIGKILL/OOM mid-pull
    # (MR-9). Age-gated so an in-flight pull from another worker is never
    # touched; housekeeping must never block startup.
    try:
        from hal0.registry.pull import sweep_orphaned_partials

        sweep_orphaned_partials()
    except Exception as exc:  # housekeeping must never block startup
        log.warning("model.partial_sweep_startup_failed", error=str(exc))
    # Reap stale terminal pull-job snapshots left by deleted / failed /
    # cancelled pulls or older builds (#MR-8). Best-effort — a broken sweep
    # must never block startup.
    try:
        from hal0.registry.pull import sweep_pull_jobs

        reaped = sweep_pull_jobs()
        if reaped:
            log.info("model.pull_jobs_swept", count=reaped)
    except Exception as exc:
        log.warning("model.pull_jobs_sweep_failed", error=str(exc))
    # Auto-resume any pull left mid-flight by a prior hal0-api process that
    # died without a clean shutdown (issue #1225) — e.g. an older build (pre
    # this fix) that got SIGKILLed by systemd, or a hard crash. Best-effort:
    # only resumes plain HF pulls whose registry row still carries hf_repo +
    # hf_filename; anything else is left for the operator to re-POST.
    try:
        _auto_resume_interrupted_pulls(app)
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("model.pull_auto_resume_failed", error=str(exc))
    # Container image-pull job registry — keyed by slot name, value is a
    # dict with keys: state (pulling|completed|failed), layer, total_layers,
    # error, and a threading.Event for SSE fan-out.
    app.state.slot_pull_jobs = ctx.slot_pull_jobs


async def _boot_publish_runtime(app: FastAPI, ctx: BootState) -> None:
    """Phase — publish the event bus, audit, board client + core runtime handles.

    ``app.state.events`` is published HERE (after auto-resume in the pull-
    registry phase), and the ``system.restart`` event is emitted once the bus
    is live.
    """
    # Dashboard footer event bus. Constructed earlier (so SlotManager could
    # be wired with the same instance); published on app.state here so
    # request handlers can reach it via ``request.app.state.events``.
    app.state.events = ctx.events
    # Durable audit/activity store + a per-process epoch so the ActivityLog
    # can detect a restart and reset its cursor (events ids restart at 1).
    app.state.audit = ctx.audit
    app.state.audit_epoch = ctx.audit_epoch
    # Operator Board: thin audited proxy client to the Hermes kanban plugin
    # (loopback :9119). Constructed once per process; the board router funnels
    # every /api/board/* call through it. Resolves HERMES_DASHBOARD_BASE_URL +
    # the Hermes session bearer (env HERMES_SESSION_TOKEN) from from_env().
    from hal0.board import HermesKanbanClient

    ctx.hermes_kanban = HermesKanbanClient.from_env()
    app.state.hermes_kanban = ctx.hermes_kanban
    # KB-5 executor bridge (HP-executor): register the concrete Hermes
    # BoardExecutor ONLY when Hermes is configured (env presence — no network
    # call at startup). Inert otherwise: the KB-5 registry stays empty and the
    # board runs fully with no executor (the seam's shipped state).
    try:
        from hal0.board.hermes_executor import register as _register_hermes_executor

        if _register_hermes_executor(app):
            log.info("board.hermes_executor_registered")
    except Exception as exc:  # optional bridge must never block startup
        log.warning("board.hermes_executor_register_failed", error=str(exc))
    await ctx.events.emit(
        "system.restart",
        "info",
        "system",
        f"hal0 {__version__} starting",
        data={"version": __version__},
    )
    # /api/upstreams hands the dashboard the cached model list so the
    # "models advertised" column reflects live state without an extra
    # round trip per upstream. ``upstream_models`` is the same object as
    # ``model_cache`` so live updates propagate.
    ctx.upstream_models = ctx.model_cache
    app.state.upstream_models = ctx.upstream_models
    app.state.dispatcher = ctx.dispatcher
    app.state.slot_manager = ctx.slot_manager
    app.state.model_cache = ctx.model_cache


async def _boot_seeds(app: FastAPI, ctx: BootState) -> None:
    """Phase — idempotent persona + static-slot fresh-install seeds.

    RELOCATE(brain-lane): folds in two of the five relocated
    hermes_provision install steps — both are local-FS-only, no memory
    call, so they belong here beside the pre-existing seed calls rather
    than in the memory-dependent terminal ``_boot_brain_lane`` phase:

    - ``persona_seed`` — DEDUPED, not re-added. This phase already called
      ``seed_default_personas(root=hermes_home / "personas")`` below
      before persona_seed existed as an install step at all; that call is
      unchanged and IS this relocation for personas — the standalone
      ``hermes_provision._phase_persona_seed`` function is kept only for
      its own direct-call unit coverage (its wider agent_id/overwrite
      handling collapses to the same defaults this call already used).
    - ``brain_profile_mcp_wire`` — genuinely new here (see below).
    """
    # Agent personas — idempotently seed any missing defaults (hermes /
    # coder / hal0-brain). The dashboard's agent-chat slide-out embodies the
    # hal0-brain profile (routes/board_chat), so a box updated from a release
    # that predates a seed must still grow its editable TOML: this seed
    # is the only hook every install path (update, editable/dev, fresh)
    # shares, now that RELOCATE(brain-lane) retired the install-time
    # persona_seed step entirely (see hermes_provision.py). Existing files
    # are never touched (overwrite=False) — there is no boot-time
    # equivalent of `hal0 agent install hermes --repair`'s forced reset;
    # an operator who needs a hard reset removes the persona TOML(s) and
    # restarts hal0-api, or re-runs `--repair` (still installed, just no
    # longer touching personas). The root goes through paths.var_lib()
    # rather than the module's PERSONAS_ROOT constant so HAL0_HOME installs
    # (tests, dev boxes) seed under their own tree instead of the host's
    # /var/lib/hal0 — in FHS production the two resolve identically.
    try:
        from hal0.agents.hermes_provision import mark_home_managed_if_owned
        from hal0.agents.personas import seed_default_personas
        from hal0.config import paths as _hal0_paths

        hermes_home = _hal0_paths.var_lib() / ".hermes"
        # Stamp `.hal0-managed` BEFORE seeding personas. This seed populates
        # HERMES_HOME on every fresh box before `hal0 agent install hermes`
        # runs; without the marker the bootstrap's home-claim guard mistakes
        # hal0's OWN seeded personas for a foreign tree and fatal-aborts every
        # phase ("unclaimed HERMES_HOME"). A genuine foreign tree stays
        # unstamped so capture still requires --adopt (returns False here).
        claimed = await asyncio.to_thread(mark_home_managed_if_owned, hermes_home)
        if not claimed:
            log.info("personas.startup_seed_foreign_home", home=str(hermes_home))

        seeded_personas = await asyncio.to_thread(
            seed_default_personas,
            root=hermes_home / "personas",
        )
        if seeded_personas:
            log.info("personas.startup_seed", ids=[p.id for p in seeded_personas])
    except Exception as exc:  # seeding must never block startup
        log.warning("personas.startup_seed_failed", error=str(exc))

    # Static slot seeds (flm/tts/rerank/utility/img/agent/brain) — same
    # fresh-install-only gap as the persona seed above: install.sh's copy
    # loop never re-runs on `hal0 update`, so a box upgrading past a
    # release that added a new seed (e.g. `brain`, the dashboard
    # steward's slot) never grows the file. Copy-if-absent; an existing
    # <name>.toml (operator edit or prior seed) is never touched.
    try:
        from hal0.install.static_seeds import seed_static_slots

        # P3-runtime-db inc3 (seed split-brain fix): thread the identity
        # store's known names in. This phase runs AFTER slot_reconcile+
        # fold_identity, so a slot migrated to id-keying (living at <id>.toml,
        # with NO <name>.toml) is already an identity row here — passing its
        # name lets the seeder skip it instead of re-materialising a stale
        # <name>.toml beside the id-keyed file.
        existing_names = ctx.slot_manager.identity_names()
        seeded_slots = await asyncio.to_thread(seed_static_slots, existing_names=existing_names)
        if seeded_slots:
            log.info("slots.startup_seed", names=seeded_slots)
            # GH #1475: this phase runs AFTER slot_reconcile's fold_identity
            # (see _boot_slot_reconcile), so a slot just seeded above has no
            # identity row for the rest of THIS boot — a name-keyed file
            # sitting beside every other slot's <id>.toml, unregistered
            # until the next restart. That's the exact state #1422 reports
            # as duplicate /api/slots entries. Re-fold (idempotent +
            # additive — no artefact/unit rename, same as the original
            # call) so the freshly-seeded slot(s) get a row immediately.
            try:
                await ctx.slot_manager.fold_identity()
            except Exception as exc:  # pragma: no cover — defensive
                log.warning("slots.startup_seed_refold_failed", error=str(exc))
    except Exception as exc:  # seeding must never block startup
        log.warning("slots.startup_seed_failed", error=str(exc))

    # RELOCATE(brain-lane): brain_profile_mcp_wire — deep-merge hal0's two
    # MCP servers (hal0-admin, hal0-memory) + memory.provider into the
    # hal0-brain hermes profile's config.yaml, reproducibly. Local FS only
    # (reads/writes ~/.hermes/profiles/hal0-brain/config.yaml, no memory
    # call), so it runs here instead of the memory-dependent
    # _boot_brain_lane phase. Skips when the profile config is absent (the
    # upstream hermes binary owns profile creation) or PyYAML is missing;
    # only rewrites when the merged content actually differs, so a
    # correctly configured box is left byte-untouched on every restart.
    try:
        from hal0.agents.hermes_provision import BootstrapState as _BrainWireState
        from hal0.agents.hermes_provision import InstallIO as _BrainWireIO
        from hal0.agents.hermes_provision import _phase_brain_profile_mcp_wire
        from hal0.agents.hermes_provision import _StepCtx as _BrainWireCtx
        from hal0.config import paths as _hal0_paths_wire

        wire_home = _hal0_paths_wire.var_lib() / ".hermes"
        wire_result = await asyncio.to_thread(
            _phase_brain_profile_mcp_wire,
            _BrainWireCtx(state=_BrainWireState(hermes_home=str(wire_home)), io=_BrainWireIO()),
        )
        if wire_result.details.get("wired") and wire_result.details.get("changed"):
            log.info("brain_profile.mcp_wire_startup_seed", path=wire_result.details.get("path"))
    except Exception as exc:  # seeding must never block startup
        log.warning("brain_profile.mcp_wire_startup_seed_failed", error=str(exc))


async def _boot_capabilities(app: FastAPI, ctx: BootState) -> None:
    """Phase — capability orchestrator overlay (built after slots + registry)."""
    # Capability orchestrator — overlay that maps the dashboard's
    # capability-grouped children (embed/voice/img) onto regular slots.
    # The orchestrator is intentionally constructed AFTER the slot
    # manager + registry are ready so initialize_if_missing() can lift
    # current slot config into capabilities.toml on first boot.
    ctx.capability_orchestrator = CapabilityOrchestrator(
        slot_manager=ctx.slot_manager,
        registry=ctx.model_registry,
    )
    try:
        await ctx.capability_orchestrator.initialize_if_missing()
    except Exception as exc:
        # Never let an overlay seeding failure block API startup — the
        # dashboard can still hit GET /api/capabilities and see empty
        # selections, which is the correct "blank slate" UX.
        log.warning("capabilities.init_failed", error=str(exc))
    app.state.capability_orchestrator = ctx.capability_orchestrator


async def _boot_metrics_state(app: FastAPI, ctx: BootState) -> None:
    """Phase — per-slot metric registries + the SQLite MetricsService."""
    # Tracks the most recent model id sent to each upstream so the
    # dashboard's synthetic slot reflects current usage instead of the
    # first-non-alias from the catalog. Populated by v1 routes after
    # dispatch resolves.
    app.state.last_used_model = ctx.last_used_model
    # Per-slot rolling window of (monotonic_ts, tokens_in_chunk) tuples
    # measured on the streaming forward path. Keyed by the dispatcher's
    # `call.upstream_name` (a slot name for local slots, an upstream id
    # for remote providers) so /api/slots/metrics can attribute current
    # tok/s to the right SlotCard. A defaultdict so any new slot name
    # picks up its own bounded deque without route-side bookkeeping.
    import collections

    def _new_tps_deque() -> collections.deque[tuple[float, int]]:
        return collections.deque(maxlen=4096)

    ctx.tps_events = collections.defaultdict(_new_tps_deque)
    app.state.tps_events = ctx.tps_events

    def _new_ttft_deque() -> collections.deque[tuple[float, float]]:
        return collections.deque(maxlen=128)

    ctx.ttft_events = collections.defaultdict(_new_ttft_deque)
    app.state.ttft_events = ctx.ttft_events

    # FLM / NPU per-slot metrics — updated by v1._record_nonstreaming_throughput
    # when the upstream (FLM container) returns decoding_speed_tps and
    # kv_token_occupancy_rate_percentage in the usage block.
    app.state.slot_throughput = ctx.slot_throughput
    app.state.slot_kv_occupancy = ctx.slot_kv_occupancy
    app.state.slot_request_count = ctx.slot_request_count
    app.state.slot_last_used = ctx.slot_last_used

    # OBS-1 (§13): the SQLite metrics core. Does NOT replace the deques
    # above -- those keep feeding /api/stats/throughput/history and
    # /api/slots/metrics unchanged -- it observes the same v1 request seam
    # a second time and persists an exact request_metric row, plus runs
    # the T2 per-slot sampler as a background task. Construction never
    # touches the filesystem; ``start()`` (inside the AsyncExitStack below)
    # applies the 001/002 migrations and launches the background tasks.
    # ``[metrics].enabled = false`` (or HAL0_METRICS_ENABLED=0) makes every
    # background task a no-op — never blocks startup either way.
    from hal0.metrics.service import MetricsService

    ctx.metrics_service = MetricsService(slot_manager=ctx.slot_manager, registry=ctx.model_registry)
    app.state.metrics_service = ctx.metrics_service
    ctx.metrics_seam = ctx.metrics_service.seam
    app.state.metrics_seam = ctx.metrics_seam


async def _boot_background_tasks(app: FastAPI, ctx: BootState) -> None:
    """Phase — MCP session managers + refresh / GPU-arbiter loops + omni & NPU routers.

    The MCP session managers, refresh task and GPU-arbiter idle loop are set up
    here; their stop callbacks live on ``ctx`` so the orchestrator's
    AsyncExitStack can register them for shutdown.
    """
    log.info(
        "hal0.api.upstreams_loaded",
        count=len(ctx.upstreams.list()),
        names=[u.name for u in ctx.upstreams.list()],
    )

    # Each mounted FastMCP server has a ``StreamableHTTPSessionManager``
    # whose anyio task group must be started inside an async-context
    # before any request can be dispatched. Mounted sub-apps don't get
    # their own lifespans run automatically, so we enter each manager's
    # ``run()`` ctxmgr from the parent lifespan via an AsyncExitStack.
    # Without this every /mcp/* request fails with
    # ``Task group is not initialized``.
    ctx.managers = getattr(app.state, "mcp_session_managers", []) or []

    ctx.refresh_task = asyncio.create_task(
        _refresh_model_cache_on_ready(
            ctx.events, ctx.upstreams, ctx.slot_manager, ctx.fetch_and_cache, ctx.model_cache
        )
    )

    async def _stop_refresh_task() -> None:
        ctx.refresh_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ctx.refresh_task

    ctx.stop_refresh_task = _stop_refresh_task

    # GpuArbiter idle-restore loop (Phase D, Task D6). Auto-restores the
    # saved LLM set after the img (ComfyUI) slot idles out — window from the
    # img slot's ``[image].idle_restore_minutes`` (default 60; 0 = manual-only).
    # Mirrors the refresh_task pattern above: created at startup, cancelled +
    # awaited on shutdown via the AsyncExitStack. Guarded so an arbiter
    # construction failure never blocks API startup (omni-router precedent).
    gpu_arbiter_idle_task: asyncio.Task[None] | None = None
    try:
        gpu_arbiter_idle_task = asyncio.create_task(ctx.slot_manager.arbiter.run_idle_loop())
        log.info("gpu_arbiter.idle_loop_started")
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("gpu_arbiter.idle_loop_start_failed", error=str(exc))
    ctx.gpu_arbiter_idle_task = gpu_arbiter_idle_task
    app.state.gpu_arbiter_idle_task = ctx.gpu_arbiter_idle_task

    async def _stop_gpu_arbiter_idle_loop() -> None:
        if ctx.gpu_arbiter_idle_task is not None:
            ctx.gpu_arbiter_idle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ctx.gpu_arbiter_idle_task

    ctx.stop_gpu_arbiter_idle_loop = _stop_gpu_arbiter_idle_loop

    # Memory provider self-heal loop (#1613). Armed only when create_app
    # wrapped a boot-degraded provider in SelfHealingMemoryProvider; polls
    # until the hindsight engine answers a probe, then the shell swaps its
    # delegate in place and the loop exits. The probe is sync + bounded
    # (HAL0_HINDSIGHT_PROBE_TIMEOUT_S), run off-loop via to_thread.
    ctx.memory_reprobe_task = None
    _mem_provider = getattr(app.state, "memory_provider", None)
    if hasattr(_mem_provider, "try_heal"):
        _reprobe_interval = float(os.environ.get("HAL0_MEMORY_REPROBE_INTERVAL_S", "30") or "30")

        async def _memory_reprobe_loop(provider: Any = _mem_provider) -> None:
            while True:
                await asyncio.sleep(_reprobe_interval)
                if await asyncio.to_thread(provider.try_heal):
                    return

        ctx.memory_reprobe_task = asyncio.create_task(_memory_reprobe_loop())
        log.info("hal0.memory.reprobe_loop_started", interval_s=_reprobe_interval)

    async def _stop_memory_reprobe_task() -> None:
        if ctx.memory_reprobe_task is not None:
            ctx.memory_reprobe_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ctx.memory_reprobe_task

    ctx.stop_memory_reprobe_task = _stop_memory_reprobe_task

    # OmniRouter (PR-16). Client-side OpenAI
    # tool-calling loop. Wired here so the /v1/chat/completions route
    # can pick it up via ``request.app.state.omni_router`` when a
    # request body carries ``omni: true``. The router holds a
    # dedicated httpx client so its lifetime is decoupled from the
    # dispatcher's pool. Chat completions re-enter hal0's own /v1
    # surface (#709) so the full dispatch chain — GpuArbiter
    # image-mode guard, readiness gates, container routing — applies
    # to omni traffic too.
    ctx.omni_router_client = None
    try:
        from hal0.omni_router import OmniRouter

        ctx.omni_router_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0),
            follow_redirects=False,
        )
        api_base_url = os.environ.get("HAL0_SELF_BASE_URL", "http://127.0.0.1:8080")
        ctx.omni_router = OmniRouter(
            slot_manager=ctx.slot_manager,
            http_client=ctx.omni_router_client,
            api_base_url=api_base_url,
        )
        app.state.omni_router = ctx.omni_router
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
        ctx.omni_router = None
        app.state.omni_router = None

    # NPU trio router. The containerized npu
    # slot's single ``flm serve`` process answers chat + STT + embed on
    # one static port; chat routes through the slot upstream like any
    # other slot, while v1.py's STT/embed routes post the two shadow
    # roles straight to the container when they detect an enabled
    # ``flm-stt`` / ``flm-embed`` slot record. Degrades cleanly when the
    # container isn't dispatchable (NpuTrioNotAvailable raised at
    # dispatch time so the user sees a clear envelope).
    try:
        from hal0.dispatcher.npu_trio import NpuTrioRouter

        ctx.npu_trio_router = NpuTrioRouter(slot_manager=ctx.slot_manager)
        app.state.npu_trio_router = ctx.npu_trio_router
        log.info("npu_trio.attached")
    except Exception as exc:
        log.warning(
            "npu_trio.start_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        ctx.npu_trio_router = None
        app.state.npu_trio_router = None


# RELOCATE(brain-lane): namespace_register, brain_profile_seed, and
# self_report (hermes_provision.py) used to run once at `hal0 agent install
# hermes` time. They now run on every hal0-api boot instead, via the
# terminal ``_boot_brain_lane`` phase below. All three call out through
# hermes_provision's ``ctx.io.mcp_memory_call`` seam, whose production
# default (``_mcp_memory_call``) POSTs to ``/api/memory/*`` over loopback
# HTTP (http://127.0.0.1:8080) — that only works once uvicorn's socket is
# bound and accepting connections. uvicorn.Server.startup() runs
# ``await self.lifespan.startup()`` BEFORE it creates the listening socket,
# so at every point during lifespan startup (including every boot phase in
# this module) that socket does not exist yet — reusing ``_mcp_memory_call``
# as-is here would always fail with connection-refused, regardless of which
# phase it ran in.
#
# ``_boot_memory_dispatch`` / ``_boot_mcp_memory_call`` below are a drop-in
# substitute for hermes_provision's IO seam that instead reaches the memory
# provider IN-PROCESS — the same seam
# ``hal0.dispatcher.memory_dispatcher.MemoryDispatcher`` uses so the admin
# MCP server's own memory_* tools skip the loopback tax. That dispatcher
# isn't reused directly because it's bound to AMBIENT per-request resolvers
# (Bearer/header derived); there is no HTTP request during boot, so those
# would resolve to "anonymous"/non-private and silently mis-stamp the
# agent identity. Building a fresh ``make_dispatcher(...)`` per call with
# explicit ``agent_id``/``private`` reproduces exactly what the HTTP path's
# ``X-hal0-Agent`` / ``X-hal0-Private`` headers used to do.
async def _boot_memory_dispatch(
    app: FastAPI,
    method: str,
    params: dict[str, Any],
    *,
    agent_id: str,
    private: bool = False,
) -> dict[str, Any]:
    """Boot-time, in-process substitute for ``hermes_provision._mcp_memory_call``.

    Same ``{"ok": bool, "result": ...}`` / ``{"ok": False, "error": ...}``
    envelope the hermes_provision phase bodies already expect, so they need
    no changes to consume this instead of the HTTP-based default.
    """
    if method != "tools/call" or not isinstance(params, dict):
        return {"ok": False, "error": f"unsupported method {method!r}"}
    tool = params.get("name")
    if tool not in ("memory_search", "memory_add", "memory_delete"):
        return {"ok": False, "error": f"unsupported tool {tool!r}"}
    arguments = params.get("arguments") or {}
    memory_provider = getattr(app.state, "memory_provider", None)
    if memory_provider is None:
        # [memory].enabled = false, or provider construction failed in
        # create_app() — same warn-as-OK degradation the HTTP path hits
        # when hal0-memory is unreachable.
        return {"ok": False, "error": "memory provider not configured"}

    from hal0.mcp.memory import make_dispatcher

    dispatcher = make_dispatcher(
        memory_provider,
        client_id_resolver=lambda: agent_id,
        private_resolver=lambda: private,
    )
    try:
        result = await dispatcher(tool, arguments)
    except Exception as exc:  # pragma: no cover — defensive; make_dispatcher already catches
        return {"ok": False, "error": str(exc)}
    if result.get("status") == "ok":
        return {"ok": True, "result": {k: v for k, v in result.items() if k != "status"}}
    error = result.get("error") or {}
    detail = error.get("detail") or error.get("code") or "memory dispatch failed"
    return {"ok": False, "error": detail}


def _boot_mcp_memory_call(
    app: FastAPI,
    loop: asyncio.AbstractEventLoop,
    method: str,
    params: dict[str, Any],
    *,
    agent_id: str,
    base_url: str = "http://127.0.0.1:8080",
    timeout: float = 5.0,
    private: bool = False,
) -> dict[str, Any]:
    """Sync bridge matching ``InstallIO.mcp_memory_call``'s callable shape.

    The hermes_provision phase bodies (``_phase_namespace_register`` et al.)
    are plain synchronous functions run off the event-loop thread via
    ``asyncio.to_thread`` (see ``_boot_brain_lane``) — required because
    blocking that worker thread on ``future.result()`` below would deadlock
    if it were the loop's own thread. From the worker thread this submits
    :func:`_boot_memory_dispatch` onto the SAME running loop the memory
    provider was constructed on (``run_coroutine_threadsafe``) and blocks
    only the worker thread for the result — never the loop. Bounded so a
    hung memory engine can never block boot indefinitely; ``base_url`` is
    accepted (unused) purely for call-site / signature compatibility with
    ``_mcp_memory_call``.
    """
    del base_url
    coro = _boot_memory_dispatch(app, method, params, agent_id=agent_id, private=private)
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=timeout + 5.0)
    except Exception as exc:
        future.cancel()
        return {"ok": False, "error": str(exc)}


async def _boot_brain_lane(app: FastAPI, ctx: BootState) -> None:
    """Phase — RELOCATE(brain-lane): identity-card + self-report memory publishes.

    Runs LAST (after ``background_tasks``, the final named phase before this
    one) so ``app.state``/``ctx.boot_report`` reflect a fully-booted process
    by the time ``self_report`` writes its summary. Reuses hermes_provision's
    ``_phase_namespace_register`` / ``_phase_brain_profile_seed`` /
    ``_phase_self_report`` bodies UNCHANGED via the same ``InstallIO`` /
    ``_StepCtx`` injection seam their tests already use — only
    ``mcp_memory_call`` is swapped for the boot-safe in-process adapter
    above (see its docstring for why the HTTP-loopback default can't be
    reused during lifespan startup). All three are warn-as-OK: memory-layer
    unavailability must never fail boot, matching their install-time posture.

    ``self_report`` originally read ``ctx.output_of("smoke_tests")`` for its
    failure rollup — there is no lifespan analogue to a smoke-test pass, so
    this substitutes a ``{"failures": [<phase name>, ...]}`` shape built
    from ``ctx.boot_report``'s phases that ended in ``status == "error"``.
    This is a DESIGN SUBSTITUTION, not a real smoke test: it reports boot
    *phase* failures (a phase that had to raise), not a functional
    self-test of chat/memory/etc. Flagged here and in the relocation
    handoff notes; a future lane could add a lightweight post-boot
    self-check if a closer smoke_tests equivalent is wanted.
    """
    import functools

    from hal0.agents.hermes_provision import (
        BootstrapState,
        InstallIO,
        _phase_brain_profile_seed,
        _phase_namespace_register,
        _phase_self_report,
        _StepCtx,
    )

    state = BootstrapState()
    loop = asyncio.get_running_loop()
    io = InstallIO(mcp_memory_call=functools.partial(_boot_mcp_memory_call, app, loop))

    try:
        ns_result = await asyncio.to_thread(_phase_namespace_register, _StepCtx(state=state, io=io))
        if not ns_result.details.get("registered"):
            log.info(
                "brain_lane.namespace_register_degraded",
                warnings=ns_result.details.get("warnings"),
            )
    except Exception as exc:  # pragma: no cover — defensive, must never block boot
        log.warning("brain_lane.namespace_register_failed", error=str(exc))

    try:
        brain_result = await asyncio.to_thread(
            _phase_brain_profile_seed, _StepCtx(state=state, io=io)
        )
        if not brain_result.details.get("registered"):
            log.info(
                "brain_lane.brain_profile_seed_degraded",
                warnings=brain_result.details.get("warnings"),
            )
    except Exception as exc:  # pragma: no cover — defensive, must never block boot
        log.warning("brain_lane.brain_profile_seed_failed", error=str(exc))

    # self_report MUST run last — see docstring above re: the smoke_tests
    # substitution.
    boot_failures = [p.name for p in ctx.boot_report.phases if p.status == "error"]
    prior = {"smoke_tests": {"failures": boot_failures}}
    try:
        report_result = await asyncio.to_thread(
            _phase_self_report, _StepCtx(state=state, io=io, _prior=prior)
        )
        if not report_result.details.get("published"):
            log.info(
                "brain_lane.self_report_degraded",
                warning=report_result.details.get("warning"),
            )
    except Exception as exc:  # pragma: no cover — defensive, must never block boot
        log.warning("brain_lane.self_report_failed", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application boot/shutdown, decomposed into named, ordered boot phases.

    Behaviour and boot order are byte-for-byte equivalent to the original
    monolithic lifespan — each phase runs in the same sequence and publishes
    the same ``app.state`` members at the same relative point. State flows
    through a single typed :class:`BootState` container (``ctx``) instead of
    loose locals + ``app.state`` attribute soup. Phases are wrapped by
    :func:`_run_boot_phase` for an additive :class:`BootReport`
    (``app.state.boot_report``); the report only observes — a phase that must
    fail the boot still raises.
    """
    log.info("hal0.api.startup", version=__version__)

    ctx = BootState()
    report = ctx.boot_report
    app.state.boot_report = report

    await _run_boot_phase(report, "registries", lambda: _boot_registries(app, ctx))
    await _run_boot_phase(report, "model_cache", lambda: _boot_model_cache(app, ctx))
    await _run_boot_phase(report, "audit_store", lambda: _boot_audit_store(app, ctx))
    await _run_boot_phase(report, "slot_manager", lambda: _boot_slot_manager(app, ctx))
    await _run_boot_phase(report, "dispatcher", lambda: _boot_dispatcher(app, ctx))
    await _run_boot_phase(report, "slot_reconcile", lambda: _boot_slot_reconcile(app, ctx))
    await _run_boot_phase(report, "model_priming", lambda: _boot_model_priming(app, ctx))
    await _run_boot_phase(report, "pull_registry", lambda: _boot_pull_registry(app, ctx))
    await _run_boot_phase(report, "publish_runtime", lambda: _boot_publish_runtime(app, ctx))
    await _run_boot_phase(report, "seeds", lambda: _boot_seeds(app, ctx))
    await _run_boot_phase(report, "capabilities", lambda: _boot_capabilities(app, ctx))
    await _run_boot_phase(report, "metrics_state", lambda: _boot_metrics_state(app, ctx))
    await _run_boot_phase(report, "background_tasks", lambda: _boot_background_tasks(app, ctx))
    # RELOCATE(brain-lane): terminal phase — namespace_register,
    # brain_profile_seed, self_report (in that order; self_report last).
    # Runs after every other phase so its self-report reflects a fully
    # booted process. See _boot_brain_lane's docstring.
    await _run_boot_phase(report, "brain_lane", lambda: _boot_brain_lane(app, ctx))

    from contextlib import AsyncExitStack

    try:
        async with AsyncExitStack() as stack:
            for mgr in ctx.managers:
                await stack.enter_async_context(mgr.run())
            stack.push_async_callback(ctx.stop_refresh_task)
            stack.push_async_callback(ctx.stop_gpu_arbiter_idle_loop)
            stack.push_async_callback(ctx.stop_memory_reprobe_task)
            ctx.metrics_service.start()
            stack.push_async_callback(ctx.metrics_service.stop)
            yield
    finally:
        # First — issue #1225: cancel any in-flight model pull before doing
        # anything else, so a live multi-GB download doesn't keep this
        # shutdown (and thus `systemctl restart hal0-api`) waiting.
        with contextlib.suppress(Exception):
            await _shutdown_pull_jobs(app)
        with contextlib.suppress(Exception):
            from hal0.registry.runner_pull_jobs import shutdown_pull_jobs as _shutdown_runner_pulls

            await _shutdown_runner_pulls(app.state)
        if ctx.omni_router_client is not None:
            with contextlib.suppress(Exception):
                await ctx.omni_router_client.aclose()
        await ctx.slot_manager.stop_idle_monitor()
        await ctx.dispatcher.aclose()
        with contextlib.suppress(Exception):
            await comfyui.aclose_client()
        log.info("hal0.api.shutdown")


def create_app() -> FastAPI:
    # First statement in the factory, so an exception raised while BUILDING
    # the app (router import, middleware construction) is already
    # reportable. Inert unless HAL0_SENTRY_DSN is set — see
    # hal0.observability.sentry.
    sentry.init_sentry("api")

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

    # KB-1 / §1: deny-by-default auth gate. Installed here -- right after
    # log_scrub, before any app.include_router() call -- so every request
    # (http incl. SSE, and websocket) passes through path classification
    # (hal0.security.exposure, seam S9) before it can reach a route
    # handler. Dev-open (loopback + no keys configured) bypasses this
    # entirely, which is what keeps the pre-existing TestClient suite
    # green without every test needing to know auth exists; see
    # AuthEnforcementMiddleware / require_auth_enabled docstrings.
    app.add_middleware(AuthEnforcementMiddleware)

    # KB-1 hardening: per-IP brute-force throttle for POST /api/auth/login.
    # One limiter per app instance (fresh per create_app() so tests stay
    # isolated); the login route reads it off app.state and meters every
    # attempt before the key compare. Budget tunes via HAL0_LOGIN_RATELIMIT_*.
    from hal0.security.ratelimit import login_limiter_from_env

    app.state.login_limiter = login_limiter_from_env()

    # /api/auth: login (mints the admin-equivalent session cookie via the
    # SAME HMAC cookie agents/_auth.py already ships) + status (posture
    # report for the dashboard's own auth gate). Both routes are OPEN in
    # security/exposure.py -- you can't require a cookie to obtain one.
    app.include_router(auth_routes.router, prefix="/api/auth", tags=["auth"])

    # /v1 is split into a public probe (GET /v1/models + /v1/models/{id})
    # and a writer surface that requires auth. The split lives in v1.py
    # via v1.public_router (probes) + v1.router (inference). OpenAI
    # clients historically GET /v1/models before sending an Authorization
    # header — keeping that probe auth-free preserves SDK compatibility.
    app.include_router(v1.public_router, prefix="/v1", tags=["v1"])
    app.include_router(v1.router, prefix="/v1", tags=["v1"])
    # WS /v1/realtime — OpenAI Realtime surface (HP-realtime inc-1). CLIENT tier
    # (exposure.py "realtime ws" row); reaches STT/TTS/chat over loopback only.
    app.include_router(realtime_routes.router, prefix="/v1", tags=["realtime"])

    # /api/install drives the first-run wizard. Auth was removed
    # so these endpoints are open; the installer surface is admin-only by
    # convention (network-level access control).
    app.include_router(
        installer.router,
        prefix="/api/install",
        tags=["installer"],
    )
    app.include_router(slots.router, prefix="/api/slots", tags=["slots"])
    # Global port-claim map (hal0.ports registry) — slots, runtime rows,
    # reserved ports, live listeners, conflicts, next-free.
    app.include_router(ports_routes.router, prefix="/api/ports", tags=["ports"])
    # Read-only ComfyUI "generation engine" status for the slots-page Image-Gen
    # tab (docker + systemd + ComfyUI HTTP), plus arbiter switchover controls.
    app.include_router(comfyui.router, prefix="/api/comfyui", tags=["comfyui"])
    # hardware.router is registered BEFORE models.router: it owns the
    # literal path GET /api/models/health (OBS-1 §21.3), which must match
    # before models.router's GET /api/models/{model_id} catch-all — routes
    # are tried in registration order, so the more specific literal path
    # has to land first or every request to /api/models/health 404s as a
    # "model 'health' not found" lookup instead.
    app.include_router(hardware.router, prefix="/api", tags=["hardware"])
    app.include_router(models.router, prefix="/api/models", tags=["models"])
    # Runner Image catalogue — subpage of Models (feat/runner-image-catalogue).
    app.include_router(
        runner_images_routes.router, prefix="/api/runner-images", tags=["runner-images"]
    )
    # Issue #311: HuggingFace Hub discovery (search proxy). Sits next
    # to the models surface so the dashboard's "Search HF" button has a
    # backend to call; the inspect endpoint already lives under
    # /api/models/inspect and is a *different* flow (known coord →
    # variants) than this search proxy (free-text → coord candidates).
    app.include_router(hf.router, prefix="/api/hf", tags=["hf"])
    # Dashboard-overhaul backend endpoints (CONTRACTS.md §2):
    #   throughput.router → GET /api/stats/throughput/history (bucketed tps_events)
    #   services_health.router → GET /api/services/health
    #   dashboard_layout.router → GET/PUT /api/user/dashboard-layout (file-backed)
    app.include_router(throughput.router, prefix="/api", tags=["stats"])
    app.include_router(power.router, prefix="/api", tags=["stats"])
    # GET /api/doctor — doctor verdict feed (D6 diagnostics panel). GET
    # /api/stats/requests (dispatcher rollup, D5 requests card) lives on
    # hardware.router above -- already mounted at prefix="/api".
    app.include_router(doctor.router, prefix="/api", tags=["doctor"])
    app.include_router(services_health.router, prefix="/api/services", tags=["services"])
    # Services management page (registry-driven detail + lifecycle + mDNS):
    #   services_routes.router → GET /api/services, POST /api/services/{id}/action,
    #   GET/POST /api/services/mdns — see hal0/services/ for the registry.
    app.include_router(services_routes.router, prefix="/api/services", tags=["services"])
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
    # memory.graph gate + status. Mounted under /api/memory
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

    # hal0-brain steward chat (SPEC §G / R4) — PRIMARY route. First-class
    # brain engine (hal0.brain.chat) with zero Hermes/board import dependency;
    # /api/board/chat is a thin alias into the same engine. Mounted PRE-dashboard
    # so /api/brain/chat (SSE) is not shadowed by the SPA fallback. ADMIN-gated
    # (deny-by-default) via hal0.security.exposure's /api/brain rule.
    app.include_router(brain_routes.router, prefix="/api/brain", tags=["brain"])

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
    # public (e.g. /api/status, /api/config/urls). Auth was removed;
    # all endpoints on this server are open on the local network.
    app.include_router(health.router, prefix="/api", tags=["health"])
    # Static backend vocabulary (GET /api/meta/enums) — the canonical
    # device/backend/capability enums from hal0.model_meta, read once by the
    # dashboard at startup. Code-level constants only; no auth.
    app.include_router(meta_routes.router, prefix="/api/meta", tags=["meta"])
    app.include_router(config_routes.router, prefix="/api/config", tags=["config"])

    # Profile catalog — read-only, no auth. Returns every profile
    # from /etc/hal0/profiles.toml (falling back to the built-in seeds on a
    # fresh install). Profiles are P1 container-runtime templates (issue #653).
    app.include_router(profiles_routes.router, prefix="/api/profiles", tags=["profiles"])

    # Stack catalog — named, portable bundles of slots + profiles + model
    # assignments + capability selections. Read + declarative apply (dry-run
    # diff → atomic commit → lifecycle converge) + export/import/snapshot.
    # Public on the local network, same rationale as profiles.
    app.include_router(stacks_routes.router, prefix="/api/stacks", tags=["stacks"])

    # Benchmarks — roster board, run detail, history, evals, and the run-queue
    # actions (queue/control; the API process never drives the GPU — the
    # `hal0 bench worker` service drains the queue). Public on the local
    # network (same rationale as profiles/stacks: admin-only by network
    # convention, no creds). Router carries its own /api/benchmarks prefix.
    app.include_router(benchmarks_routes.router)

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

    # Bundled-agent lifecycle. Install / uninstall / list /
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
    # so a hal0 install without a memory engine still renders sensibly.
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

    # Approval inbox. The dashboard bell, the MCP admin
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
    # 501 — future ``mcp_client.py`` work owns those.
    app.include_router(
        mcp_routes.router,
        prefix="/api/mcp",
        tags=["mcp"],
    )

    # OpenRouter OAuth callback scaffold (Phase 0).  The route
    # is gated behind HAL0_OPENROUTER_OAUTH_ENABLED so the 501 placeholder
    # does not appear in the API surface while the linked-account PKCE flow
    # is unimplemented (#775).  Set the env var to "1" or "true" to mount
    # the callback route so V1 (the OpenRouter-as-Hermes-upstream PR) can
    # inherit the loopback guard from day 1.
    if os.environ.get("HAL0_OPENROUTER_OAUTH_ENABLED", "").lower() in ("1", "true"):
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

    # ── MCP servers ──────────────────────────────────────────────
    # Mounted BEFORE _mount_dashboard so the dashboard's SPA fallback
    # doesn't shadow /mcp/* paths. ApprovalQueue + the memory provider are
    # constructed eagerly here (no async setup needed for either) so
    # the mount can wire them in immediately.
    from hal0.mcp import ApprovalQueue

    app.state.approval_queue = ApprovalQueue()

    memory_provider = None
    # Gated by [memory].enabled in hal0.toml (default True) — see
    # 'hal0 memory enable'/'hal0 memory disable'. Every downstream caller
    # (admin MCP routing, /api/memory/* routes, the Hermes memory provider,
    # per-agent memory stats) already degrades to a no-op / 503 when
    # app.state.memory_provider is None, so flipping the flag is the whole
    # toggle. Consumed once here at create_app() — a change needs a
    # hal0-api restart to take effect (memory.enabled is registered
    # service-restart[hal0-api] in _settings_apply.py). create_app() runs
    # before lifespan(), so this is a fresh load rather than the cached
    # app.state.hal0_config lifespan() sets up later.
    try:
        create_app_cfg = load_hal0_config()
    except ConfigParseError as exc:
        log.warning("hal0.config.parse_failed", error=str(exc))
        from hal0.config.schema import Hal0Config

        create_app_cfg = Hal0Config()
    if not create_app_cfg.memory.enabled:
        log.info("hal0.memory.disabled", reason="memory.enabled=false")
    else:
        try:
            from hal0.memory import SelfHealingMemoryProvider, provider_from_config

            memory_provider = provider_from_config(create_app_cfg)
            # #1613: a degraded boot result (hindsight lost the boot race,
            # pgvector fallback) used to be permanent because every consumer
            # below captures this object. Wrap ONLY the degraded case in the
            # self-healing shell; the lifespan polls try_heal() until the
            # durable engine answers. Deliberate pgvector config is not a
            # degradation — no shell, no re-probe loop.
            engine_cfg = str(
                getattr(create_app_cfg.memory, "engine", "hindsight") or "hindsight"
            ).lower()
            if engine_cfg != "pgvector" and getattr(memory_provider, "degraded", False):
                memory_provider = SelfHealingMemoryProvider(memory_provider, create_app_cfg)
                log.warning(
                    "hal0.memory.boot_degraded",
                    detail="hindsight unreachable at boot — pgvector fallback active, "
                    "self-heal re-probe armed",
                )
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("hal0.memory.init_failed", error=str(exc))
    app.state.memory_provider = memory_provider

    # In-process memory dispatcher (Phase 8 closeout).
    # When memory is up, instantiate one MemoryDispatcher and hand it to
    # mount_mcp_servers so the admin MCP server's ``memory_*`` tools hit
    # the memory engine directly instead of looping back through HTTP to
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

    # A mount failure is NOT cosmetic: it removes /mcp/admin and /mcp/memory
    # entirely, which is the whole agent control surface. We still don't let it
    # abort boot (a serving API with no MCP beats no API), but it must be
    # LOUD and OBSERVABLE — the fastapi-0.138 route-walker regression hid here
    # for 21 boots on a live box behind a single warning line, with every
    # health endpoint still reporting "ok". Log at error level and record the
    # reason on app.state so /api/health/system can degrade and name it.
    app.state.mcp_mount_error = None
    try:
        from hal0.api.mcp_mount import mount_mcp_servers

        mount_mcp_servers(
            app,
            approval_queue=app.state.approval_queue,
            memory_provider=memory_provider,
            memory_dispatcher=memory_dispatcher,
        )
    except Exception as exc:  # pragma: no cover — defensive
        app.state.mcp_mount_error = str(exc)
        log.error("hal0.mcp.mount_failed", error=str(exc))

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
        # no-cache so browsers always revalidate index.html and pick up a new
        # build's hashed assets immediately (hashed /assets stay cacheable).
        # Without this the dashboard serves stale after a deploy until a hard
        # reload — the "can't see my change on 105" trap.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
