"""The fit verdict: does an image model, at a size, fit this box's memory right now?

ADR-0037 gave image models a catalog and ADR-0039 gave memory one owner. This module joins
them. :func:`need_for` reads the catalog's memory facts (``gpu_peak_gb``, ``load_ram_gb`` and
the weight total) into a :class:`Need`; :func:`assess` compares a need with a
:class:`~hal0.hardware.memory_budget.MemoryBudget` and answers with a :class:`Fit`. Both are
pure: the caller collects the budget (``hal0.api.memory_source.collect_memory_budget``), so
the models routes, ``/v1/images/generations`` and the dashboard all judge the same figures.

Units: "GB" is GiB (bytes / 2**30), like the budget, and every figure is rounded to one
decimal. The verdict compares the rounded figures, so the sentence in ``reason`` can never
contradict it.

Two budgets, two needs, never summed: the GPU pool and this box's RAM are different
resources. The verdict is the worse of the two, and ``reason`` names the one that decided it
(the GPU when both are equally bad).

What the figures mean:

* **GPU need** is the pool a render takes *above an idle engine* at one size, the same
  increment ``render_free_gb`` leaves room for (the budget already subtracts the idle
  engine, other guests, slots still loading and a 2 GB margin). Measured sizes are exact.
  Any other size is an estimate that never undershoots the measurements: the smallest
  measured size at least as large, or the largest measurement scaled by pixel count.
  A model nobody measured needs its weight total, which is a lower bound (SD 1.5 weighs
  4.0 GB and was measured at 11.6).
* **RAM need** is what the load takes. A measured ``load_ram_gb`` is a *floor* (the
  anonymous RSS the kernel reported when it killed the load), so it never lowers the need
  below the weight total. Without one the need is the weight total, estimated.

Both kinds of figure are lower bounds, so a refusal built on them stands; a "fits" built on an
estimate keeps the normal out-of-memory error path.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import Any, Final, Literal

from hal0.comfyui.catalog import ImageModel, weights_gib
from hal0.hardware.memory_budget import MemoryBudget, RamBudget

Verdict = Literal["fits", "tight", "wont_fit"]

# A need above this share of what is free is "tight": it fits, with little to spare.
TIGHT_SHARE: Final = 0.9

_SIZE: Final = re.compile(r"([0-9]+)x([0-9]+)")

# The worse verdict wins; the order is the severity.
_SEVERITY: Final[dict[Verdict, int]] = {"fits": 0, "tight": 1, "wont_fit": 2}


@dataclass(frozen=True, slots=True)
class Need:
    """What one render needs, in GB (GiB), and whether each figure is an estimate.

    ``gpu_estimated`` is false only for a size the catalog measured. ``ram_estimated`` is
    false only when the catalog measured a load (``load_ram_gb``).
    """

    gpu_gb: float
    ram_gb: float
    gpu_estimated: bool
    ram_estimated: bool


@dataclass(frozen=True, slots=True)
class Fit:
    """The verdict of one :class:`Need` against one budget.

    ``reason`` is the sentence the dashboard and the 422 show; it is ``None`` exactly when
    the verdict is ``"fits"``. ``render_free_gb`` and ``ram_free_gb`` are the two figures the
    need was judged against.
    """

    verdict: Verdict
    reason: str | None
    need: Need
    render_free_gb: float
    ram_free_gb: float

    def as_dict(self) -> dict[str, Any]:
        """JSON-native form: the field names, with ``need`` nested."""
        return dataclasses.asdict(self)


def _dims(size: str) -> tuple[int, int]:
    """``"WxH"`` as ``(width, height)``; ``ValueError`` for anything else."""
    match = _SIZE.fullmatch(size)
    if match is None:
        raise ValueError(f"not a WxH size: {size!r}")
    return int(match[1]), int(match[2])


def _gpu_need(model: ImageModel, size: str | None) -> tuple[float, bool]:
    """``(gb, estimated)`` for a render of ``model`` at ``size``.

    In order: the exact measurement; the smallest measured size with at least the requested
    pixel count (estimated); the largest measurement scaled by the ratio of pixel counts
    (estimated); the weight total, when nothing was measured (estimated). A model with
    measurements but no native size reads the weight total for ``size=None``: there is no
    size to look up.
    """
    peaks = model.gpu_peak_gb
    if size is None and model.sizes:
        size = model.sizes[0]
    if size is None or not peaks:
        return weights_gib(model), True
    width, height = _dims(size)
    exact = peaks.get(f"{width}x{height}")
    if exact is not None:
        return exact, False
    area = width * height
    measured: list[tuple[int, float]] = []
    for key, gb in peaks.items():
        key_width, key_height = _dims(key)
        measured.append((key_width * key_height, gb))
    measured.sort()
    for measured_area, gb in measured:
        if measured_area >= area:
            return gb, True
    largest_area, largest_gb = measured[-1]
    return round(largest_gb * area / largest_area, 1), True


def _ram_need(model: ImageModel) -> tuple[float, bool]:
    """``(gb, estimated)``: the load's measured floor or the weight total, whichever is more."""
    weights = weights_gib(model)
    if model.load_ram_gb is None:
        return weights, True
    return max(model.load_ram_gb, weights), False


def need_for(model: ImageModel, size: str | None) -> Need:
    """What rendering ``model`` at ``size`` (``"WxH"``) needs; ``None`` is its native size.

    The native size is ``model.sizes[0]``. A size the caller has not validated raises
    ``ValueError``. The size is read as the render will use it: the caller passes the
    effective size, after any clamping its own render path does.
    """
    gpu_gb, gpu_estimated = _gpu_need(model, size)
    ram_gb, ram_estimated = _ram_need(model)
    return Need(
        gpu_gb=gpu_gb, ram_gb=ram_gb, gpu_estimated=gpu_estimated, ram_estimated=ram_estimated
    )


def _judge(need_gb: float, free_gb: float) -> Verdict:
    if need_gb > free_gb:
        return "wont_fit"
    if need_gb > TIGHT_SHARE * free_gb:
        return "tight"
    return "fits"


def _gpu_reason(need_gb: float, render_free_gb: float) -> str:
    return f"needs {need_gb:.1f} GB of GPU memory, {render_free_gb:.1f} GB free for a render"


def _ram_reason(need_gb: float, ram: RamBudget) -> str:
    limit = "container limit" if ram.source == "container" else "system"
    return (
        f"needs ~{need_gb:.1f} GB of RAM to load, {ram.free_gb:.1f} GB free "
        f"({ram.limit_gb:.1f} GB {limit})"
    )


def assess(need: Need, budget: MemoryBudget) -> Fit | None:
    """Judge ``need`` against ``budget``; ``None`` when the budget cannot answer.

    Unknown is not "won't fit": ``None`` comes back when the box has no GPU pool reading
    (``budget.gpu`` or ``render_free_gb`` is ``None``) or when RAM could not be read
    (``ram.limit_gb`` is 0, the zeros an unreadable ``/proc/meminfo`` yields). Callers must
    not refuse a render on ``None``.

    The GPU is judged against ``render_free_gb`` (the pool a render can use once image mode
    has unloaded this box's slots) and RAM against ``ram.free_gb``. A need above the free
    figure is ``"wont_fit"``, one above 0.9 of it is ``"tight"``, otherwise ``"fits"``. The
    worse of the two decides, and ``reason`` is the sentence for that one (the GPU's when the
    two are equal). A tight verdict carries its sentence too, so the UI can say why.
    """
    render_free_gb = budget.render_free_gb
    ram = budget.ram
    if budget.gpu is None or render_free_gb is None or ram.limit_gb == 0:
        return None

    gpu_verdict = _judge(need.gpu_gb, render_free_gb)
    ram_verdict = _judge(need.ram_gb, ram.free_gb)
    if _SEVERITY[ram_verdict] > _SEVERITY[gpu_verdict]:
        verdict, reason = ram_verdict, _ram_reason(need.ram_gb, ram)
    else:
        verdict, reason = gpu_verdict, _gpu_reason(need.gpu_gb, render_free_gb)
    return Fit(
        verdict=verdict,
        reason=None if verdict == "fits" else reason,
        need=need,
        render_free_gb=render_free_gb,
        ram_free_gb=ram.free_gb,
    )
