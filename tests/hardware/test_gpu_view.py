"""Tests for hal0.hardware.gpu_view — the GPUMemorySample view (issue #703).

The view owns the live GPU memory + utilization surface:
  - VRAM/GTT pool split (typed fields, no route-side sysfs re-reads)
  - max-pool semantics for used_mb/total_mb (parity with HardwareStats)
  - is_uma physical carve-out signature (single home for the heuristic)
  - util_is_forced_high factual read of power_dpm_force_performance_level
  - NVIDIA path preserved behind the same fields

No GPU exists on the dev box — every test drives a fake sysfs tree under
tmp_path (same pattern as tests/hardware/test_stats.py).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from hal0.hardware import gpu_view
from hal0.hardware.gpu_view import GPUMemorySample, sample

MIB = 1024 * 1024


def _mk_drm(
    tmp_path: Path,
    *,
    busy: str | None = "42\n",
    vram_used_mb: int | None = 100,
    gtt_used_mb: int | None = 2048,
    vram_total_mb: int | None = 512,
    gtt_total_mb: int | None = 81920,
    perf_level: str | None = None,
) -> Path:
    """Create a fake AMD DRM device dir. None for a field = file absent."""
    drm = tmp_path / "drm" / "card1" / "device"
    drm.mkdir(parents=True)
    if busy is not None:
        (drm / "gpu_busy_percent").write_text(busy)
    for name, mb in (
        ("mem_info_vram_used", vram_used_mb),
        ("mem_info_gtt_used", gtt_used_mb),
        ("mem_info_vram_total", vram_total_mb),
        ("mem_info_gtt_total", gtt_total_mb),
    ):
        if mb is not None:
            (drm / name).write_text(str(mb * MIB))
    if perf_level is not None:
        (drm / "power_dpm_force_performance_level").write_text(perf_level)
    return drm


def _no_nvidia(cmd: list[str], timeout: float = 4.0) -> tuple[int, str, str]:
    return (1, "", "not found")


# ── AMD pool split + max-pool parity ─────────────────────────────────────────


def test_amd_pool_split_fields(tmp_path: Path) -> None:
    """The sample carries the typed VRAM/GTT split — no re-derivation."""
    drm = _mk_drm(tmp_path)
    s = sample(vendor="amd", drm=drm)
    assert isinstance(s, GPUMemorySample)
    assert s.vendor == "amd"
    assert s.vram_used_mb == pytest.approx(100.0)
    assert s.gtt_used_mb == pytest.approx(2048.0)
    assert s.vram_total_mb == pytest.approx(512.0)
    assert s.gtt_total_mb == pytest.approx(81920.0)
    assert s.gpu_busy == pytest.approx(0.42, abs=1e-3)


@pytest.mark.parametrize(
    ("vram_used", "gtt_used", "vram_total", "gtt_total", "want_used", "want_total"),
    [
        # gtt wins both pools (Strix Halo shape)
        (100, 2048, 512, 81920, 2048.0, 81920.0),
        # vram wins both pools (discrete shape with a gtt counter)
        (8192, 256, 24576, 1024, 8192.0, 24576.0),
        # one candidate missing → the other wins (None handling)
        (None, 2048, None, 81920, 2048.0, 81920.0),
        (100, None, 512, None, 100.0, 512.0),
        # both missing → None
        (None, None, None, None, None, None),
    ],
)
def test_amd_max_pool_parity(
    tmp_path: Path,
    vram_used: int | None,
    gtt_used: int | None,
    vram_total: int | None,
    gtt_total: int | None,
    want_used: float | None,
    want_total: float | None,
) -> None:
    """used_mb/total_mb keep HardwareStats' exact max-pool semantics."""
    drm = _mk_drm(
        tmp_path,
        vram_used_mb=vram_used,
        gtt_used_mb=gtt_used,
        vram_total_mb=vram_total,
        gtt_total_mb=gtt_total,
    )
    s = sample(vendor="amd", drm=drm)
    if want_used is None:
        assert s.used_mb is None
    else:
        assert s.used_mb == pytest.approx(want_used)
    if want_total is None:
        assert s.total_mb is None
    else:
        assert s.total_mb == pytest.approx(want_total)


# ── is_uma — physical carve-out signature, both shapes ───────────────────────


def test_is_uma_strix_halo_shape(tmp_path: Path) -> None:
    """Strix Halo: tiny VRAM carve-out (~512MB) + huge GTT → UMA."""
    drm = _mk_drm(tmp_path, vram_total_mb=512, gtt_total_mb=81920)
    s = sample(vendor="amd", drm=drm)
    assert s.is_uma is True
    # Parity with the deleted route heuristic (vram_mb > ram_mb * 0.5):
    # the probe's pooled vram_mb is max(vram, gtt) = 81920; against the
    # CT105 ram_mb of ~96GB the old heuristic also said UMA.
    pooled_vram_mb = s.total_mb
    ram_mb = 96 * 1024
    assert pooled_vram_mb is not None
    assert (pooled_vram_mb > ram_mb * 0.5) is True


@pytest.mark.parametrize(
    ("vram_total", "gtt_total"),
    [
        (24576, 0),  # discrete: big VRAM, zero GTT pool
        (24576, None),  # discrete: GTT counter absent
    ],
)
def test_is_uma_discrete_amd_shape(
    tmp_path: Path, vram_total: int | None, gtt_total: int | None
) -> None:
    """Discrete AMD: big dedicated VRAM, no GTT pool → not UMA."""
    drm = _mk_drm(tmp_path, vram_total_mb=vram_total, gtt_total_mb=gtt_total)
    s = sample(vendor="amd", drm=drm)
    assert s.is_uma is False
    # Parity with the deleted route heuristic on a typical 64GB-RAM
    # workstation: 24576 > 65536 * 0.5 is False — both agree.
    assert s.total_mb == pytest.approx(24576.0)
    assert (s.total_mb > 64 * 1024 * 0.5) is False


def test_is_uma_requires_amd_vendor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu_view, "_amd_drm_device", lambda: None)
    monkeypatch.setattr(gpu_view, "_run", _no_nvidia)
    assert sample().is_uma is False


# ── util_is_forced_high — factual perf-level read ────────────────────────────


@pytest.mark.parametrize(
    ("perf_level", "want"),
    [
        ("high\n", True),
        ("high", True),
        ("auto\n", False),
        ("manual\n", False),
        (None, False),  # file missing (e.g. perms, virtual GPU)
    ],
)
def test_util_is_forced_high(tmp_path: Path, perf_level: str | None, want: bool) -> None:
    drm = _mk_drm(tmp_path, perf_level=perf_level)
    s = sample(vendor="amd", drm=drm)
    assert s.util_is_forced_high is want


def test_gpu_busy_stays_raw_when_forced_high(tmp_path: Path) -> None:
    """The flag never rewrites gpu_busy — consumers decide what to trust."""
    drm = _mk_drm(tmp_path, busy="100\n", perf_level="high\n")
    s = sample(vendor="amd", drm=drm)
    assert s.util_is_forced_high is True
    assert s.gpu_busy == pytest.approx(1.0)


# ── NVIDIA path ──────────────────────────────────────────────────────────────


def _fake_nvidia_run(cmd: list[str], timeout: float = 4.0) -> tuple[int, str, str]:
    if not cmd or cmd[0] != "nvidia-smi":
        return (1, "", "")
    query = next((a for a in cmd if a.startswith("--query-gpu=")), "")
    if "name" in query:
        return (0, "GeForce RTX 4080\n", "")
    if "utilization.gpu" in query:
        return (0, "55\n", "")
    if "memory.used" in query:
        return (0, "8192\n", "")
    if "memory.total" in query:
        return (0, "16376\n", "")
    return (1, "", "")


def test_nvidia_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """NVIDIA fields map onto the same sample shape; no GTT, never UMA."""
    monkeypatch.setattr(gpu_view, "_amd_drm_device", lambda: None)
    monkeypatch.setattr(gpu_view, "_run", _fake_nvidia_run)
    s = sample()
    assert s.vendor == "nvidia"
    assert s.is_uma is False
    assert s.util_is_forced_high is False
    assert s.gtt_total_mb is None
    assert s.gtt_used_mb is None
    assert s.vram_used_mb == pytest.approx(8192.0)
    assert s.vram_total_mb == pytest.approx(16376.0)
    assert s.used_mb == pytest.approx(8192.0)
    assert s.total_mb == pytest.approx(16376.0)
    assert s.gpu_busy == pytest.approx(0.55, abs=1e-3)


def test_nvidia_vendor_passed_uses_injected_run() -> None:
    """When the caller (HardwareStats) supplies vendor + run, the view uses
    them — this is the seam that keeps stats_mod monkeypatching effective."""
    s = sample(vendor="nvidia", run=_fake_nvidia_run)
    assert s.gpu_busy == pytest.approx(0.55, abs=1e-3)
    assert s.used_mb == pytest.approx(8192.0)


# ── no GPU at all ────────────────────────────────────────────────────────────


def test_no_gpu_box_all_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu_view, "_amd_drm_device", lambda: None)
    monkeypatch.setattr(gpu_view, "_run", _no_nvidia)
    s = sample()
    assert s.vendor == "unknown"
    assert s.is_uma is False
    assert s.util_is_forced_high is False
    assert s.vram_total_mb is None
    assert s.gtt_total_mb is None
    assert s.total_mb is None
    assert s.vram_used_mb is None
    assert s.gtt_used_mb is None
    assert s.used_mb is None
    assert s.gpu_busy is None


def test_amd_vendor_without_drm_degrades_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """vendor='amd' with no resolvable DRM dir → all-None sample, no raise."""
    monkeypatch.setattr(gpu_view, "_amd_drm_device", lambda: None)
    s = sample(vendor="amd")
    assert s.vendor == "amd"
    assert s.used_mb is None
    assert s.total_mb is None
    assert s.is_uma is False
    assert s.util_is_forced_high is False


def test_sample_is_frozen(tmp_path: Path) -> None:
    drm = _mk_drm(tmp_path)
    s = sample(vendor="amd", drm=drm)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.vendor = "nvidia"  # type: ignore[misc]
