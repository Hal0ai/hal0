"""Gather the live inputs for the memory budget (ADR-0039).

:func:`hal0.hardware.memory_budget.build_memory_budget` is pure; this module is
the one place that reads what it needs from a running API process: the GPU
sample, the slot rows, the slot configs, ComfyUI's own figure and the RAM
reading. ``/api/stats/hardware`` serves the result; the image fit check and the
``/v1`` preflight (Plan 4b) call :func:`collect_memory_budget` too, so every
caller sees the same budget.

Everything here degrades instead of raising. A missing ``hardware_stats`` or
slot manager, a failing probe, a dead ComfyUI: each leaves its input empty and
a budget still comes back (with ``gpu`` ``None`` when nothing measured the
pool). The only exceptions that escape are bugs in this module.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import structlog

from hal0.hardware.gpu_view import GPUMemorySample
from hal0.hardware.memory_budget import MemoryBudget, build_memory_budget, read_ram

log = structlog.get_logger(__name__)

# Hard ceiling on the ComfyUI read. ``engine_used_mb`` has its own short HTTP timeouts and a
# 5 s cache; this is the belt to those braces, so a stuck read can never hold up a 2.5 s poll.
# Past it the img slot's own attribution stands in for the engine.
_ENGINE_WAIT_S = 0.75

# A slot config without a ``device`` runs on this one, as in ``gpu_exclusive_group``.
_DEFAULT_DEVICE = "gpu-rocm"


async def _gpu_sample(stats: Any) -> GPUMemorySample | None:
    """A live GPU sample off the event loop; None without a stats singleton or on failure."""
    if stats is None or not hasattr(stats, "gpu_sample"):
        return None
    try:
        return await asyncio.to_thread(stats.gpu_sample)
    except Exception:
        log.warning("memory_budget.gpu_sample_failed", exc_info=True)
        return None


async def _slot_roles(manager: Any) -> tuple[frozenset[str], str | None]:
    """``(gpu_slots, img_slot)`` from the raw slot configs.

    ``gpu_slots`` are the slots whose ``device`` starts with ``gpu``; ``img_slot`` is the
    first one the GPU arbiter groups as ``img``. Both empty without a slot manager.
    """
    if manager is None:
        return frozenset(), None
    from hal0.slots.arbiter import gpu_exclusive_group

    try:
        configs = await manager.iter_configs()
    except Exception:
        log.warning("memory_budget.slot_configs_failed", exc_info=True)
        return frozenset(), None
    gpu_slots: set[str] = set()
    img_slot: str | None = None
    for cfg in configs:
        name = str(cfg.get("name") or "")
        if not name:
            continue
        if str(cfg.get("device") or _DEFAULT_DEVICE).startswith("gpu"):
            gpu_slots.add(name)
        if img_slot is None and gpu_exclusive_group(cfg) == "img":
            img_slot = name
    return frozenset(gpu_slots), img_slot


async def _slot_rows(
    manager: Any,
    registry: Any,
    gpu_capable: bool,
    prebuilt: Mapping[str, Mapping[str, Any]] | None,
) -> Mapping[str, Mapping[str, Any]]:
    """``build_per_slot`` rows for the resident and the starting slots; {} on failure.

    ``prebuilt`` rows, when the caller has them, are used as they are.
    """
    if prebuilt is not None:
        return prebuilt
    if manager is None:
        return {}
    from hal0.slots import capacity

    try:
        slots = await manager.list()
        if not slots:
            return {}
        return await capacity.build_per_slot(
            slots, registry=registry, gpu_capable=gpu_capable, include_starting=True
        )
    except Exception:
        log.warning("memory_budget.slot_rows_failed", exc_info=True)
        return {}


async def _engine_used_mb(has_pool: bool) -> float | None:
    """ComfyUI's own usage; None without a GPU pool or when it is not read within the bound.

    Without a pool the budget has no GPU block to attribute the engine to, so ComfyUI is
    not asked.
    """
    if not has_pool:
        return None
    from hal0.api.routes import comfyui

    try:
        return await asyncio.wait_for(comfyui.engine_used_mb(), _ENGINE_WAIT_S)
    except Exception:  # includes the wait_for TimeoutError
        return None


async def collect_memory_budget(
    app: Any, *, per_slot: Mapping[str, Mapping[str, Any]] | None = None
) -> MemoryBudget:
    """Read this process's live memory inputs and build its :class:`MemoryBudget`.

    ``app`` is the FastAPI app, or anything with ``.state`` carrying
    ``hardware_stats``, ``slot_manager`` and ``model_registry``; each may be absent.
    The RAM and GPU reads run in threads. The ComfyUI read is bounded by
    :data:`_ENGINE_WAIT_S`, and is skipped when there is no GPU pool to attribute it to.

    ``per_slot`` is ``build_per_slot(..., include_starting=True)`` output the caller
    already built this request; passing it saves a second round of per-slot cgroup
    probes. When omitted it is built here.
    """
    from hal0.slots import capacity

    state = app.state
    manager = getattr(state, "slot_manager", None)
    sample, gpu_capable, ram, (gpu_slots, img_slot) = await asyncio.gather(
        _gpu_sample(getattr(state, "hardware_stats", None)),
        asyncio.to_thread(capacity._host_has_capable_gpu),
        asyncio.to_thread(read_ram),
        _slot_roles(manager),
    )
    engine_mb, rows = await asyncio.gather(
        _engine_used_mb(gpu_capable and sample is not None),
        _slot_rows(manager, getattr(state, "model_registry", None), gpu_capable, per_slot),
    )
    return build_memory_budget(
        sample=sample,
        gpu_capable=gpu_capable,
        per_slot=rows,
        gpu_slots=gpu_slots,
        img_slot=img_slot,
        image_engine_mb=engine_mb,
        ram=ram,
    )
