"""Tests for npu_util field added to /api/stats/hardware.

Covers:
- Case1: first call returns None (no prev sample, need 2 reads).
- Case2: two valid reads -> correct delta fraction (0.1).
- Case3: missing sysfs files -> None.
- Case4: counter reset (active decreases) -> None, baseline restarts.
- Two meters (route, sampler) keep independent intervals.
- Endpoint: npu_util present when valid delta available.
- Endpoint: npu_util absent when helper returns None.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import hal0.api.routes.hardware as hw_mod
from hal0.hardware.npu_residency import NpuResidencyMeter

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


# ── NpuResidencyMeter unit tests ─────────────────────────────────────────────


def _write_counters(root, active: int, suspended: int) -> None:
    (root / "runtime_active_time").write_text(str(active))
    (root / "runtime_suspended_time").write_text(str(suspended))


class TestNpuResidencyMeter:
    """Direct tests of the meter — no HTTP layer."""

    def test_first_call_returns_none(self, tmp_path) -> None:
        """Case1: the first read only sets the baseline (a delta needs two reads)."""
        _write_counters(tmp_path, active=1000, suspended=0)
        assert NpuResidencyMeter(tmp_path).read() is None

    def test_second_call_returns_fraction(self, tmp_path) -> None:
        """Case2: da=100, ds=900, denom=1000 -> 0.1."""
        meter = NpuResidencyMeter(tmp_path)
        _write_counters(tmp_path, active=1000, suspended=0)
        assert meter.read() is None
        _write_counters(tmp_path, active=1100, suspended=900)
        assert meter.read() == pytest.approx(0.1)

    def test_missing_files_returns_none(self, tmp_path) -> None:
        """Case3: sysfs files absent -> None, no exception raised."""
        assert NpuResidencyMeter(tmp_path).read() is None

    def test_counter_reset_returns_none_and_restarts_baseline(self, tmp_path) -> None:
        """Case4: active decreases (driver reload) -> None; the next read measures from the reset."""
        meter = NpuResidencyMeter(tmp_path)
        _write_counters(tmp_path, active=5000, suspended=1000)
        meter.read()
        _write_counters(tmp_path, active=10, suspended=0)
        assert meter.read() is None
        _write_counters(tmp_path, active=60, suspended=50)
        assert meter.read() == pytest.approx(0.5)

    def test_zero_denom_returns_none(self, tmp_path) -> None:
        """When active+suspended don't change between reads, denom=0 -> None."""
        meter = NpuResidencyMeter(tmp_path)
        _write_counters(tmp_path, active=500, suspended=500)
        meter.read()
        assert meter.read() is None

    def test_two_meters_keep_their_own_interval(self, tmp_path) -> None:
        """The route and the sampler each own a meter; one reading never shortens the other's delta."""
        route, sampler = NpuResidencyMeter(tmp_path), NpuResidencyMeter(tmp_path)
        _write_counters(tmp_path, active=0, suspended=0)
        route.read()
        sampler.read()
        _write_counters(tmp_path, active=100, suspended=100)
        assert route.read() == pytest.approx(0.5)
        _write_counters(tmp_path, active=500, suspended=100)
        # The sampler's delta spans both changes: 500 active of 600.
        assert sampler.read() == pytest.approx(500 / 600)


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
        monkeypatch.setattr(hw_mod, "_NPU_METER", NpuResidencyMeter(tmp_path))

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
        monkeypatch.setattr(hw_mod, "_NPU_METER", NpuResidencyMeter(tmp_path))

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
        monkeypatch.setattr(hw_mod, "_NPU_METER", NpuResidencyMeter(tmp_path))

        # tmp_path is empty.
        resp = client.get("/api/stats/hardware")
        assert resp.status_code == 200
        assert "npu_util" not in resp.json()


# ── GET /api/stats/activity ───────────────────────────────────────────────────


class TestStatsActivityEndpoint:
    def test_returns_buckets_for_the_window(self, client: TestClient, monkeypatch) -> None:
        captured: dict = {}

        def _fake(path, *, window):
            captured["window"] = window
            return {"window": window, "buckets": 60, "slots": {}, "fleet": {}}

        monkeypatch.setattr("hal0.metrics.read.slot_activity", _fake)
        resp = client.get("/api/stats/activity?window=5m")
        assert resp.status_code == 200
        assert resp.json()["window"] == "5m"
        assert captured["window"] == "5m"

    def test_is_a_client_read(self) -> None:
        from hal0.security.exposure import AuthClass, classify

        assert classify("GET", "/api/stats/activity") is AuthClass.CLIENT
