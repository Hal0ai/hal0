"""Memory budget model — the single owner of the dashboard's memory figures.

ADR-0039: every memory figure the dashboard shows comes from one
:class:`MemoryBudget`, built here and nowhere else. A route serves it, the UI
renders it; neither re-derives a number. Before this module the GPU pool, the
per-slot attribution, ComfyUI's own figure and the box's RAM were combined in
several places, so the same pool read three different ways.

The budget has two halves, which are different resources and are never
summed together:

* **The GPU pool** (:class:`GpuBudget`). On a UMA part (Strix Halo et al.)
  the model-loading pool is GTT, shared with the host and with every other
  guest; on a discrete GPU it is dedicated VRAM. The pool total and usage are
  read live from the sample (``gtt_*`` when shared, ``vram_*`` when dedicated
  -- never the max-pooled ``total_mb``, which can pick the wrong counter).
  Usage is then *attributed*: slots on this box, the image engine, and
  whatever is left over, which belongs to other guests on the shared pool.
* **This box's RAM** (:class:`RamBudget`), passed in by the caller, already
  resolved to the container cgroup limit or the system total.

This module is pure -- no I/O, no clock, no logging. Callers gather the
inputs (the GPU sample, ``build_per_slot`` rows, ComfyUI's memory view, the RAM
reading) and hand them to :func:`build_memory_budget`.

Units: "GB" here is MiB / 1024, and every GB field is rounded to one decimal
when the dataclass is built. Derived values (``other_guests_gb``,
``attribution_excess_gb``, ``free_gb``, ``render_free_gb``) are computed from
the unrounded MiB first and rounded once at the end, so rounding error never
accumulates through the subtraction chain.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from hal0.hardware.gpu_view import GPUMemorySample

# Headroom kept back from a render's budget for the driver, the compositor and
# allocator slop. Subtracted once, from the pool, in ``render_free_gb``.
RENDER_MARGIN_GB = 2.0

_STARTING = "starting"


@dataclass(frozen=True, slots=True)
class RamBudget:
    """This box's RAM: the container's cgroup limit, or the system total."""

    limit_gb: float
    used_gb: float
    free_gb: float
    source: Literal["container", "system"]


@dataclass(frozen=True, slots=True)
class SlotShare:
    """One slot's attributed share of the GPU pool."""

    slot: str
    gb: float
    state: str


@dataclass(frozen=True, slots=True)
class GpuBudget:
    """The GPU pool, split into who holds it.

    ``used_gb`` is the live pool usage. It is split into ``this_box`` (the
    other GPU slots on this box), ``image_engine_gb`` (ComfyUI) and
    ``other_guests_gb`` (the unattributed remainder: other guests sharing the
    pool). ``attribution_excess_gb`` is the opposite remainder -- how far the
    attributed footprints overshoot the live usage (estimates, not readings).
    """

    kind: Literal["shared", "dedicated"]
    total_gb: float
    used_gb: float
    this_box: tuple[SlotShare, ...]
    image_engine_gb: float
    other_guests_gb: float
    attribution_excess_gb: float
    free_gb: float


@dataclass(frozen=True, slots=True)
class MemoryBudget:
    """The whole budget: the GPU pool (``None`` without a GPU lane) and RAM.

    ``render_free_gb`` is how much pool a render can use once image mode has
    unloaded this box's resident GPU slots; ``None`` exactly when ``gpu`` is.
    """

    gpu: GpuBudget | None
    ram: RamBudget
    render_free_gb: float | None

    def as_dict(self) -> dict[str, Any]:
        """JSON-native form; keys are the dataclass field names, nested too."""
        out = dataclasses.asdict(self)
        gpu = out["gpu"]
        if gpu is not None:
            gpu["this_box"] = list(gpu["this_box"])
        return out


def _gb(mb: float) -> float:
    return round(mb / 1024.0, 1)


def _mb(value: Any) -> float:
    """A MiB reading as a non-negative float; missing or unparseable is 0."""
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _state(row: Mapping[str, Any]) -> str:
    return str(row.get("state") or "").lower()


def build_memory_budget(
    *,
    sample: GPUMemorySample | None,
    gpu_capable: bool,
    per_slot: Mapping[str, Mapping[str, Any]],
    gpu_slots: frozenset[str],
    img_slot: str | None,
    image_engine_mb: float | None,
    ram: RamBudget,
) -> MemoryBudget:
    """Combine the live readings into one :class:`MemoryBudget`.

    ``per_slot`` is ``build_per_slot`` output (``{slot: {"vram_mb", "ram_mb",
    "mem_mb", "state", "model_id"}}``); a row without ``vram_mb`` counts as 0.
    ``gpu_slots`` names the slots that draw on the GPU pool -- an NPU slot's row
    is ignored. ``img_slot`` is the image-engine slot; its row stands in for the
    engine only when ``image_engine_mb`` (ComfyUI's own view, which wins when
    present -- even at 0) is ``None``.

    ``gpu`` and ``render_free_gb`` are ``None`` when the box has no GPU lane, no
    sample was read, or the sample carries no total for its pool. A missing
    ``used`` reading with a known total is treated as 0 used.

    ``this_box`` lists the other GPU slots that hold a row, ordered by slot name.
    A slot still ``"starting"`` is listed, but it is not yet a resident
    footprint: it is left out of the attribution sums (so its usage reads as
    ``other_guests`` rather than as an overshoot) and subtracted from
    ``render_free_gb`` instead, because it finishes loading onto the pool
    (#3056).
    """
    if not gpu_capable or sample is None:
        return MemoryBudget(gpu=None, ram=ram, render_free_gb=None)

    shared = sample.is_uma
    total_mb = sample.gtt_total_mb if shared else sample.vram_total_mb
    used_raw = sample.gtt_used_mb if shared else sample.vram_used_mb
    if total_mb is None:
        return MemoryBudget(gpu=None, ram=ram, render_free_gb=None)
    used_mb = _mb(used_raw)

    shares: list[tuple[str, float, str]] = []
    for name in sorted(gpu_slots):
        row = per_slot.get(name)
        if name == img_slot or row is None:
            continue
        shares.append((name, _mb(row.get("vram_mb")), _state(row)))

    if image_engine_mb is not None:
        engine_mb = _mb(image_engine_mb)
    else:
        img_row = per_slot.get(img_slot) if img_slot is not None else None
        engine_mb = _mb(img_row.get("vram_mb")) if img_row is not None else 0.0

    resident_mb = sum(mb for _, mb, state in shares if state != _STARTING)
    starting_mb = sum(mb for _, mb, state in shares if state == _STARTING)

    other_guests_mb = max(0.0, used_mb - resident_mb - engine_mb)
    excess_mb = max(0.0, resident_mb + engine_mb - used_mb)
    free_mb = max(0.0, total_mb - used_mb)
    render_free_mb = max(
        0.0,
        total_mb - other_guests_mb - engine_mb - starting_mb - RENDER_MARGIN_GB * 1024.0,
    )

    gpu = GpuBudget(
        kind="shared" if shared else "dedicated",
        total_gb=_gb(total_mb),
        used_gb=_gb(used_mb),
        this_box=tuple(SlotShare(slot=n, gb=_gb(mb), state=st) for n, mb, st in shares),
        image_engine_gb=_gb(engine_mb),
        other_guests_gb=_gb(other_guests_mb),
        attribution_excess_gb=_gb(excess_mb),
        free_gb=_gb(free_mb),
    )
    return MemoryBudget(gpu=gpu, ram=ram, render_free_gb=_gb(render_free_mb))
