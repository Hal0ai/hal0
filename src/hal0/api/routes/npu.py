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

from typing import Any

from fastapi import APIRouter, Request

from hal0.dispatcher.npu_swap_status import (
    NpuSwapStatus,
    fetch_npu_swap_status,
)

router = APIRouter()

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
    if sm is not None:
        try:
            all_slots = await sm.list()
        except Exception:
            all_slots = []
        for s in all_slots:
            meta = getattr(s, "metadata", None) or {}
            provider = str(meta.get("provider") or "").lower()
            backend = str(getattr(s, "backend", None) or meta.get("backend") or "").lower()
            if provider == "flm" or backend in ("flm", "npu"):
                flm_slots.append(s)

    try:
        npu_present = await _npu_status(request) is not None
    except Exception:
        npu_present = False

    if not npu_present and not flm_slots:
        return _occupancy_absent()

    slots_out: list[dict[str, Any]] = []
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

        tag = _model_tag(getattr(s, "model_id", None))
        gb = _flm_footprint_gb(tag)

        cols: list[int] = []
        is_loaded = raw_state in _LOADED_STATES
        if is_loaded:
            probe = await cached_aie_columns(f"hal0-slot-{s.name}")
            if probe and probe.get("partitions"):
                columns_available = True
                for part in probe["partitions"]:
                    start = int(part["start_col"])
                    num = int(part["num_cols"])
                    cols.extend(range(start, start + num))
                # Clamp to silicon geometry + dedupe while keeping order.
                seen: set[int] = set()
                cols = [c for c in cols if 0 <= c < _NPU_COLS and not (c in seen or seen.add(c))]

        slots_out.append(
            {
                "name": s.name,
                "model": tag,
                "state": mapped_state,
                "cols": cols,
                "gb": gb,
            }
        )

    # Degraded fallback: if xrt-smi never succeeded, every loaded slot owns
    # all 8 columns (single-tenant binary occupancy).
    if not columns_available:
        any_loaded = False
        for slot_view, s in zip(slots_out, flm_slots, strict=True):
            raw_state = str(getattr(s, "state", "") or "").lower()
            state_val = getattr(getattr(s, "state", None), "value", None)
            if isinstance(state_val, str):
                raw_state = state_val.lower()
            if raw_state in _LOADED_STATES:
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

    return {
        "present": True,
        "rows": _NPU_ROWS,
        "cols": _NPU_COLS,
        "tiles": _NPU_TILES,
        "tops_peak": _NPU_TOPS_PEAK,
        "cols_total": _NPU_COLS,
        "cols_used": cols_used,
        "serving": any_serving,
        "single_tenant": True,
        "columns_available": columns_available,
        "slots": slots_out,
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
