"""NPU-specific dashboard endpoints — PR-20.

Mounted under ``/api/npu`` (see :mod:`hal0.api.__init__`):

  - ``GET /api/npu/swap-status`` — returns whether the FLM trio's chat
    model is currently mid-swap, plus the ``from_model`` / ``to_model``
    pair when the swap is observable. Polled by the dashboard's NPU
    block to surface a "Swap incoming" banner + spinner.

Why a dedicated route (not the slot-state SSE stream): the swap-status
signal derives from slot TOML + slot lifecycle state; a simple poll
endpoint keeps the dashboard's swap banner re-polling every few seconds
without holding a long-lived connection open.

The endpoint is read-only and falls back to ``in_progress=false`` on
every error path (no SlotManager, accessor errors, etc.).
"""

from __future__ import annotations

import contextlib
import copy
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Request

from hal0.dispatcher.npu_swap_status import (
    NpuSwapStatus,
    fetch_npu_swap_status,
)
from hal0.slots.naming import slot_container_name, slot_token_for

log = structlog.get_logger(__name__)

router = APIRouter()


def _slots_dir() -> Path:
    """Return /etc/hal0/slots/ — the TOML config directory."""
    return Path("/etc/hal0/slots")


# Strix Halo XDNA NPU geometry — fixed silicon: 4 rows by 8 cols = 32 AIE
# tiles, ~50 TOPS peak (single-tenant). These never change at runtime; the
# occupancy card renders the tile grid from them.
_NPU_ROWS = 4
_NPU_COLS = 8
_NPU_TILES = 32
_NPU_TOPS_PEAK = 50

# Slot states (lower-cased SlotState values) where the model is resident on
# the NPU and therefore owns AIE columns. SERVING/READY/WARMING/IDLE all
# hold weights in GTT; OFFLINE/PULLING/STARTING/UNLOADING/ERROR do not.
_LOADED_STATES = frozenset({"serving", "ready", "warming", "idle"})
# The _map_slot_state() outputs that count as "loaded" (warming → "loaded").
# Used by the degraded single-tenant fallback, which works off the already-
# mapped slot_view["state"] rather than the raw SlotState.
_LOADED_VIEW_STATES = frozenset({"serving", "ready", "loaded", "idle"})

# A capability counts as "actively hitting the NPU" when its slot was used
# within this window. Must exceed the dashboard's 2.5s occupancy poll so a
# single request is catchable by at least one poll; short enough that the
# grid's per-capability tint fades soon after traffic stops.
_ACTIVE_WINDOW_S = 6.0


def _last_used_age_s(app_state: Any, slot_name: str) -> float | None:
    """Seconds since *slot_name* last served a request, or ``None``.

    Reads the ``app.state.slot_last_used`` monotonic timestamps that v1.py
    stamps on every chat response (``_record_nonstreaming_throughput`` /
    the streaming counter) and on every trio STT/embed dispatch
    (``_touch_npu_shadow_count``).
    """
    last_used = getattr(app_state, "slot_last_used", None) or {}
    ts = last_used.get(slot_name)
    if not isinstance(ts, (int, float)):
        return None
    return max(0.0, time.monotonic() - float(ts))


# ── engine + host facts ──────────────────────────────────────────────────────
#
# ``npu_engines`` / ``npu_host`` ride on BOTH occupancy shapes so the NPU tab's
# status line and readiness checklist have real facts even with no NPU slot.
# Every fact is real or ``None`` — a failed read never fails the route.

_IMAGE_STORE_TTL_S = 30.0
_image_store_cache: tuple[float, set[str] | None] | None = None
_image_refresh_lock = threading.Lock()
_image_refresh_thread: threading.Thread | None = None
# Bumped by ``invalidate_engine_caches``; a refresh that started before the bump
# read the store before the change and must not repopulate the cache with it.
_cache_generation = 0
# The generation the cached store value was read under.
_image_store_gen = 0


def invalidate_engine_caches() -> None:
    """Mark the image-store and facts caches stale (keeping their last values).

    Called when the local image store changes (a runner-image pull or delete
    finishes) so the next poll re-reads instead of waiting out both 30 s TTLs.
    The last value keeps being served while the refresh runs, so the facts never
    flicker to ``None``. Safe to call from any thread.
    """
    global _image_store_cache, _facts_cache, _cache_generation
    with _image_refresh_lock:
        _cache_generation += 1
        if _image_store_cache is not None:
            _image_store_cache = (float("-inf"), _image_store_cache[1])
        if _facts_cache is not None:
            _facts_cache = (float("-inf"), _facts_cache[1])


def _read_image_store() -> set[str] | None:
    """Blocking podman store read (seam + rootless fallback, up to ~20 s)."""
    try:
        from hal0.providers.podman_introspect import images_digests

        local = images_digests()
        return None if local is None else set(local.refs) | set(local.refs.values())
    except Exception:
        log.warning("npu.image_store_read_failed", exc_info=True)
        return None


def _refresh_image_store() -> None:
    global _image_store_cache, _image_refresh_thread, _image_store_gen
    gen = _cache_generation
    try:
        result = _read_image_store()
    except Exception:
        result = None
    with _image_refresh_lock:
        if gen == _cache_generation:
            _image_store_cache = (time.monotonic(), result)
            _image_store_gen = gen
        _image_refresh_thread = None


def _local_image_refs() -> set[str] | None:
    """Refs and digests in the local podman image store, or ``None``.

    Never blocks on podman: stale-while-revalidate with single-flight. Returns
    the cached value (stale included; ``None`` before the first read lands or
    when the last read failed) and, when the cache is missing or older than
    ``_IMAGE_STORE_TTL_S``, starts at most one background refresh. A failed
    refresh is cached as ``None`` for the TTL too.

    Exception: after ``invalidate_engine_caches`` the cached value is known to
    be wrong, not merely old, so this reads the store inline instead. Its only
    caller is the facts refresh worker thread, never the request path.
    """
    global _image_refresh_thread, _image_store_cache, _image_store_gen
    with _image_refresh_lock:
        gen = _cache_generation
        invalidated = _image_store_cache is not None and _image_store_gen != gen
    if invalidated:
        result = _read_image_store()
        with _image_refresh_lock:
            if gen == _cache_generation:
                _image_store_cache = (time.monotonic(), result)
                _image_store_gen = gen
        return result
    with _image_refresh_lock:
        cached = _image_store_cache
        fresh = cached is not None and time.monotonic() - cached[0] < _IMAGE_STORE_TTL_S
        if not fresh and _image_refresh_thread is None:
            _image_refresh_thread = threading.Thread(
                target=_refresh_image_store, name="npu-image-store", daemon=True
            )
            _image_refresh_thread.start()
        return cached[1] if cached is not None else None


def _npu_host_probe() -> dict[str, Any]:
    from hal0.mcp.probes import npu_status

    return npu_status()


def _image_pulled(ref: str, local: set[str] | None) -> bool | None:
    if local is None:
        return None
    base, _, digest = ref.partition("@")
    return ref in local or base in local or (bool(digest) and digest in local)


def _engine_facts_sync() -> tuple[dict[str, Any], dict[str, Any]]:
    from hal0.api.routes.updater import _parse_flm_version_from_image

    flm_ref: str | None = None
    xdna_ref: str | None = None
    try:
        from hal0.api.routes.runner_images import _effective_defaults

        defaults = _effective_defaults()
        flm_ref = (defaults.get("flm") or ("", ""))[0] or None
        xdna_ref = (defaults.get("xdna") or ("", ""))[0] or None
    except Exception:
        log.warning("npu.engine_images_unreadable", exc_info=True)

    flm_version: str | None = None
    with contextlib.suppress(Exception):
        flm_version = _parse_flm_version_from_image(flm_ref)

    xdna_tag: str | None = None
    xdna_pulled: bool | None = None
    if xdna_ref:
        last = xdna_ref.split("@", 1)[0].rsplit("/", 1)[-1]
        xdna_tag = last.rsplit(":", 1)[-1] if ":" in last else None
        try:
            xdna_pulled = _image_pulled(xdna_ref, _local_image_refs())
        except Exception:
            xdna_pulled = None

    host: dict[str, Any] = {"device_node": None, "driver_loaded": None, "xdna_gen": None}
    try:
        probed = _npu_host_probe()
        for key in host:
            host[key] = probed.get(key)
    except Exception:
        log.warning("npu.host_probe_failed", exc_info=True)

    engines = {
        "flm": {"image": flm_ref, "version": flm_version},
        "xdna": {"image": xdna_ref, "tag": xdna_tag, "pulled": xdna_pulled},
    }
    return engines, host


_FACTS_TTL_S = 30.0
_facts_cache: tuple[float, dict[str, Any]] | None = None
_facts_refresh_thread: threading.Thread | None = None


def _none_facts() -> dict[str, Any]:
    """The all-``None`` facts shape: what a failed or not-yet-read block reports."""
    return {
        "npu_engines": {
            "flm": {"image": None, "version": None},
            "xdna": {"image": None, "tag": None, "pulled": None},
        },
        "npu_host": {"device_node": None, "driver_loaded": None, "xdna_gen": None},
    }


def _refresh_facts() -> None:
    """Blocking read of the whole facts block; stores it and clears the in-flight thread."""
    global _facts_cache, _facts_refresh_thread
    gen = _cache_generation
    try:
        engines, host = _engine_facts_sync()
        facts = {"npu_engines": engines, "npu_host": host}
    except Exception:
        log.warning("npu.engine_facts_failed", exc_info=True)
        facts = _none_facts()
    with _image_refresh_lock:
        if gen == _cache_generation:
            _facts_cache = (time.monotonic(), facts)
        _facts_refresh_thread = None


async def _engine_and_host_facts() -> dict[str, Any]:
    """``{"npu_engines": ..., "npu_host": ...}`` -- never raises, never blocks.

    The occupancy route is polled every ~2.5 s, so the block (a thread, ``lsmod``,
    the image store) is cached for ``_FACTS_TTL_S``: stale-while-revalidate with
    one refresh in flight. The very first call, before any read lands, reports
    the all-``None`` shape.
    """
    global _facts_refresh_thread
    with _image_refresh_lock:
        cached = _facts_cache
        fresh = cached is not None and time.monotonic() - cached[0] < _FACTS_TTL_S
        if not fresh and _facts_refresh_thread is None:
            _facts_refresh_thread = threading.Thread(
                target=_refresh_facts, name="npu-facts", daemon=True
            )
            _facts_refresh_thread.start()
    if cached is None:
        return _none_facts()
    return copy.deepcopy(cached[1])


def _occupancy_absent() -> dict[str, Any]:
    """The minimal ``present:false`` payload (no NPU hw / no flm slot)."""
    return {
        "present": False,
        "rows": _NPU_ROWS,
        "cols": _NPU_COLS,
        "tiles": _NPU_TILES,
        "tops_peak": _NPU_TOPS_PEAK,
        "cols_total": _NPU_COLS,
        "cols_used": 0,
        "serving": False,
        "single_tenant": True,
        "columns_available": False,
        "slots": [],
    }


def _map_slot_state(state: str) -> str:
    """Map a lower-cased :class:`SlotState` value to the card's 5 strings.

    Contract states: ``serving | ready | loaded | idle | offline``.
      - ``serving``  ← SERVING (request in-flight)
      - ``ready``    ← READY (up, healthy)
      - ``loaded``   ← WARMING (container up, model loading into GTT)
      - ``idle``     ← IDLE (up but not serving)
      - ``offline``  ← OFFLINE / PULLING / STARTING / UNLOADING / ERROR
    """
    if state == "serving":
        return "serving"
    if state == "ready":
        return "ready"
    if state == "warming":
        return "loaded"
    if state == "idle":
        return "idle"
    return "offline"


def _flm_footprint_gb(model_tag: str | None) -> float | None:
    """Resident footprint (GiB, 1 decimal) for *model_tag* from FLM's catalog.

    Returns ``None`` when the catalog probe fails or the tag is unknown so
    the slot reports ``gb: null`` rather than a misleading 0.
    """
    if not model_tag:
        return None
    try:
        from hal0.providers.flm import flm_served_models

        catalog = {e["tag"]: e for e in flm_served_models()}
    except Exception:
        return None
    entry = catalog.get(model_tag)
    if not entry:
        return None
    footprint_gb = entry.get("footprint_gb") or 0.0
    if footprint_gb > 0:
        return round(float(footprint_gb), 1)
    size_bytes = entry.get("size_bytes") or 0
    if size_bytes > 0:
        return round(size_bytes / (1024.0**3), 1)
    return None


def _model_tag(model_id: str | None) -> str | None:
    """Strip a hal0 ``<tag>-FLM`` id back to FLM's native ``family:size`` tag.

    Falls back to a bare ``-FLM`` suffix strip when the catalog probe can't
    resolve the id; passes other ids through untouched.
    """
    if not model_id:
        return None
    try:
        from hal0.providers.flm import flm_id_to_tag

        resolved = flm_id_to_tag(model_id)
    except Exception:
        resolved = None
    if resolved:
        return resolved
    if model_id.endswith("-FLM"):
        return model_id[: -len("-FLM")]
    return model_id


@router.get("/occupancy")
async def npu_occupancy(request: Request) -> dict[str, Any]:
    """Return the honest NPU column-allocation + slot composition.

    Read-only. Composes:

      1. NPU presence (hardware probe via :func:`hardware._npu_status`).
      2. FLM/NPU slots from the SlotManager (one ``slots[]`` entry each).
      3. AIE column allocation per loaded slot — exec ``xrt-smi`` inside the
         live container (cached). On success the slot owns
         ``start_col..start_col+num_cols-1`` and ``columns_available=true``.
         On any failure DEGRADE: each loaded slot owns all 8 columns
         (single-tenant binary fallback) and ``columns_available=false``.

    Always 200. Returns the ``present:false`` payload when there is no NPU
    hardware AND no flm/npu slot configured.
    """
    from hal0.api.routes.hardware import _npu_status
    from hal0.providers.npu_columns import cached_aie_columns

    sm = getattr(request.app.state, "slot_manager", None)

    # Gather flm/npu slots first — presence is "hw present OR a flm slot".
    flm_slots: list[Any] = []
    all_slots: list[Any] = []
    # xdna slots (ADR-0057), named from their config: their device is npu, so
    # the backend mirror alone could read as FLM on an older state record.
    xdna_names: set[str] = set()
    if sm is not None:
        try:
            all_slots = await sm.list()
        except Exception:
            all_slots = []
        try:
            from hal0.slots.activation import is_xdna_slot

            xdna_names = {
                str(cfg.get("name") or "")
                for cfg in await sm.iter_configs()
                if isinstance(cfg, dict) and is_xdna_slot(cfg)
            }
        except Exception:
            xdna_names = set()
        for s in all_slots:
            meta = getattr(s, "metadata", None) or {}
            provider = str(meta.get("provider") or "").lower()
            backend = str(getattr(s, "backend", None) or meta.get("backend") or "").lower()
            # Skip shadow slots — they ride on the FLM trio, no separate process
            served = getattr(s, "served_by", None)
            if served:
                continue
            # flm-stt / flm-embed are companion slots whose occupancy is
            # reported via virtual synthesis from the FLM anchor's TOML —
            # they never appear as standalone entries. An xdna slot an operator
            # happened to name ``*-stt`` / ``*-embed`` is a real process holding
            # the NPU, so it is never skipped.
            if s.name not in xdna_names and (s.name.endswith("-stt") or s.name.endswith("-embed")):
                continue
            if provider == "flm" or backend in ("flm", "npu") or s.name in xdna_names:
                flm_slots.append(s)

    try:
        npu_present = await _npu_status(request) is not None
    except Exception:
        npu_present = False

    if not npu_present and not flm_slots:
        return {**_occupancy_absent(), **await _engine_and_host_facts()}

    if flm_slots:
        # _model_tag / _flm_footprint_gb below read the FLM catalog
        # synchronously; warm a cold cache off the event loop first (#2334).
        from hal0.providers.flm import flm_served_models_async

        with contextlib.suppress(Exception):
            await flm_served_models_async()

    slots_out: list[dict[str, Any]] = []
    # Real NPU tenants (one per process). The synthesized -stt/-embed rows
    # ride on their FLM anchor's process and are never counted here.
    tenant_names: list[str] = []
    columns_available = False
    cols_used = 0
    any_serving = False

    for s in flm_slots:
        raw_state = str(getattr(s, "state", "") or "").lower()
        # SlotState may be an enum — its .value is already lower-case.
        state_val = getattr(getattr(s, "state", None), "value", None)
        if isinstance(state_val, str):
            raw_state = state_val.lower()
        mapped_state = _map_slot_state(raw_state)
        if mapped_state == "serving":
            any_serving = True
        if mapped_state in _LOADED_VIEW_STATES:
            tenant_names.append(s.name)

        meta = getattr(s, "metadata", None) or {}
        provider = str(meta.get("provider") or "").lower()
        backend = str(getattr(s, "backend", None) or meta.get("backend") or "").lower()
        engine = (
            "xdna" if s.name in xdna_names or (provider != "flm" and backend == "npu") else "flm"
        )

        tag = _model_tag(getattr(s, "model_id", None))
        gb = _flm_footprint_gb(tag)

        cols: list[int] = []
        is_loaded = raw_state in _LOADED_STATES
        if is_loaded:
            probe = await cached_aie_columns(slot_container_name(await slot_token_for(sm, s.name)))
            if probe and probe.get("partitions"):
                columns_available = True
                for part in probe["partitions"]:
                    start = int(part["start_col"])
                    num = int(part["num_cols"])
                    cols.extend(range(start, start + num))
                # Clamp to silicon geometry + dedupe while keeping order.
                seen: set[int] = set()
                cols = [c for c in cols if 0 <= c < _NPU_COLS and not (c in seen or seen.add(c))]

        # Per-capability activity: chat is "active" while a request is
        # in flight (SERVING) or was answered within the window; the
        # trio's shadow roles below key off their own last-used stamps.
        chat_age = _last_used_age_s(request.app.state, s.name)
        slots_out.append(
            {
                "name": s.name,
                "model": tag,
                "state": mapped_state,
                "engine": engine,
                "cols": cols,
                "gb": gb,
                "active": mapped_state == "serving"
                or (chat_age is not None and chat_age <= _ACTIVE_WINDOW_S),
                "last_used_age_s": round(chat_age, 1) if chat_age is not None else None,
            }
        )

        # Synthesise virtual sub-slots for STT + embed so the NPU pane
        # shows three glowing cards when the FLM trio is running.
        npu_cfg = getattr(s, "npu", None) or {}
        # The slot object may not carry the [npu] table — read from the TOML.
        if not npu_cfg:
            try:
                toml_path = _slots_dir() / f"{s.name}.toml"
                if toml_path.exists():
                    raw = tomllib.loads(toml_path.read_text())
                    npu_cfg = raw.get("npu") or {}
            except Exception:
                pass

        # Virtual subs ride coresident with the FLM anchor: the [npu] toggle
        # (asr/embed) IS the operator control — the single `flm serve` child
        # serves them, so they're live whenever the modality is on and the
        # anchor is loaded. (The old code gated on the shadow slot's own
        # `enabled` flag, which reads false in embed/STT-primary mode where
        # the operator disables chat but the modality is still served — so
        # the sub-card wrongly showed "off"/dim.)
        if npu_cfg.get("asr"):
            stt_live = is_loaded
            stt_age = _last_used_age_s(request.app.state, s.name + "-stt")
            slots_out.append(
                {
                    "name": s.name + "-stt",
                    "model": "whisper-v3:turbo",
                    "state": mapped_state if stt_live else "off",
                    "engine": "flm",
                    "cols": list(range(_NPU_COLS)) if stt_live else [],
                    "gb": None,
                    "active": stt_live and stt_age is not None and stt_age <= _ACTIVE_WINDOW_S,
                    "last_used_age_s": round(stt_age, 1) if stt_age is not None else None,
                    "served_by": s.name,
                }
            )
        if npu_cfg.get("embed"):
            embed_live = is_loaded
            embed_age = _last_used_age_s(request.app.state, s.name + "-embed")
            slots_out.append(
                {
                    "name": s.name + "-embed",
                    "model": "embedding-gemma",
                    "state": mapped_state if embed_live else "off",
                    "engine": "flm",
                    "cols": list(range(_NPU_COLS)) if embed_live else [],
                    "gb": None,
                    "active": embed_live
                    and embed_age is not None
                    and embed_age <= _ACTIVE_WINDOW_S,
                    "last_used_age_s": round(embed_age, 1) if embed_age is not None else None,
                    "served_by": s.name,
                }
            )

    # Sort: flm first, then flm-stt, flm-embed in alphabetical order
    slots_out.sort(
        key=lambda s: (
            0
            if s["name"] == "flm"
            else 1
            if s["name"] == "flm-stt"
            else 2
            if s["name"] == "flm-embed"
            else 9
        )
    )

    # Degraded fallback: if xrt-smi never succeeded, every loaded slot owns
    # all 8 columns (single-tenant binary occupancy). Iterate slots_out on its
    # own using each view's already-mapped state — the old code zipped it with
    # flm_slots (strict=True), which crashed the whole endpoint: slots_out also
    # carries the synthesised -stt/-embed sub-views AND is re-sorted, so it is
    # both longer than and mis-ordered vs flm_slots. Triggered exactly when the
    # NPU column probe is unavailable — i.e. while the FLM slot is offline.
    if not columns_available:
        any_loaded = False
        for slot_view in slots_out:
            if slot_view["state"] in _LOADED_VIEW_STATES:
                slot_view["cols"] = list(range(_NPU_COLS))
                any_loaded = True
            else:
                slot_view["cols"] = []
        cols_used = _NPU_COLS if any_loaded else 0
    else:
        used: set[int] = set()
        for slot_view in slots_out:
            used.update(slot_view["cols"])
        cols_used = min(len(used), _NPU_COLS)

    # Coexistence (xdna-runner plan): an xdna (llama-server/npu backend) slot
    # and the flm trio each independently hold the one physical NPU — hal0
    # never blocks either from loading alongside the other (spec-hw-slot-
    # ownership never arbitrates npu/cpu devices), but time-slicing throughput
    # is a real cost the operator should see. Surface it as a warning on the
    # occupancy payload (and the activation log) rather than silently.
    npu_shared_warning: str | None = None
    if len(tenant_names) > 1:
        npu_shared_warning = f"NPU shared with slot {tenant_names[0]!r}; throughput is time-sliced"
        log.warning(
            "npu.shared_occupancy",
            slots=tenant_names,
        )

    return {
        "present": True,
        "rows": _NPU_ROWS,
        "cols": _NPU_COLS,
        "tiles": _NPU_TILES,
        "tops_peak": _NPU_TOPS_PEAK,
        "cols_total": _NPU_COLS,
        "cols_used": cols_used,
        "serving": any_serving,
        "single_tenant": len(tenant_names) <= 1,
        "columns_available": columns_available,
        "slots": slots_out,
        "shared_warning": npu_shared_warning,
        **await _engine_and_host_facts(),
    }


@router.get("/swap-status")
async def get_swap_status(request: Request) -> dict[str, Any]:
    """Return the current NPU trio chat-model swap-in-progress snapshot.

    Response shape::

        {
          "in_progress": bool,
          "from_model": "gemma3:1b" | null,
          "to_model":   "llama3.2-3b-npu" | null
        }

    Always 200. Returns ``in_progress=false`` whenever:

      - No SlotManager is wired (test bypass).
      - No NPU LLM slot is enabled.
      - The configured NPU LLM model already matches the loaded one.
    """
    sm = getattr(request.app.state, "slot_manager", None)

    if sm is None:
        # Pre-lifespan / test bypass. Return a stable empty payload so
        # the dashboard poll never sees a 500 here.
        return NpuSwapStatus(in_progress=False, from_model=None, to_model=None).to_dict()

    try:
        slot_configs = await sm.iter_configs()
    except Exception:
        # Defensive: a config read error should not propagate to the
        # status endpoint. iter_configs already swallows individual
        # malformed TOMLs (see :meth:`SlotManager.iter_configs`); this
        # catch covers the (unlikely) directory-level failure.
        slot_configs = []

    status = await fetch_npu_swap_status(slot_configs, slot_manager=sm)
    return status.to_dict()


__all__ = ["router"]
