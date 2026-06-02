"""Regression tests for HardwareStats GPU probing (FIX-A).

The key invariant: on an AMD box (DRM sysfs present) the GPU metric
methods read sysfs directly and make ZERO nvidia-smi subprocess calls.
Previously each of gpu_util()/gpu_vram_used_mb()/gpu_vram_total_mb()
shelled out to nvidia-smi first on every read, producing a per-read
execve storm on AMD hosts where nvidia-smi is absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.hardware import stats as stats_mod
from hal0.hardware.stats import HardwareStats


def _mk_amd_drm(tmp_path: Path) -> Path:
    """Create a fake AMD DRM device dir with the sysfs counters we read."""
    drm = tmp_path / "drm" / "card1" / "device"
    drm.mkdir(parents=True)
    (drm / "gpu_busy_percent").write_text("42\n")
    # _read_sysfs_mb interprets these as bytes; values chosen so MiB are
    # round-ish. 1 MiB = 1048576 bytes.
    (drm / "mem_info_vram_used").write_text(str(100 * 1024 * 1024))
    (drm / "mem_info_gtt_used").write_text(str(2048 * 1024 * 1024))
    (drm / "mem_info_vram_total").write_text(str(512 * 1024 * 1024))
    (drm / "mem_info_gtt_total").write_text(str(65536 * 1024 * 1024))
    return drm


class _RunSpy:
    """Records every _run() invocation so tests can assert nvidia-smi
    was never (or was) execed."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], timeout: float = 4.0) -> tuple[int, str, str]:
        self.calls.append(list(cmd))
        # If anything reaches here on the AMD path, the test should fail —
        # but return a benign "nvidia absent" result so we don't mask the
        # assertion with an exception.
        return (1, "", "not found")

    @property
    def nvidia_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c and c[0] == "nvidia-smi"]


def test_amd_gpu_metrics_make_zero_nvidia_smi_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """On an AMD DRM host, snapshot() and the GPU methods never exec
    nvidia-smi — they read sysfs directly via the cached drm device."""
    drm = _mk_amd_drm(tmp_path)
    spy = _RunSpy()
    monkeypatch.setattr(stats_mod, "_amd_drm_device", lambda: drm)
    monkeypatch.setattr(stats_mod, "_run", spy)

    s = HardwareStats()
    assert s._vendor() == "amd"

    # Repeated reads must do zero subprocess work.
    for _ in range(20):
        s.gpu_util()
        s.gpu_vram_used_mb()
        s.gpu_vram_total_mb()
        s.snapshot()

    assert spy.nvidia_calls == [], (
        f"nvidia-smi was execed on the AMD path; expected zero calls, got {spy.nvidia_calls}"
    )


def test_amd_gpu_values_from_sysfs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The AMD path returns sysfs-derived values with the expected shape."""
    drm = _mk_amd_drm(tmp_path)
    spy = _RunSpy()
    monkeypatch.setattr(stats_mod, "_amd_drm_device", lambda: drm)
    monkeypatch.setattr(stats_mod, "_run", spy)

    s = HardwareStats()
    assert s.gpu_util() == pytest.approx(0.42, abs=1e-3)
    # max(vram_used=100, gtt_used=2048) MiB -> gtt wins (Strix Halo UMA)
    assert s.gpu_vram_used_mb() == pytest.approx(2048.0, rel=1e-3)
    # max(vram_total=512, gtt_total=65536) -> gtt wins
    assert s.gpu_vram_total_mb() == pytest.approx(65536.0, rel=1e-3)
    assert spy.nvidia_calls == []


def test_vendor_cached_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Vendor detection probes _amd_drm_device exactly once and memoises."""
    drm = _mk_amd_drm(tmp_path)
    probe_calls = {"n": 0}

    def fake_drm() -> Path:
        probe_calls["n"] += 1
        return drm

    monkeypatch.setattr(stats_mod, "_amd_drm_device", fake_drm)
    monkeypatch.setattr(stats_mod, "_run", _RunSpy())

    s = HardwareStats()
    for _ in range(10):
        s._vendor()
        s.gpu_util()
    assert probe_calls["n"] == 1


def test_nvidia_path_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """On a host with no AMD DRM device, nvidia-smi is used and the
    nvidia code path still parses output correctly; vendor caches to
    'nvidia'."""

    def fake_run(cmd: list[str], timeout: float = 4.0) -> tuple[int, str, str]:
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

    monkeypatch.setattr(stats_mod, "_amd_drm_device", lambda: None)
    monkeypatch.setattr(stats_mod, "_run", fake_run)

    s = HardwareStats()
    assert s._vendor() == "nvidia"
    assert s.gpu_util() == pytest.approx(0.55, abs=1e-3)
    assert s.gpu_vram_used_mb() == pytest.approx(8192.0, rel=1e-3)
    assert s.gpu_vram_total_mb() == pytest.approx(16376.0, rel=1e-3)


def test_unknown_vendor_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """No AMD DRM and no working nvidia-smi -> vendor 'unknown', metrics None."""
    monkeypatch.setattr(stats_mod, "_amd_drm_device", lambda: None)
    monkeypatch.setattr(stats_mod, "_run", lambda cmd, timeout=4.0: (1, "", "no"))

    s = HardwareStats()
    assert s._vendor() == "unknown"
    assert s.gpu_util() is None
    assert s.gpu_vram_used_mb() is None
    assert s.gpu_vram_total_mb() is None
