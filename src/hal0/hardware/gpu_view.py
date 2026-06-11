"""GPU memory view — one typed sample owning the live GPU memory surface.

Issue #703: before this module existed, the live GPU numbers were derived
in three places with private cross-imports:

  - ``hardware/stats.py`` max-pooled VRAM/GTT via probe's private helpers,
  - ``api/routes/hardware.py`` re-imported the SAME private helpers to
    un-do the pooling for the dashboard's GTT/VRAM split,
  - ``is_uma`` was derived twice (probe detect-time max-pool + the route's
    per-request ``vram > ram*0.5`` heuristic),

and nothing interpreted the forced-high artifact (gpu-compute.service pins
``power_dpm_force_performance_level`` to ``high`` → ``gpu_busy_percent``
reads flat 100 regardless of real load).

``sample()`` returns a frozen :class:`GPUMemorySample` carrying the pool
split, the max-pooled aggregates (exact ``HardwareStats`` semantics), the
single-home ``is_uma`` signature, and the factual ``util_is_forced_high``
flag. ``probe.py`` REMAINS the one-time detection owner — this view only
uses its low-level readers as internals.

Scope note (locked design): ``slots/capacity.py`` and ``routes/comfyui.py``
keep their own accounting for now; they are optional future consumers of
``total_mb``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from hal0.hardware.probe import _amd_drm_device, _read_sysfs_mb, _run

# is_uma physical carve-out signature: on UMA parts (Strix Halo et al.) the
# dedicated-VRAM counter reports a small BAR carve-out — well under 2 GiB —
# while the real model-loading pool is GTT. A discrete GPU reports the
# opposite shape (multi-GiB VRAM, zero/absent GTT pool).
_UMA_VRAM_CARVEOUT_MAX_MB = 2048.0


class _Runner(Protocol):
    def __call__(self, cmd: list[str], timeout: float = 4.0) -> tuple[int, str, str]: ...


@dataclass(frozen=True)
class GPUMemorySample:
    """A point-in-time read of the GPU memory + utilization counters.

    ``gpu_busy`` is reported RAW even when ``util_is_forced_high`` is set —
    the flag tells consumers not to trust it; nothing rewrites the value.
    """

    vendor: str  # "amd" | "nvidia" | "unknown"
    is_uma: bool
    vram_total_mb: float | None
    gtt_total_mb: float | None
    total_mb: float | None  # max-pool (HardwareStats semantics)
    vram_used_mb: float | None
    gtt_used_mb: float | None
    used_mb: float | None  # max-pool
    gpu_busy: float | None  # 0..1, raw
    util_is_forced_high: bool


def _max_pool(*candidates: float | None) -> float | None:
    """max() over the non-None candidates; None when all are missing.

    This is the exact pooling HardwareStats.gpu_vram_{used,total}_mb()
    applied: on UMA the GTT counter wins, on discrete the VRAM counter
    wins, and "no counter" stays distinguishable from "actually zero".
    """
    present = [c for c in candidates if c is not None]
    return max(present) if present else None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def _parse_pct(txt: str | None) -> float | None:
    if txt is None:
        return None
    try:
        return round(float(txt.strip()) / 100.0, 3)
    except ValueError:
        return None


def _nvidia_query_mb(run: _Runner, field: str) -> float | None:
    """Query one nvidia-smi numeric field; None on any failure.

    Issued as a SEPARATE exec per field — deliberately mirroring the
    pre-#703 HardwareStats queries so behaviour (and test fakes keyed on
    the query string) stay identical.
    """
    rc, out, _ = run(
        [
            "nvidia-smi",
            f"--query-gpu={field}",
            "--format=csv,noheader,nounits",
        ]
    )
    if rc == 0 and out.strip():
        try:
            return round(float(out.strip().splitlines()[0]), 1)
        except ValueError:
            return None
    return None


def _empty(vendor: str) -> GPUMemorySample:
    return GPUMemorySample(
        vendor=vendor,
        is_uma=False,
        vram_total_mb=None,
        gtt_total_mb=None,
        total_mb=None,
        vram_used_mb=None,
        gtt_used_mb=None,
        used_mb=None,
        gpu_busy=None,
        util_is_forced_high=False,
    )


def _sample_amd(drm: Path) -> GPUMemorySample:
    vram_total = _read_sysfs_mb(drm / "mem_info_vram_total")
    gtt_total = _read_sysfs_mb(drm / "mem_info_gtt_total")
    vram_used = _read_sysfs_mb(drm / "mem_info_vram_used")
    gtt_used = _read_sysfs_mb(drm / "mem_info_gtt_used")

    # Physical carve-out signature (single home for the UMA heuristic):
    # a real GTT pool plus a sub-2GiB VRAM carve-out is UMA; a discrete
    # card reports multi-GiB VRAM and zero/absent GTT.
    is_uma = (gtt_total or 0) > 0 and (vram_total or 0) < _UMA_VRAM_CARVEOUT_MAX_MB

    # Factual read only — gpu_busy stays raw; the flag tells consumers the
    # utilization counter is pinned by a forced performance level.
    perf = _read_text(drm / "power_dpm_force_performance_level")
    util_is_forced_high = perf is not None and perf.strip() == "high"

    return GPUMemorySample(
        vendor="amd",
        is_uma=is_uma,
        vram_total_mb=vram_total,
        gtt_total_mb=gtt_total,
        total_mb=_max_pool(vram_total, gtt_total),
        vram_used_mb=vram_used,
        gtt_used_mb=gtt_used,
        used_mb=_max_pool(vram_used, gtt_used),
        gpu_busy=_parse_pct(_read_text(drm / "gpu_busy_percent")),
        util_is_forced_high=util_is_forced_high,
    )


def _sample_nvidia(run: _Runner) -> GPUMemorySample:
    util_pct = _nvidia_query_mb(run, "utilization.gpu")
    used = _nvidia_query_mb(run, "memory.used")
    total = _nvidia_query_mb(run, "memory.total")
    return GPUMemorySample(
        vendor="nvidia",
        is_uma=False,
        vram_total_mb=total,
        gtt_total_mb=None,
        total_mb=_max_pool(total),
        vram_used_mb=used,
        gtt_used_mb=None,
        used_mb=_max_pool(used),
        gpu_busy=round(util_pct / 100.0, 3) if util_pct is not None else None,
        util_is_forced_high=False,
    )


def sample(
    *,
    vendor: str | None = None,
    drm: Path | None = None,
    run: _Runner | None = None,
) -> GPUMemorySample:
    """Take a point-in-time GPU memory + utilization sample.

    With no arguments, detects the vendor itself (AMD DRM sysfs first,
    nvidia-smi probe second — same order as ``HardwareStats._vendor``).
    Callers that already cached detection (``HardwareStats``) pass
    ``vendor``/``drm``/``run`` so detection isn't repeated and their
    test seams (module-level ``_run``/``_amd_drm_device``) stay
    effective. Never raises; missing counters degrade to ``None``.
    """
    runner: _Runner = run if run is not None else _run

    if vendor is None:
        drm = drm if drm is not None else _amd_drm_device()
        if drm is not None:
            vendor = "amd"
        else:
            rc, out, _ = runner(
                [
                    "nvidia-smi",
                    "--query-gpu=name",
                    "--format=csv,noheader,nounits",
                ]
            )
            vendor = "nvidia" if (rc == 0 and out.strip()) else "unknown"

    if vendor == "amd":
        if drm is None:
            drm = _amd_drm_device()
        if drm is None:
            return _empty("amd")
        return _sample_amd(drm)
    if vendor == "nvidia":
        return _sample_nvidia(runner)
    return _empty(vendor)


__all__ = ["GPUMemorySample", "sample"]
