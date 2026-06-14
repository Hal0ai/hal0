"""Tests for npu_util field added to /api/stats/hardware.

Covers:
- Case1: first call returns None (no prev sample, need 2 reads).
- Case2: two valid reads -> correct delta fraction (0.1).
- Case3: missing sysfs files -> None.
- Case4: counter reset (active decreases) -> None, cache reset.
- Endpoint: npu_util present when valid delta available.
- Endpoint: npu_util absent when helper returns None.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import hal0.api.routes.hardware as hw_mod

# ── fixture helpers (mirrors test_cpu_util.py pattern) ───────────────────────


class _MinimalStatsStub:
    """HardwareStats stand-in returning a minimal snapshot (no GPU fields)."""

    def snapshot(self) -> dict:
        return {"ram_used_gb": 2.0}

    def gpu_sample(self) -> None:
        return None


def _wire_stats(client: TestClient) -> None:
    """Attach the minimal stats stub to the app so _local_live_stats runs."""
    client.app.state.hardware_stats = _MinimalStatsStub()


# ── helper: _npu_residency_util unit tests ────────────────────────────────────


class TestNpuResidencyUtil:
    """Direct tests of the sync helper — no HTTP layer."""

    def _write_counters(self, root, active: int, suspended: int) -> None:
        (root / "runtime_active_time").write_text(str(active))
        (root / "runtime_suspended_time").write_text(str(suspended))

    def test_first_call_returns_none(
        self, tmp_path: pytest.fixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Case1: first call caches the sample and returns None (need 2 reads)."""
        self._write_counters(tmp_path, active=1000, suspended=0)
        monkeypatch.setattr(hw_mod, "_NPU_PM_ROOT", tmp_path)
        monkeypatch.setattr(hw_mod, "_npu_pm_prev", None)

        result = hw_mod._npu_residency_util()
        assert result is None
        # Cache should now hold the first sample.
        assert hw_mod._npu_pm_prev == (1000, 0)

    def test_second_call_returns_fraction(
        self, tmp_path: pytest.fixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Case2: da=100, ds=900, denom=1000 -> 0.1."""
        monkeypatch.setattr(hw_mod, "_NPU_PM_ROOT", tmp_path)
        monkeypatch.setattr(hw_mod, "_npu_pm_prev", None)

        # First call — prime the cache.
        self._write_counters(tmp_path, active=1000, suspended=0)
        r1 = hw_mod._npu_residency_util()
        assert r1 is None

        # Second call — valid delta.
        self._write_counters(tmp_path, active=1100, suspended=900)
        r2 = hw_mod._npu_residency_util()
        assert r2 == pytest.approx(0.1)

    def test_missing_files_returns_none(
        self, tmp_path: pytest.fixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Case3: sysfs files absent -> None, no exception raised."""
        monkeypatch.setattr(hw_mod, "_NPU_PM_ROOT", tmp_path)
        monkeypatch.setattr(hw_mod, "_npu_pm_prev", None)

        # tmp_path is empty — no counter files.
        result = hw_mod._npu_residency_util()
        assert result is None

    def test_counter_reset_returns_none_and_resets_cache(
        self, tmp_path: pytest.fixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Case4: active decreases (da<0) -> None, cache reset to current."""
        monkeypatch.setattr(hw_mod, "_NPU_PM_ROOT", tmp_path)
        monkeypatch.setattr(hw_mod, "_npu_pm_prev", None)

        # Prime cache with high value.
        self._write_counters(tmp_path, active=5000, suspended=1000)
        hw_mod._npu_residency_util()  # first call -> None, caches (5000, 1000)

        # Simulate counter reset: active dropped below prev.
        self._write_counters(tmp_path, active=10, suspended=0)
        result = hw_mod._npu_residency_util()
        assert result is None
        # Cache should be reset to the post-reset sample.
        assert hw_mod._npu_pm_prev == (10, 0)

    def test_zero_denom_returns_none(
        self, tmp_path: pytest.fixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When active+suspended don't change between reads, denom=0 -> None."""
        monkeypatch.setattr(hw_mod, "_NPU_PM_ROOT", tmp_path)
        monkeypatch.setattr(hw_mod, "_npu_pm_prev", None)

        self._write_counters(tmp_path, active=500, suspended=500)
        hw_mod._npu_residency_util()  # prime

        # Same values again.
        self._write_counters(tmp_path, active=500, suspended=500)
        result = hw_mod._npu_residency_util()
        assert result is None


# ── endpoint integration tests ────────────────────────────────────────────────


class TestNpuUtilEndpoint:
    def _write_counters(self, root, active: int, suspended: int) -> None:
        (root / "runtime_active_time").write_text(str(active))
        (root / "runtime_suspended_time").write_text(str(suspended))

    def test_npu_util_present_on_valid_delta(
        self,
        tmp_path: pytest.fixture,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When two reads have elapsed, npu_util appears in the response."""
        _wire_stats(client)
        monkeypatch.setattr(hw_mod, "_NPU_PM_ROOT", tmp_path)
        monkeypatch.setattr(hw_mod, "_npu_pm_prev", None)

        # Pre-seed the cache with a first sample (skips the None-first-call path).
        self._write_counters(tmp_path, active=2000, suspended=0)
        hw_mod._npu_residency_util()  # prime cache directly

        # Now point counters at the second sample.
        self._write_counters(tmp_path, active=2200, suspended=800)

        resp = client.get("/api/stats/hardware")
        assert resp.status_code == 200
        data = resp.json()
        assert "npu_util" in data
        assert data["npu_util"] == pytest.approx(0.2)  # da=200, ds=800 -> 0.2

    def test_npu_util_absent_when_no_prev_sample(
        self,
        tmp_path: pytest.fixture,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """First call returns None -> npu_util key absent from response."""
        _wire_stats(client)
        monkeypatch.setattr(hw_mod, "_NPU_PM_ROOT", tmp_path)
        monkeypatch.setattr(hw_mod, "_npu_pm_prev", None)

        self._write_counters(tmp_path, active=1000, suspended=0)

        resp = client.get("/api/stats/hardware")
        assert resp.status_code == 200
        assert "npu_util" not in resp.json()

    def test_npu_util_absent_when_files_missing(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pytest.fixture,
    ) -> None:
        """Missing sysfs files -> helper returns None -> key absent, 200 OK."""
        _wire_stats(client)
        monkeypatch.setattr(hw_mod, "_NPU_PM_ROOT", tmp_path)
        monkeypatch.setattr(hw_mod, "_npu_pm_prev", None)

        # tmp_path is empty.
        resp = client.get("/api/stats/hardware")
        assert resp.status_code == 200
        assert "npu_util" not in resp.json()
