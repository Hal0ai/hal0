"""HardwareStats → gpu_view delegation (issue #703).

tests/hardware/test_stats.py is the parity oracle and stays UNMODIFIED;
this file covers only what's NEW: the gpu_sample() seam and the typed
split/flag fields snapshot() now carries for the API route.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.hardware import stats as stats_mod
from hal0.hardware.gpu_view import GPUMemorySample
from hal0.hardware.stats import HardwareStats

MIB = 1024 * 1024


def _mk_amd_drm(tmp_path: Path, *, perf_level: str | None = "high\n") -> Path:
    drm = tmp_path / "drm" / "card1" / "device"
    drm.mkdir(parents=True)
    (drm / "gpu_busy_percent").write_text("100\n")
    (drm / "mem_info_vram_used").write_text(str(100 * MIB))
    (drm / "mem_info_gtt_used").write_text(str(2048 * MIB))
    (drm / "mem_info_vram_total").write_text(str(512 * MIB))
    (drm / "mem_info_gtt_total").write_text(str(81920 * MIB))
    if perf_level is not None:
        (drm / "power_dpm_force_performance_level").write_text(perf_level)
    return drm


def test_gpu_sample_uses_cached_vendor_and_drm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """gpu_sample() reuses the memoised vendor/drm — drm detection still
    happens exactly once (the test_stats.py caching invariant extends to
    the new seam)."""
    drm = _mk_amd_drm(tmp_path)
    probe_calls = {"n": 0}

    def fake_drm() -> Path:
        probe_calls["n"] += 1
        return drm

    monkeypatch.setattr(stats_mod, "_amd_drm_device", fake_drm)

    s = HardwareStats()
    for _ in range(5):
        smp = s.gpu_sample()
        assert isinstance(smp, GPUMemorySample)
        assert smp.vendor == "amd"
    assert probe_calls["n"] == 1


def test_gpu_sample_amd_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    drm = _mk_amd_drm(tmp_path)
    monkeypatch.setattr(stats_mod, "_amd_drm_device", lambda: drm)

    smp = HardwareStats().gpu_sample()
    assert smp.is_uma is True
    assert smp.gtt_used_mb == pytest.approx(2048.0)
    assert smp.vram_used_mb == pytest.approx(100.0)
    assert smp.used_mb == pytest.approx(2048.0)
    assert smp.total_mb == pytest.approx(81920.0)
    assert smp.util_is_forced_high is True
    assert smp.gpu_busy == pytest.approx(1.0)  # raw — never rewritten


def test_snapshot_carries_split_and_forced_high_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """snapshot() exposes the typed split + flag so the API route reads
    them off the SWR cache instead of re-reading sysfs per request."""
    drm = _mk_amd_drm(tmp_path)
    monkeypatch.setattr(stats_mod, "_amd_drm_device", lambda: drm)

    snap = HardwareStats().snapshot()
    # Pre-#703 keys unchanged (max-pool semantics).
    assert snap["gpu_util"] == pytest.approx(1.0)
    assert snap["gpu_vram_used_mb"] == pytest.approx(2048.0)
    assert snap["gpu_vram_total_mb"] == pytest.approx(81920.0)
    # New typed fields.
    assert snap["gtt_used_mb"] == pytest.approx(2048.0)
    assert snap["vram_used_mb"] == pytest.approx(100.0)
    assert snap["util_is_forced_high"] is True


def test_snapshot_flag_false_without_forced_high(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    drm = _mk_amd_drm(tmp_path, perf_level="auto\n")
    monkeypatch.setattr(stats_mod, "_amd_drm_device", lambda: drm)
    snap = HardwareStats().snapshot()
    assert snap["util_is_forced_high"] is False


def test_snapshot_no_gpu_box(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stats_mod, "_amd_drm_device", lambda: None)
    monkeypatch.setattr(stats_mod, "_run", lambda cmd, timeout=4.0: (1, "", "no"))
    snap = HardwareStats().snapshot()
    assert snap["gpu_util"] is None
    assert snap["gtt_used_mb"] is None
    assert snap["vram_used_mb"] is None
    assert snap["util_is_forced_high"] is False
