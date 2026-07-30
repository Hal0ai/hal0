"""Slot lifecycle endpoints (mounted under /api/slots).

Phase 1: real SlotManager-backed lifecycle wired alongside synthetic
upstream-backed entries. Real slots win on name collision; synthetic
entries persist for remote-upstream visibility in the dashboard until
every upstream has a corresponding local slot.

SSE endpoints (note: there is no ``/api/slots/{name}/events`` — the
stream is split by concern):

- ``GET /api/slots/{name}/state/stream`` — state-machine transitions
  for one slot (``starting → warming → ready → serving → idle …``).
- ``GET /api/slots/{name}/logs/stream`` — line-by-line journal tail
  for the slot's systemd unit.

PR-11: list responses are enriched with
config-derived fields (drawer seeds, declared backend) plus live
container state (``container_status`` / ``container_health``), and a
``coresident_group`` ID grouping slots that back the same FLM process
(the NPU trio: ``flm`` + ``flm-stt`` + ``flm-embed``). This is
backward-compatible — every legacy field is preserved; new keys are
purely additive.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from hal0.api._audit import record_action
from hal0.api._redact import redact_log_line
from hal0.api.middleware.error_codes import BadRequest, Hal0Error
from hal0.model_meta import is_resolvable
from hal0.slot_view import (
    SlotViewAggregator,
    config_enrichment,
    container_enrichment,
    loaded_model_names_from_slots,
    serialize_slot,
    synthesize_upstream_entries,
)
from hal0.slots import flm_catalog as _flm_catalog
from hal0.slots import image_pull as _image_pull
from hal0.slots import logs as _logs
from hal0.slots import metrics_collect as _metrics_collect
from hal0.slots import port_alloc as _port_alloc
from hal0.slots import voices as _voices
from hal0.slots.manager import Slot, SlotManager

# Auth was removed by design. All routes are open on the local network.

router = APIRouter()


@router.get("/flm/models")
async def list_flm_models(request: Request):
    """Return the full FLM catalog for the drawer's NPU model dropdowns.

    The dashboard shows the WHOLE catalog (installed or not) so the operator
    can pick any tag and trigger a download for the ones not yet on disk
    (``POST /api/models/{tag}/pull`` handles FLM tags). Each entry carries an
    ``installed`` flag so the UI can mark/act on the difference.

    Two sources, container-exec primary:

      1. ``<runtime> exec`` into the running FLM container and run ``flm list``.
         This reads the store the slot ACTUALLY serves with — on a box that
         relocates the model store (``[models].flm_store``) the store is a
         bind-mount that only exists inside the container, so the container is
         the only place ``installed`` is accurate. Full list + correct flags.
      2. When the container is down (cold/disabled slot), fall back to the HOST
         ``flm list`` probe (:func:`flm_served_models`). It still knows every
         catalog tag (from the bundled ``model_list.json``) so the dropdowns
         populate; ``installed`` may read false on relocated-store boxes since
         the host can't see the container-only mount — acceptable for the cold
         case, and correct on default-store boxes.

    Shape: ``{"models": [{"model": tag, "installed": bool,
    "capabilities": [...], "family": str}]}`` — ``model``/``installed`` keep the
    dashboard filter contract. The container-exec + host-probe fan-out lives in
    :func:`hal0.slots.flm_catalog.list_models`.
    """
    return {"models": _flm_catalog.list_models()}


class NotImplementedYet(Hal0Error):
    code = "system.not_implemented"
    status = 501


# ── helpers ────────────────────────────────────────────────────────────────


def _get_slot_manager(request: Request) -> SlotManager:
    """Pull the SlotManager off app.state (wired in the lifespan).

    Missing manager raises a typed system.internal so the error envelope
    middleware renders consistently — should never happen outside tests
    that bypass the lifespan.
    """
    sm = getattr(request.app.state, "slot_manager", None)
    if sm is None:
        # 5xx: internal invariant — the lifespan should always wire this.
        # Not a client validation failure, so it stays at the default 500.
        raise Hal0Error(
            "slot_manager not initialised on app.state",
            details={"hint": "lifespan did not run"},
        )
    return sm


def _slot_to_dict(slot: Slot, request: Request | None = None) -> dict[str, Any]:
    """Serialise a real Slot snapshot into the API shape.

    Request-bound adapter over :func:`hal0.slot_view.serialize_slot`
    (issue #698) — kept here because every per-slot route (create / get /
    config / backend / lifecycle) and ``routes/health.py`` call it with a
    ``Request`` in hand.

    When ``request`` is provided, also includes a ``models`` list pulled
    from the shared model cache. For an FLM slot serving chat + embed +
    asr concurrently, this surfaces all three tags so the dashboard can
    render the slot as multi-model instead of showing only the chat tag.
    """
    model_cache: dict[str, Any] | None = None
    if request is not None:
        model_cache = getattr(request.app.state, "model_cache", {}) or {}
    out = serialize_slot(slot, model_cache=model_cache)
    # Surface the stable opaque slot id (rework §11.1) additively: present
    # only once the identity store has assigned one, so pre-§11.1 snapshots
    # and exact-shape tests are unaffected. The dashboard keys on ``id`` to
    # treat rename as a pure relabel.
    slot_id = getattr(slot, "slot_id", None)
    if slot_id is not None and "id" not in out:
        out["id"] = slot_id
    return out


async def _config_field_enrichment(request: Request) -> dict[str, dict[str, Any]]:
    """Build per-slot config-derived fields for slot snapshots.

    Request-bound adapter over :func:`hal0.slot_view.config_enrichment`
    (issue #698) — kept here for ``get_slot``'s per-card refresh.
    Never raises — a config read failure returns an empty enrichment so
    the dashboard degrades to the on-disk view rather than 500ing.
    """
    sm = getattr(request.app.state, "slot_manager", None)
    if sm is None:
        return {}
    try:
        configs = await sm.iter_configs()
    except Exception:
        return {}
    return config_enrichment(configs)


async def _container_state_enrichment(request: Request) -> dict[str, dict[str, Any]]:
    """Build per-slot container state for container-backed slots.

    Request-bound adapter over :func:`hal0.slot_view.container_enrichment`
    (issue #698) — kept here for ``get_slot``'s per-card refresh.

    Never raises — probe failures degrade to ``stopped`` / ``False`` rather
    than surfacing as a 500.
    """
    sm = getattr(request.app.state, "slot_manager", None)
    if sm is None:
        return {}
    try:
        configs = await sm.iter_configs()
    except Exception:
        return {}
    return await container_enrichment(
        configs,
        pull_jobs=getattr(request.app.state, "slot_pull_jobs", {}),
    )


async def _loaded_models(request: Request) -> set[str]:
    """Model ids currently served by dispatchable slots.

    The truth source for the synthetic composite slot's ``status``: a
    model is "serving" only when a slot in the dispatchable ready-set
    holds it, not when the catalogue merely lists it. Never raises — a
    slot-list failure yields an empty set so the dashboard degrades to
    "offline" instead of 500ing.

    Adapter over :func:`hal0.slot_view.loaded_model_names_from_slots`
    (issue #698) — kept here for ``routes/health.py``'s composite
    status payload.
    """
    sm = getattr(request.app.state, "slot_manager", None)
    if sm is None:
        return set()
    try:
        slots = await sm.list()
    except Exception:
        return set()
    return loaded_model_names_from_slots(slots)


class _UpstreamsWithHal0Composite:
    """``.list()``-only view of ``upstreams`` plus a stand-in ``hal0`` entry.

    The dashboard's ``hal0`` tile represents the direct-read composite
    model catalogue (``model_cache["hal0"]``, aggregated straight from
    slot config by ``hal0.api._fetch_hal0_composite_models``) — no
    pseudo-upstream is registered in the routing table for it. This thin
    wrapper folds a stand-in descriptor into ``.list()`` purely so
    :func:`hal0.slot_view.synthesize_upstream_entries` (a generic,
    hal0-agnostic function) still surfaces one "local composite" entry,
    unless an operator has defined a real ``hal0`` upstream (already
    present in ``upstreams.list()``).
    """

    def __init__(self, upstreams: Any) -> None:
        self._upstreams = upstreams

    def list(self) -> list[Any]:
        entries = list(self._upstreams.list())
        if not any(u.name == "hal0" for u in entries):
            entries.append(
                SimpleNamespace(name="hal0", kind="slot", url="http://127.0.0.1:8080/v1")
            )
        return entries


def _synthesize_slots_from_upstreams(
    request: Request, loaded_models: set[str] | None = None
) -> list[dict[str, Any]]:
    """Build virtual slot entries from configured upstreams.

    Until every upstream has a corresponding local slot, the dashboard
    still needs to show remote-backed inference targets. Each upstream
    surfaces as a read-only slot entry. ``status`` is computed by kind:

      * local composite (``kind="slot"``) — ``serving`` only when one of
        the upstream's advertised models appears in the live loaded
        set (``loaded_models``). The catalogue cache lists every configured
        chat model, so it is NOT a liveness signal; consulting the loaded
        set is what keeps the dashboard from showing evicted models as
        resident. Falls back to the catalogue heuristic only when health
        was unreadable (``loaded_models is None``).
      * remote (``kind="remote"``) — ``serving`` when its model cache is
        populated, since that cache is a live ``/v1/models`` probe of the
        remote. ``offline`` otherwise.

    The slot's ``model`` reflects the most recently dispatched model id
    for this upstream (tracked in ``app.state.last_used_model``); falls
    back to the first non-alias from the catalog before any inference
    has happened.

    Request-bound adapter over
    :func:`hal0.slot_view.synthesize_upstream_entries` (issue #698) —
    kept here for ``get_slot``'s synthetic fall-through and
    ``routes/health.py``'s composite status payload.
    """
    return synthesize_upstream_entries(
        _UpstreamsWithHal0Composite(request.app.state.upstreams),
        getattr(request.app.state, "model_cache", {}),
        getattr(request.app.state, "last_used_model", {}),
        loaded_models=loaded_models,
    )


# ── /api/status enrichment mirror ────────────────────────────────────────────
# The fast /api/status poll serialises slots via _slot_to_dict only — it must
# NOT run the heavy per-slot container probe (systemctl is-active + /health)
# that list_slots' aggregator does, or it loses its cheap-liveness role. But the
# dashboard unions /api/status with /api/slots, and a status-only entry arrives
# bare (container_status null → a downgraded dot + zeroed metrics), so it
# flickers whenever it wins a poll that the slow /api/slots lost. As a cheap
# bridge, list_slots caches the enrichment it already computed and get_status
# overlays the last-good values (no extra syscalls) onto its bare entries within
# a short TTL. Mirrors the client-side reconcileEnrichment carry-forward.
_STATUS_ENRICH_TTL_S = 30.0


def _cache_slot_enrichment(request: Request, dicts: list[dict[str, Any]]) -> None:
    """Stash list_slots' freshly-probed container state for /api/status reuse."""
    store = getattr(request.app.state, "slot_enrich_cache", None)
    if store is None:
        store = {}
        request.app.state.slot_enrich_cache = store
    now = time.monotonic()
    for d in dicts:
        name = d.get("name")
        if not name or d.get("_synthetic") or d.get("container_status") is None:
            continue
        store[name] = {
            "container_status": d.get("container_status"),
            "container_health": d.get("container_health"),
            "metrics": d.get("metrics"),
            "ts": now,
        }


def overlay_cached_enrichment(
    request: Request, entries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Fill bare /api/status entries with list_slots' last-good enrichment.

    Only touches entries that lack their own ``container_status`` and only when
    the cached probe is within :data:`_STATUS_ENRICH_TTL_S`. Never runs a
    syscall — a pure dict overlay — so /api/status keeps its cheap-poll
    contract while its slot cards stop flickering in the dashboard union.
    """
    store = getattr(request.app.state, "slot_enrich_cache", {}) or {}
    now = time.monotonic()
    for e in entries:
        if e.get("container_status") is not None:
            continue
        cached = store.get(e.get("name"))
        if not cached or now - cached["ts"] > _STATUS_ENRICH_TTL_S:
            continue
        e["container_status"] = cached["container_status"]
        e["container_health"] = cached["container_health"]
        if cached.get("metrics") is not None and not e.get("metrics"):
            e["metrics"] = cached["metrics"]
    return entries


# ── list / create ──────────────────────────────────────────────────────────


@router.get("")
async def list_slots(request: Request) -> list[dict[str, object]]:
    """List configured slots.

    Thin adapter over :meth:`hal0.slot_view.SlotViewAggregator.snapshot`
    (issue #698): the aggregator merges real SlotManager-backed entries
    with synthetic upstream-backed ones (real slots win on name
    collision), enriches each real entry with config-derived fields +
    container probe results, stamps per-slot resident memory, and embeds
    live metrics in the card-expected shape. The route only wires the
    aggregator's dependencies off ``app.state`` and serializes the typed
    :class:`hal0.slot_view.SlotView` rows.
    """
    import functools

    sm = _get_slot_manager(request)
    state = request.app.state
    aggregator = SlotViewAggregator(
        sm,
        registry=getattr(state, "model_registry", None),
        metrics=functools.partial(slot_metrics, request),
        model_cache=getattr(state, "model_cache", {}) or {},
        upstreams=_UpstreamsWithHal0Composite(state.upstreams),
        last_used_model=getattr(state, "last_used_model", {}),
        slot_pull_jobs=getattr(state, "slot_pull_jobs", {}),
    )
    dicts = [view.to_dict() for view in await aggregator.snapshot()]
    _cache_slot_enrichment(request, dicts)
    return dicts


# The slot port allocator (pool resolution, claim collection, next-free,
# conflict rejection) moved to ``hal0.slots.port_alloc`` (P3-routers §J;
# MERGE TARGET rework §11.2 PortAuthority). The route layer keeps underscore
# re-export bindings so ``routes/ports``, ``capabilities/orchestrator`` and the
# test-suite keep resolving ``routes.slots._next_free_slot_port`` &c.
_slot_port_range = _port_alloc.slot_port_range
_collect_port_claims = _port_alloc.collect_port_claims
_next_free_slot_port = _port_alloc.next_free_slot_port
_reject_port_conflict = _port_alloc.reject_port_conflict


def _validation_error_details(exc: ValidationError) -> dict[str, str]:
    """Render a pydantic ValidationError into ``{field_path: message}``.

    Same shape as the identically-named helper in ``config.py`` /
    ``dashboard_layout.py`` / ``settings.py`` — kept local per-module rather
    than shared, matching the house pattern.
    """
    out: dict[str, str] = {}
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        out[loc or "<root>"] = err.get("msg", "invalid")
    return out


# ── typed command bodies (typed-bodies inc-3) ────────────────────────────────
#
# In-handler pydantic validation for the COMMAND bodies where a missing/
# wrong-type key used to silently degrade into a bad downstream call (a
# non-string model_id tunnelling into SlotManager.load/.swap, eventually
# either 500ing inside is_resolvable() or riding out the 180s container
# health timeout) instead of failing fast with a clean 400. Deliberately
# NOT FastAPI body-params: every site here already emits 400 + the hal0
# typed envelope on bad input, and body-params would flip that to FastAPI's
# 422 + a different envelope shape — a second dashboard break layered on
# top of the one this lane exists to avoid. So: parse the body by hand
# exactly as before, run it through ``Model.model_validate``, and translate
# any ``ValidationError`` into the SAME ``BadRequest`` (status + code) the
# site already raises for the equivalent bad-value case today.
#
# The SlotConfig config-write trio — create_slot / update_slot_config /
# update_slot_defaults — is deliberately LEFT LOOSE: those bodies are
# ``extra="allow"`` and already gated by ``_reject_unknown_config_keys``
# (dynamic field-set validation against the SlotConfig/ModelConfig/
# ServerConfig pydantic schemas). Adding a second, narrower pydantic model
# in front of that boundary would duplicate it and risk drifting out of
# sync with the real schema — the existing boundary IS the typed layer for
# those three.


class _RenameSlotBody(BaseModel):
    """``POST /{name}/rename`` body — either key names the new label."""

    new_name: str | None = None
    name: str | None = None


class _SlotLoadBody(BaseModel):
    """``POST /{name}/load`` body — both keys accepted for the same field."""

    model_id: str | None = None
    model: str | None = None


class _SlotSwapBody(BaseModel):
    """``POST /{name}/swap`` body — model_id is required (checked after parse)."""

    model_id: str | None = None


def _reject_unknown_config_keys(payload: dict[str, Any]) -> None:
    """400 when a slot-config write body carries keys the schema doesn't know.

    ``SlotConfig``/``ModelConfig``/``ServerConfig`` are ``extra="allow"`` so a
    typo'd key (``ctx_sizee``, ``enabeld``) used to persist to TOML silently
    and the intended setting never took effect. The boundary now validates
    against :func:`hal0.slot_config.unknown_slot_config_keys` (field sets
    derived dynamically from the pydantic models, tolerating the documented
    legacy aliases like ``[model].ctx_size`` and the string ``image``
    override) and rejects unknown keys with the paths listed.

    Called AFTER :func:`_reject_model_owned_config_keys` at every call site —
    ``mtp``/``enable_thinking``/``vision`` would otherwise just fall out as
    generic "unknown slot config key(s)" here instead of the more actionable
    "belongs on the model" message.
    """
    from hal0.slot_config import unknown_slot_config_keys

    unknown = unknown_slot_config_keys(payload)
    if unknown:
        raise BadRequest(
            "unknown slot config key(s): " + ", ".join(unknown),
            details={
                "unknown_keys": unknown,
                "hint": "check spelling against the SlotConfig schema "
                "(known sub-tables: [model], [server], [npu], [image])",
            },
            code="validation.unknown_keys",
        )


def _reject_model_owned_config_keys(payload: dict[str, Any]) -> None:
    """400 when a slot-config write body carries a MODEL-owned key.

    Thin route-layer wrapper over
    :func:`hal0.slot_config.reject_model_owned_slot_keys` — ``mtp`` /
    ``enable_thinking`` / ``vision`` moved to ``ModelDefaults`` (spec-hw-
    slot-ownership §1); called BEFORE :func:`_reject_unknown_config_keys` at
    every slot-config write boundary so the operator gets the specific
    "belongs on the model" message instead of a generic unknown-key 400.
    """
    from hal0.slot_config import reject_model_owned_slot_keys

    reject_model_owned_slot_keys(payload)


def _device_backend(device: str | None) -> str:
    """Bare backend token for a slot ``device`` enum (mirrors the UI helper).

    gpu-rocm → rocm, gpu-vulkan → vulkan, gpu-cuda → cuda, cpu → cpu, npu → npu.
    """
    d = str(device or "").lower()
    if not d:
        return ""
    return d[4:] if d.startswith("gpu-") else d


def _fit_check_warning(device: str | None, binary: str | None) -> str | None:
    """Non-blocking hardware fit-check (spec-hw-slot-ownership §4).

    WARN (never reject) when the slot's device backend is not in the chosen
    BINARY runner's ``supported_backends``. Mirrors the UI slot-HW-grid warning
    and the "warn at assignment, not at spawn" rule. Returns a message string or
    ``None``. No BINARY (empty) = HW-gated default derived from ``device`` → no
    check; an unknown BINARY key is left to the launch path to report.
    """
    if not binary:
        return None
    from hal0.runners import RUNNER_IMAGES

    runner = RUNNER_IMAGES.get(str(binary))
    if runner is None:
        return None
    supported = tuple(runner.supported_backends)
    backend = _device_backend(device)
    if backend and supported and backend not in supported:
        return (
            f'device backend "{backend}" is not in {binary}\'s supported '
            f"backends ({', '.join(supported)}); the slot may fall back or "
            "fail at spawn"
        )
    return None


def _normalize_create_body(
    body: dict[str, Any],
    *,
    port_start: int | None = None,
    port_end: int | None = None,
    slot_snapshots: list[dict] | None = None,
) -> dict[str, Any]:
    """Normalize a POST /api/slots body to the canonical nested shape.

    Two compat hops (#275 bugs 1 + 2):

    1. Top-level ``model: "name"`` (flat shape) → ``model: {"default":
       "name"}`` (nested [model] table). The serializer reads
       ``cfg.get("model").get("default")`` and the SlotConfig pydantic
       model has a nested ModelConfig — but callers may POST a
       top-level string. The result was ``model_default`` MISSING from
       /api/slots responses for any slot created via POST.
    2. Missing or zero ``port`` → auto-assign via
       :func:`_next_free_slot_port` over the configured
       ``[slots].port_range_start/end`` pool. Without this, new slots
       persist ``port=0`` and the dashboard card shows ``port=0``
       instead of a useable port.
    """
    out = dict(body)
    model_val = out.get("model")
    if isinstance(model_val, str):
        out["model"] = {"default": model_val}
    if "port" not in out or not isinstance(out.get("port"), int) or out.get("port") in (0, None):
        out["port"] = _next_free_slot_port(port_start, port_end, slot_snapshots)
    return out


@router.post("", status_code=201)
async def create_slot(request: Request) -> dict[str, object]:
    """Create a new slot. Body: SlotConfig schema.

    Writes /etc/hal0/slots/<name>.toml, the systemd drop-in override, the
    env file, and the initial state.json. Does NOT start the slot — the
    caller follows with POST /api/slots/<name>/load when ready.

    Accepts both the flat body shape (top-level ``model: "name"``,
    ``device: "gpu-vulkan"``, no ``port``) and the legacy nested shape
    (``[model] default = "name"``, ``[server] port = 8081``). The body
    is normalized to the nested shape via :func:`_normalize_create_body`
    before persistence so the serializer + persistent TOML loaders see
    one canonical shape.
    """
    sm = _get_slot_manager(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest(
            "request body must be valid JSON",
            details={"error": str(exc)},
            code="request.invalid_json",
        ) from exc
    if not isinstance(body, dict):
        raise BadRequest("request body must be a JSON object", code="request.not_an_object")

    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise BadRequest(
            "slot 'name' is required (non-empty string)",
            code="slot.name_required",
        )

    # "Set as default" from the create modal: a top-level ``default: true`` here
    # means "promote this slot's MODEL as its type's default" (the model-layer
    # marker), NOT the SC-4 slot-level default flag. Pop it out of the slot body
    # before persistence so it neither lands in the slot TOML nor triggers the
    # slot-level uniqueness guard — the promotion happens post-create through
    # the models_service chokepoint (see below). Absent/false = no change.
    make_default = bool(body.pop("default", False))

    # [slots] policy is read from the live config on every create, so a PUT
    # /api/settings change applies to the next creation without a restart.
    slots_cfg = getattr(getattr(request.app.state, "hal0_config", None), "slots", None)
    max_slots = int(getattr(slots_cfg, "max_slots", 0) or 0)
    # Runtime rows feed the port registry: a slot's LIVE port can differ
    # from any TOML (FLM-trio virtual ports), so auto-assign and conflict
    # checks must see them or they hand out an occupied port.
    existing = await sm.list()
    runtime_ports = [
        {
            "name": getattr(s, "name", None),
            "port": getattr(s, "port", None),
            "coresident_group": getattr(s, "coresident_group", None),
        }
        for s in existing
    ]
    if max_slots and len(existing) >= max_slots:
        raise BadRequest(
            f"slot budget reached: [slots].max_slots={max_slots} and "
            f"{len(existing)} slots already exist (seeded slots count "
            "toward the budget — raise max_slots or delete a slot)",
            code="slot.capacity_exhausted",
            details={"max_slots": max_slots, "existing_slots": len(existing)},
        )

    body = _normalize_create_body(
        body,
        port_start=(int(slots_cfg.port_range_start) if slots_cfg else None),
        port_end=(int(slots_cfg.port_range_end) if slots_cfg else None),
        slot_snapshots=runtime_ports,
    )
    if isinstance(body.get("port"), int):
        _reject_port_conflict(int(body["port"]), name, runtime_ports)
    _reject_model_owned_config_keys(body)
    _reject_unknown_config_keys(body)
    async with record_action(
        request,
        category="slot",
        action="slot.create",
        target=name,
    ) as _rec:
        snap = await sm.create(name, body)
        _rec.after = {"config": body}

    out = _slot_to_dict(snap, request)

    # Fit-check (spec-hw-slot-ownership §4): warn — never reject — when the
    # created slot's (device, BINARY) pair is incompatible. Attached to the
    # response like ``default_promotion`` below so the UI can surface a soft
    # notice; mirrors the client-side slot-HW-grid warning.
    fit_warn = _fit_check_warning(body.get("device"), body.get("binary"))
    if fit_warn:
        out["fit_warning"] = fit_warn

    # Post-create model-default promotion. The slot is the primary object and is
    # already persisted — a promotion failure (unresolved / unregistered model,
    # e.g. a "will pull" pick or an FLM tag) must NOT 500 the create. Route
    # through the SAME single chokepoint the dedicated endpoint uses so the
    # demote logic is never duplicated. Report the outcome on the response so
    # the UI can surface a soft warning if it didn't take.
    if make_default:
        from hal0.services import models_service as _models_svc

        model_sect = body.get("model")
        model_id = model_sect.get("default") if isinstance(model_sect, dict) else None
        registry = getattr(request.app.state, "model_registry", None)
        if not model_id or registry is None:
            out["default_promotion"] = {
                "promoted": False,
                "reason": "no model bound to the slot to promote",
            }
        else:
            try:
                result = _models_svc.set_model_type_default(registry, model_id, default=True)
                out["default_promotion"] = {"promoted": True, **result}
            except Exception as exc:  # ModelNotFound / registry hiccup — fail soft
                out["default_promotion"] = {
                    "promoted": False,
                    "model_id": model_id,
                    "reason": str(exc),
                }

    return out


# ── id-keyed lookups + rename (rework §11.1) ─────────────────────────────────
#
# These literal-prefix routes (``by-name`` / ``by-id``) are registered before
# the ``/{name}`` catch-alls below; FastAPI matches them first because their
# first path segment is a literal, not a wildcard.


@router.get("/by-name/{name}")
async def get_slot_by_name(name: str, request: Request) -> dict[str, object]:
    """Canonical name-keyed lookup (rework §11.1).

    The documented go-forward name path — identical payload to the existing
    ``GET /api/slots/{name}`` (kept for one release). Resolves through the
    SlotManager so aliases + drift reconcile apply.
    """
    sm = _get_slot_manager(request)
    snap = await sm.status(name, include_config_drift=True)
    return _slot_to_dict(snap, request)


@router.get("/by-id/{slot_id}")
async def get_slot_by_id(slot_id: int, request: Request) -> dict[str, object]:
    """Stable-id lookup (rework §11.1): opaque id → current name → snapshot."""
    sm = _get_slot_manager(request)
    name = sm.slot_id_to_name(slot_id)
    snap = await sm.status(name, include_config_drift=True)
    return _slot_to_dict(snap, request)


@router.post("/{name}/rename")
async def rename_slot(name: str, request: Request) -> dict[str, object]:
    """Rename a slot's display label. Body: ``{"new_name": "..."}``.

    The identity ``id`` is stable, so a rename never touches the slot's
    port or state semantics. The slot must be OFFLINE (the systemd unit is
    still name-keyed until the id-migration lands). A name collision surfaces
    as the typed ``slot.config_error`` envelope.
    """
    sm = _get_slot_manager(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest(
            "request body must be valid JSON",
            details={"error": str(exc)},
            code="request.invalid_json",
        ) from exc
    if not isinstance(body, dict):
        raise BadRequest("request body must be a JSON object", code="request.not_an_object")
    try:
        parsed_body = _RenameSlotBody.model_validate(body)
    except ValidationError as exc:
        # A wrong-type new_name/name (e.g. an int) fails isinstance the same
        # way an empty one does today — same code, same message.
        raise BadRequest(
            "rename requires a non-empty 'new_name' in the request body",
            code="slot.name_required",
            details={"slot": name, **_validation_error_details(exc)},
        ) from exc
    new_name = parsed_body.new_name or parsed_body.name
    if not new_name or not new_name.strip():
        raise BadRequest(
            "rename requires a non-empty 'new_name' in the request body",
            code="slot.name_required",
            details={"slot": name},
        )
    async with record_action(
        request,
        category="slot",
        action="slot.rename",
        target=name,
        message=f"rename → {new_name}",
    ) as _rec:
        snap = await sm.rename(name, new_name)
        _rec.after = {"new_name": new_name}
    return _slot_to_dict(snap, request)


# ── metrics / capacity ─────────────────────────────────────────────────────


# The per-slot metric collectors moved to ``hal0.slots.metrics_collect``
# (P3-routers §J): the systemctl/cgroup/llama-server IO adapters, the fan-out,
# and the rolling tok/s + TTFT views. The route layer keeps thin re-export
# bindings + request adapters so ``slot_metrics`` reads as a service call,
# external callers keep resolving ``routes.slots._scrape_llama_metrics``
# (``hal0.metrics.sampler``), and the monkeypatch/import seams the test-suite
# uses still resolve. New callers import from ``hal0.slots.metrics_collect``.
_tps_from_events = _metrics_collect.tps_from_events
_systemd_show = _metrics_collect.systemd_props
_scrape_llama_metrics = _metrics_collect.llama_metrics
_docker_container_mem_bytes = _metrics_collect.container_mem_bytes


def _per_slot_local_tps(request: Request, window_s: float = 5.0) -> dict[str, float]:
    """Per-slot/upstream tok/s on this process's streaming path (see
    ``metrics_collect.local_tps``)."""
    return _metrics_collect.local_tps(request.app.state, window_s)


def _per_slot_ttft(request: Request) -> dict[str, dict[str, float]]:
    """Per-slot TTFT view — latest + windowed mean (see
    ``metrics_collect.local_ttft``)."""
    return _metrics_collect.local_ttft(request.app.state)


async def _local_slot_metrics(request: Request) -> dict[str, dict[str, Any]]:
    """Per-slot mem/uptime/request-count fan-out (see
    ``metrics_collect.collect_local``)."""
    return await _metrics_collect.collect_local(getattr(request.app.state, "slot_manager", None))


@router.get("/metrics")
async def slot_metrics(request: Request) -> dict[str, Any]:
    """Per-slot runtime metrics keyed by slot name.

    Drives the dashboard's per-slot tok/s row + sparkline. Three layers:

    1. Remote upstreams' /api/slots/metrics (for haloai-style fanouts).
    2. Local per-slot tok/s measured on the dispatcher's streaming path.
    3. Local per-slot MEM/UP scraped from systemd + /proc for the
       hal0-slot@<name>.service template instance.

    Layer 2 wins over layer 1 on tok/s when locally higher (the local
    rolling window reflects the request that's happening *right now*);
    layer 3 fills MEM/UP for any slot that didn't get values from
    layer 1, which is the single-host LXC case where there are no
    upstreams to proxy.
    """
    from hal0.api.routes.hardware import stats_slots

    merged = await stats_slots(request)
    for name, tps in _per_slot_local_tps(request).items():
        if tps <= 0:
            continue
        entry = merged.get(name)
        if not isinstance(entry, dict):
            entry = {"name": name}
            merged[name] = entry
        existing = entry.get("tokens_per_sec") or entry.get("tps") or 0
        if tps > existing:
            entry["tokens_per_sec"] = tps
    for name, local in (await _local_slot_metrics(request)).items():
        entry = merged.get(name)
        if not isinstance(entry, dict):
            entry = dict(local)
            merged[name] = entry
            continue
        # Only fill fields the upstream didn't already report. Truthy-only
        # to avoid overwriting a real 0 with another real 0; for these
        # three fields a 0 from systemd means "no data", so this is safe.
        for key in ("mem_rss_mb", "uptime_seconds", "requests_processing"):
            if not entry.get(key):
                entry[key] = local.get(key, 0)
        # KV-cache is a gauge — present only on llama-backed slots,
        # which the remote upstream may not know about. Always prefer
        # the local scrape when we have one.
        if "kv_cache_usage" in local:
            entry["kv_cache_usage"] = local["kv_cache_usage"]
        if local.get("ctx"):
            entry["ctx"] = local["ctx"]
    # TTFT samples are captured on the dispatcher's streaming wrapper
    # and only exist locally — fold them in last so they win.
    for name, ttft in _per_slot_ttft(request).items():
        entry = merged.get(name)
        if not isinstance(entry, dict):
            entry = {"name": name}
            merged[name] = entry
        entry.update(ttft)
    # FLM / NPU per-slot throughput + KV column occupancy — captured
    # from the ``usage`` block of non-streaming chat completions.
    flm_tps = getattr(request.app.state, "slot_throughput", {})
    flm_kv = getattr(request.app.state, "slot_kv_occupancy", {})
    for name, tps in flm_tps.items():
        entry = merged.get(name)
        if not isinstance(entry, dict):
            entry = {"name": name}
            merged[name] = entry
        entry["tokens_per_sec"] = tps
    for name, kv in flm_kv.items():
        entry = merged.get(name)
        if not isinstance(entry, dict):
            entry = {"name": name}
            merged[name] = entry
        entry["kv_cache_usage"] = kv
    # Per-slot request counts (shadow slots inherit from parent)
    req_store = getattr(request.app.state, "slot_request_count", {})
    for name, count in req_store.items():
        entry = merged.get(name)
        if not isinstance(entry, dict):
            entry = {"name": name}
            merged[name] = entry
        entry["request_count"] = count
    # Per-slot last-use timestamps (drives most-recent animation)
    last_used = getattr(request.app.state, "slot_last_used", {})
    for name, ts in last_used.items():
        entry = merged.get(name)
        if not isinstance(entry, dict):
            entry = {"name": name}
            merged[name] = entry
        entry["last_used_ts"] = ts
    return merged


@router.get("/capacity")
async def slot_capacity(request: Request) -> dict[str, object]:
    """Per-slot resident memory + slot-count budget for the dashboard.

    Returns ``{"per_slot": {slot_name: {vram_mb, ram_mb, mem_mb, state,
    model_id}}, "slot_budget": {"used_slots", "max_slots"}}``. ``per_slot``
    mirrors the block also stamped onto ``GET /api/stats/hardware``;
    ``slot_budget`` reflects the ``[slots].max_slots`` creation gate
    (max_slots 0 = unlimited).
    """
    from hal0.slots.capacity import build_per_slot

    sm = _get_slot_manager(request)
    slots = await sm.list()
    registry = getattr(request.app.state, "model_registry", None)
    slots_cfg = getattr(getattr(request.app.state, "hal0_config", None), "slots", None)
    return {
        "per_slot": await build_per_slot(slots, registry=registry),
        "slot_budget": {
            "used_slots": len(slots),
            "max_slots": int(getattr(slots_cfg, "max_slots", 0) or 0),
        },
    }


# ── per-slot ───────────────────────────────────────────────────────────────


@router.get("/{name}")
async def get_slot(name: str, request: Request) -> dict[str, object]:
    """Return a snapshot of a single slot.

    Real slots come from the SlotManager; if the name isn't a configured
    local slot, fall through to the synthetic upstream-backed entry.
    SlotNotFound surfaces as the typed slot.not_found envelope.

    PR-11: real-slot snapshots are enriched with config-derived fields
    so the dashboard's per-card refresh stays consistent with the list
    endpoint.
    """
    sm = _get_slot_manager(request)
    try:
        snap = await sm.status(name, include_config_drift=True)
        out = _slot_to_dict(snap, request)
        enrichment = await _config_field_enrichment(request)
        extra = enrichment.get(name)
        if extra:
            for k, v in extra.items():
                out.setdefault(k, v)
        c_enrichment = await _container_state_enrichment(request)
        c_extra = c_enrichment.get(name)
        if c_extra:
            for k, v in c_extra.items():
                out.setdefault(k, v)
        return out
    except Exception:
        # Fall through to synthetic lookup before re-raising — a remote
        # upstream named ``haloai`` should be observable via this endpoint
        # even though it isn't a real slot.
        for entry in _synthesize_slots_from_upstreams(request):
            if entry["name"] == name:
                return entry
        raise


def _state_value(snap: Any) -> str | None:
    """Extract a serialisable state string from a slot snapshot for audit."""
    state = getattr(snap, "state", None)
    return getattr(state, "value", None) or (str(state) if state is not None else None)


async def _safe_config(sm: Any, name: str) -> dict[str, Any] | None:
    """Best-effort current config snapshot for an audit before-image."""
    try:
        cfg = await sm.get_config(name)
        return cfg if isinstance(cfg, dict) else None
    except Exception:
        return None


@router.delete("/{name}")
async def delete_slot(name: str, request: Request, force: bool = False) -> dict[str, object]:
    """Delete a slot. If the slot is running, it is stopped first.

    Built-in (seeded) slots — primary/embed/stt/tts + the NPU trio — are
    protected: the SlotManager raises a typed error the envelope middleware
    surfaces as 4xx. Pass ``?force=true`` to delete a seeded slot anyway (an
    install/update reconcile may re-seed it later).
    """
    sm = _get_slot_manager(request)
    async with record_action(request, category="slot", action="slot.delete", target=name):
        await sm.delete(name, force=force)
    return {"name": name, "deleted": True, "forced": force}


@router.get("/{name}/config")
async def get_slot_config(name: str, request: Request) -> dict[str, object]:
    """Return the slot's TOML config as a dict."""
    sm = _get_slot_manager(request)
    cfg = await sm.get_config(name)
    return cfg


@router.get("/{name}/voices")
async def get_slot_voices(name: str, request: Request) -> dict[str, object]:
    """Proxy the slot's ``GET /v1/audio/voices`` (TTS engines expose it).

    Kokoro and qwen3tts containers serve the list of loaded voices; the
    dashboard's Voice settings populate the default-voice picker from it
    instead of hardcoding the pack. Fail-soft: a cold/unreachable slot (or
    an engine without the route) returns ``{"voices": [], "source":
    "offline"}`` rather than an error — the UI falls back to a seed list.
    Unknown slot names still 404 via ``SlotManager.get_config``. The httpx
    proxy + fail-soft shaping live in
    :func:`hal0.slots.voices.fetch_for_slot`.
    """
    sm = _get_slot_manager(request)
    cfg = await sm.get_config(name)
    return await _voices.fetch_for_slot(name, cfg.get("port"))


@router.get("/{name}/resolved")
async def get_slot_resolved(name: str, request: Request) -> dict[str, object]:
    """Return the resolved llama-server argv with per-flag provenance.

    The auditable single-source-of-truth view: the deduped command plus, for
    each surviving flag, which segment (``base`` / ``profile`` / ``extra_args``)
    set its final value and how many duplicate flags were collapsed. Non-llama
    slots (no profile) get ``{"argv": None, ...}`` rather than an error.
    """
    from hal0.providers.container import resolved_argv_detail_for_slot

    sm = _get_slot_manager(request)
    cfg = await sm.get_config(name)
    detail = resolved_argv_detail_for_slot(cfg)
    if detail is None:
        return {"name": name, "argv": None, "provenance": [], "removed": 0}
    return {"name": name, **detail}


@router.put("/{name}/config")
async def update_slot_config(name: str, request: Request) -> dict[str, object]:
    """Update a slot's config. Body: partial SlotConfig (shallow merge)."""
    sm = _get_slot_manager(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest(
            "request body must be valid JSON",
            details={"error": str(exc)},
            code="request.invalid_json",
        ) from exc
    if not isinstance(body, dict):
        raise BadRequest("request body must be a JSON object", code="request.not_an_object")
    _reject_model_owned_config_keys(body)
    _reject_unknown_config_keys(body)
    # Port moves go through the registry so an edit can't land on a port
    # another slot (config or runtime) or listener already owns.
    new_port = body.get("port")
    if isinstance(new_port, int) and new_port > 0:
        runtime_ports = [
            {
                "name": getattr(s, "name", None),
                "port": getattr(s, "port", None),
                "coresident_group": getattr(s, "coresident_group", None),
            }
            for s in await sm.list()
        ]
        _reject_port_conflict(new_port, name, runtime_ports)
    before = await _safe_config(sm, name)
    async with record_action(
        request, category="slot", action="slot.edit_config", target=name, before=before
    ) as _rec:
        snap = await sm.update_config(name, body)
        _rec.after = body
    # Spec 1 / Component 2: an explicit ``enabled: false`` write must take a
    # running slot actually offline so the faded card matches reality. The
    # config write alone only flips the on-disk flag; without this a disabled
    # slot would keep its llama-server child resident until the next restart.
    # ``unload`` is idempotent (short-circuits when already OFFLINE), but we
    # gate on a live state so an offline/error slot incurs no /v1/unload call.
    if body.get("enabled") is False:
        from hal0.slots.state import SlotState

        _LIVE = {
            SlotState.STARTING,
            SlotState.WARMING,
            SlotState.READY,
            SlotState.SERVING,
            SlotState.IDLE,
        }
        if snap.state in _LIVE:
            snap = await sm.unload(name)
    out = _slot_to_dict(snap, request)
    # Fit-check (spec-hw-slot-ownership §4): warn — never reject — when the
    # slot's post-save (device, BINARY) pair is incompatible. Compute over the
    # merged pair (this write's values win over the pre-write config) so a HW
    # edit that touches only one of the two still checks against the other.
    _before = before or {}
    merged_device = body.get("device", _before.get("device"))
    merged_binary = body.get("binary", _before.get("binary"))
    fit_warn = _fit_check_warning(merged_device, merged_binary)
    if fit_warn:
        out["fit_warning"] = fit_warn
    return out


@router.patch("/{name}/defaults")
async def update_slot_defaults(name: str, request: Request) -> dict[str, object]:
    """Update slot defaults (ctx_size / context_size, n_gpu_layers, …).

    Convenience wrapper over update_config — body keys merge into the
    slot's [model] sub-table rather than the top level. Keys are validated
    against the ModelConfig schema (plus the documented ``ctx_size``
    alias); unknown keys 400 with ``validation.unknown_keys``. Provider-
    specific params belong under ``extra`` (passed through verbatim).
    """
    sm = _get_slot_manager(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest(
            "request body must be valid JSON",
            details={"error": str(exc)},
            code="request.invalid_json",
        ) from exc
    if not isinstance(body, dict):
        raise BadRequest("request body must be a JSON object", code="request.not_an_object")
    # Defaults merge into [model] — validate the wrapped shape so unknown
    # keys surface as their real destination path (``model.<key>``).
    _reject_unknown_config_keys({"model": body})
    before = await _safe_config(sm, name)
    async with record_action(
        request, category="slot", action="slot.edit_defaults", target=name, before=before
    ) as _rec:
        snap = await sm.update_config(name, {"model": body})
        _rec.after = {"model": body}
    return _slot_to_dict(snap, request)


# ── lifecycle ──────────────────────────────────────────────────────────────


@router.post("/{name}/load")
async def load_slot(name: str, request: Request) -> dict[str, object]:
    """Load a model into a slot. Optional body: {"model_id": "..."}.

    Validates ``model_id`` against the registry up-front when supplied
    — a bad id otherwise tunnels into ``SlotManager.load``, which
    happily spawns a container that never goes healthy, leaving the
    operator to wait out the 180s health timeout. Empty / None model_id
    is fine: that path falls back to the slot's TOML default.
    """
    sm = _get_slot_manager(request)
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        # POST without a body is fine — fall back to the slot's default model.
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        parsed_body = _SlotLoadBody.model_validate(body)
    except ValidationError as exc:
        # A non-string model_id (e.g. an int, or a falsy-but-wrong-typed 0)
        # used to slip past the old truthy `if model_id:` guard below and
        # tunnel into SlotManager.load, which either 500s inside
        # is_resolvable() (AttributeError: int has no .endswith) or spawns a
        # container that never goes healthy — the operator eats the 180s
        # timeout the docstring warns about. This is a NEW guard (no prior
        # site error to reuse — a missing model_id was always legal here),
        # so it gets its own code rather than reusing another route's.
        raise BadRequest(
            "model_id must be a string",
            code="slot.invalid_model_id",
            details={"slot": name, **_validation_error_details(exc)},
        ) from exc
    # Some callers post {"model": "..."} for symmetry with the slot
    # config schema. Accept both so a dashboard typo doesn't 422.
    model_id = parsed_body.model_id or parsed_body.model
    if model_id:
        registry = getattr(request.app.state, "model_registry", None)
        if registry is not None and not is_resolvable(model_id, registry):
            from hal0.registry.store import ModelNotFound

            raise ModelNotFound(
                f"model {model_id!r} is not resolvable — not in the registry and "
                f"not an installed FLM model (slot {name!r} not touched)",
                details={"model_id": model_id, "slot": name},
            )
    async with record_action(
        request,
        category="slot",
        action="slot.load",
        target=name,
        message=f"load {model_id or 'default'}",
    ) as _rec:
        # Manual load is operator intent — clear the crash-loop breaker
        # (issue i4) so an ERROR/parked slot always gets a real attempt.
        sm.reset_load_failures(name)
        snap = await sm.load(name, model_id=model_id)
        _rec.after = {"model_id": model_id, "state": _state_value(snap)}
    return _slot_to_dict(snap, request)


@router.post("/{name}/unload")
async def unload_slot(name: str, request: Request, force: bool = False) -> dict[str, object]:
    """Unload a slot. A pinned slot (§21.10) requires ``?force=true``.

    Pass ``?force=true`` to unload a pinned anchor (``agent``/``utility``/
    ``npu`` by default, or any slot with ``SlotConfig.pinned = true``)
    anyway — mirrors the existing seeded-slot ``force`` guard on
    ``DELETE /{name}``. Automatic idle/pressure eviction is unaffected by
    this guard (it already excludes pinned slots at candidate-selection).
    """
    sm = _get_slot_manager(request)
    if not force and await sm.is_pinned(name):
        from hal0.slots.state import SlotPinned

        raise SlotPinned(
            f"slot {name!r} is pinned — pass ?force=true to unload it anyway",
            details={"slot": name, "pinned": True},
        )
    async with record_action(request, category="slot", action="slot.unload", target=name) as _rec:
        snap = await sm.unload(name)
        _rec.after = {"state": _state_value(snap)}
    return _slot_to_dict(snap, request)


@router.post("/{name}/restart")
async def restart_slot(name: str, request: Request) -> dict[str, object]:
    sm = _get_slot_manager(request)
    async with record_action(request, category="slot", action="slot.restart", target=name) as _rec:
        snap = await sm.restart(name)
        _rec.after = {"state": _state_value(snap)}
    return _slot_to_dict(snap, request)


@router.post("/{name}/swap")
async def swap_slot(name: str, request: Request) -> dict[str, object]:
    """Hot-swap a slot's model. Body: {"model_id": "..."}.

    The swap path is destructive — it unloads the live slot before
    attempting to load the new model — so we validate ``model_id``
    against the registry up-front. A bad id without this check used to
    leave the slot in ERROR after a 180s health timeout (the container
    started but had no resolvable model file); now it 404s in <10ms
    with the slot untouched.
    """
    sm = _get_slot_manager(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        parsed_body = _SlotSwapBody.model_validate(body)
    except ValidationError as exc:
        # A truthy non-string model_id (e.g. an int) used to sail past this
        # guard (only an empty/falsy value was rejected) and crash inside
        # is_resolvable()'s .endswith() call — reuse the SAME missing-model
        # error the empty-body case already raises below.
        raise BadRequest(
            "swap requires a non-empty model_id in the request body",
            details={"slot": name, **_validation_error_details(exc)},
            code="swap.missing_model",
        ) from exc
    model_id = parsed_body.model_id
    if not model_id:
        raise BadRequest(
            "swap requires a non-empty model_id in the request body",
            details={"slot": name},
            code="swap.missing_model",
        )
    registry = getattr(request.app.state, "model_registry", None)
    if registry is not None and not is_resolvable(model_id, registry):
        from hal0.registry.store import ModelNotFound

        raise ModelNotFound(
            f"model {model_id!r} is not resolvable — not in the registry and "
            f"not an installed FLM model (slot {name!r} not touched)",
            details={"model_id": model_id, "slot": name},
        )
    async with record_action(
        request,
        category="slot",
        action="slot.swap",
        target=name,
        message=f"swap → {model_id}",
    ) as _rec:
        snap = await sm.swap(name, model_id)
        _rec.after = {"model_id": model_id, "state": _state_value(snap)}
    return _slot_to_dict(snap, request)


# ── logs ───────────────────────────────────────────────────────────────────
#
# journalctl-backed per-slot log access. Slot containers run under the
# ``hal0-slot@<name>.service`` template unit (podman ``--log-driver=none``
# so conmon→journal is the single sink), so the container's llama-server /
# ComfyUI stdout — including the one-shot model-loading lines — lands in
# journald and is reachable here. The journalctl subprocess (one-shot tail +
# follow generator) and heartbeat-noise filter moved to ``hal0.slots.logs``
# (P3-routers §J); the SSE wrapper below stays in the route because it holds
# the ``StreamingResponse``.


@router.get("/{name}/logs")
async def slot_logs(
    name: str, request: Request, lines: int = 200, quiet: bool = True
) -> dict[str, object]:
    """Return the last ``lines`` of this slot's journal output.

    ``quiet`` (default on) drops idle heartbeat spam so the window holds
    signal (model-load lines, errors) rather than ``all slots are idle``
    repeats. Best-effort: on hosts without systemd or where the slot has
    never started, returns an empty string with a hint. The UI tolerates
    that (renders "No logs available") rather than treating it as an error.
    The journalctl tail lives in :func:`hal0.slots.logs.read_tail`.
    """
    sm = _get_slot_manager(request)
    # Validate slot exists so unknown names get the typed slot.not_found
    # envelope instead of an empty 200.
    await sm.status(name)

    text, hint = await _logs.read_tail(f"hal0-slot@{name}.service", lines, quiet)
    if hint is not None:
        return {"name": name, "logs": text, "hint": hint}
    return {"name": name, "logs": text}


@router.get("/{name}/logs/stream")
async def slot_logs_stream(
    name: str, request: Request, backfill: int = 400, quiet: bool = True
) -> StreamingResponse:
    """SSE stream that tails this slot's journal output line-by-line.

    ``backfill`` (default 400) replays recent history before the live
    tail — CRITICAL because the model-loading lines are emitted once at
    container start, so a stream opened with ``-n 0`` (future-only) would
    never show them. ``quiet`` (default on) drops idle heartbeat spam so
    the backfill window holds signal.

    Best-effort: gracefully exits when journalctl is missing or the slot
    has no journal entries yet. Client disconnects close the subprocess.
    The journalctl follow generator lives in
    :func:`hal0.slots.logs.tail_journal`.

    Each line is redacted via :func:`hal0.api._redact.redact_log_line`
    before it's framed — same shared helper as ``/api/logs`` and
    ``/api/logs/stream`` (api-logs-redact). This route has its own
    independent journalctl plumbing in :mod:`hal0.slots.logs`
    (``tail_journal``), which does not redact on its own, so the
    redaction happens here at the SSE framing boundary instead.
    """
    import shutil

    sm = _get_slot_manager(request)
    await sm.status(name)  # 404 fast if unknown

    async def event_source() -> Any:
        if shutil.which("journalctl") is None:
            # B13: use a custom 'degraded' event name, NOT the reserved SSE
            # 'error' name — EventSource's onmessage/onerror never fire for a
            # named 'error' frame, so the log drawer used to spin forever on
            # "waiting for log lines…". The client listens for 'degraded'.
            yield 'event: degraded\ndata: {"message":"journalctl unavailable"}\n\n'
            return
        async for line in _logs.tail_journal(f"hal0-slot@{name}.service", backfill, quiet):
            yield f"data: {json.dumps(redact_log_line(line))}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── state ──────────────────────────────────────────────────────────────────


@router.get("/{name}/state")
async def slot_state(name: str, request: Request) -> dict[str, object]:
    """Return just the state-machine fields for this slot.

    Lighter than /api/slots/{name} — used by clients that poll for a
    transition without needing the full metadata payload.
    """
    sm = _get_slot_manager(request)
    snap = await sm.status(name)
    return {
        "name": snap.name,
        "state": snap.state.value,
        "port": snap.port,
        "model_id": snap.model_id,
        "backend": snap.backend,
    }


@router.get("/{name}/state/stream")
async def slot_state_stream(name: str, request: Request) -> StreamingResponse:
    """SSE stream of state transitions for ``name`` (and only ``name``).

    The SlotManager's state_stream() is fanned out across all slots; this
    endpoint filters to a single slot to keep the wire chatty only where
    the UI is looking. Initial event carries the current snapshot so a
    client that subscribes after a transition still sees the latest
    state without a separate GET.

    SSE event shape::

        event: state
        data: {"name": "...", "state": "ready", "port": 8081, ...}
    """
    sm = _get_slot_manager(request)
    # Confirm the slot exists before opening the long-lived stream — keeps
    # the 404 surface fast and synchronous.
    snap = await sm.status(name)
    initial = {
        "name": snap.name,
        "state": snap.state.value,
        "port": snap.port,
        "model_id": snap.model_id,
        "backend": snap.backend,
    }

    async def event_source() -> Any:
        # Initial snapshot so late subscribers don't wait for the next
        # transition just to learn the current state.
        yield f"event: state\ndata: {json.dumps(initial)}\n\n"
        try:
            async for rec in sm.state_stream():
                if rec.name != name:
                    continue
                payload = {
                    "name": rec.name,
                    "state": rec.state.value,
                    "port": rec.port,
                    "model_id": rec.model_id,
                    "message": rec.message,
                    "updated_at": rec.updated_at,
                }
                yield f"event: state\ndata: {json.dumps(payload)}\n\n"
        except asyncio.CancelledError:
            # Client disconnected — let the generator wind down cleanly so
            # the SlotManager removes the subscriber queue.
            raise

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── container image pull ───────────────────────────────────────────────────────
#
# The job object, background pull runner, profile→image resolver and
# present|missing inspection moved to ``hal0.slots.image_pull`` (P3-routers §J).
# The re-export bindings preserve the test-suite's ``routes.slots._ImagePullJob``
# import + ``routes.slots._run_image_pull`` patch seam.
_ImagePullJob = _image_pull.ImagePullJob
_run_image_pull = _image_pull.run_image_pull


@router.post("/{name}/pull", status_code=202)
async def pull_slot_image(
    name: str, request: Request, background: BackgroundTasks
) -> dict[str, object]:
    """Start a background container image pull for slot ``name``.

    Idempotent: if a pull is already in-flight for this slot, returns
    the existing job's snapshot rather than starting a second pull.

    Returns a job snapshot::

        {"slot_name": "...", "image": "...", "state": "pulling",
         "layer": 0, "total_layers": 0}

    Clients should open ``GET /api/slots/{name}/pull/stream`` to receive
    live layer-progress events after POSTing here.
    """
    sm = _get_slot_manager(request)
    # Validate slot exists.
    await sm.status(name)

    slot_pull_jobs: dict[str, Any] = getattr(request.app.state, "slot_pull_jobs", {})

    existing = slot_pull_jobs.get(name)
    if existing is not None and existing.state == "pulling":
        return {"resumed": True, **existing.as_dict()}

    # Resolve image from profile.
    image = await _image_pull.resolve_slot_image(sm, name)
    if not image:
        raise BadRequest(
            f"slot {name!r} has no container profile / image — cannot pull",
            details={"slot": name},
        )

    job = _ImagePullJob(name, image)
    if not hasattr(request.app.state, "slot_pull_jobs"):
        request.app.state.slot_pull_jobs = {}
    request.app.state.slot_pull_jobs[name] = job
    background.add_task(_run_image_pull, job, request)
    return {"resumed": False, **job.as_dict()}


@router.get("/{name}/pull/stream")
async def pull_slot_image_stream(name: str, request: Request) -> StreamingResponse:
    """SSE stream of container image-pull layer progress for slot ``name``.

    Emits one frame immediately (snapshot or terminal-already state),
    then one per layer line, and a final terminal frame on completion or
    failure. Graceful when no pull is active: emits a ``present`` or
    ``missing`` frame and closes.

    Frame shape::

        data: {"slot_name": "...", "image": "...", "state": "pulling",
               "layer": N, "total_layers": M}

    Terminal states: ``completed`` | ``failed`` | ``present`` | ``missing``.
    """

    async def _gen() -> Any:
        slot_pull_jobs: dict[str, Any] = getattr(request.app.state, "slot_pull_jobs", {})
        job = slot_pull_jobs.get(name)

        if job is None:
            # No active pull — inspect the image to surface present|missing.
            sm = _get_slot_manager(request)
            image = await _image_pull.resolve_slot_image(sm, name)
            state = await _image_pull.inspect_image_state(image)
            yield f"data: {json.dumps({'slot_name': name, 'image': image, 'state': state, 'layer': 0, 'total_layers': 0})}\n\n"
            return

        # Emit initial snapshot.
        yield f"data: {json.dumps(job.as_dict())}\n\n"
        last_layer = job.layer
        while job.state == "pulling":
            await asyncio.sleep(0.5)
            if job.layer != last_layer or job.state != "pulling":
                last_layer = job.layer
                yield f"data: {json.dumps(job.as_dict())}\n\n"
        # Terminal frame.
        yield f"data: {json.dumps(job.as_dict())}\n\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{name}/pull/status")
async def pull_slot_image_status(name: str, request: Request) -> dict[str, object]:
    """Poll fallback for container image-pull progress (mirror of #9).

    The SSE stream (``/{name}/pull/stream``) is the live path; this is the
    one-shot poll equivalent for clients that can't hold an EventSource
    open. Returns the in-flight job snapshot when a pull is active,
    otherwise inspects the slot's image and reports ``present`` |
    ``missing`` — the same terminal vocabulary the stream's no-job branch
    emits, so a poller and a streamer converge on the same states.

    Frame shape::

        {"slot_name": "...", "image": "...", "state": "...",
         "layer": N, "total_layers": M, "error": null}

    States: ``pulling`` | ``completed`` | ``failed`` | ``present`` |
    ``missing``.
    """
    slot_pull_jobs: dict[str, Any] = getattr(request.app.state, "slot_pull_jobs", {})
    job = slot_pull_jobs.get(name)
    if job is not None:
        return job.as_dict()

    # No active pull — resolve the slot's image + inspect presence so the
    # poller gets the same present|missing terminal the SSE stream emits.
    sm = _get_slot_manager(request)
    image = await _image_pull.resolve_slot_image(sm, name)
    state = await _image_pull.inspect_image_state(image)

    return {
        "slot_name": name,
        "image": image,
        "state": state,
        "layer": 0,
        "total_layers": 0,
        "error": None,
    }
