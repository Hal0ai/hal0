"""Tests for cpu_util field added to /api/stats/hardware (non-blocking psutil poll).

Covers:
- cpu_util present and correctly scaled (42.0 pct -> 0.42)
- clamping: >100 pct -> 1.0
- psutil unavailable (_psutil=None) -> cpu_util is None, endpoint still 200
- psutil raising -> cpu_util is None, endpoint still 200
"""

from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient

import hal0.api.routes.hardware as hw_mod

# ── shared stub that satisfies _cached_snapshot ───────────────────────────────


class _MinimalStatsStub:
    """HardwareStats stand-in returning a minimal snapshot (no GPU fields)."""

    def snapshot(self) -> dict:
        return {"ram_used_gb": 2.0}

    def gpu_sample(self) -> None:
        return None


# ── helpers ───────────────────────────────────────────────────────────────────


def _wire_stats(client: TestClient) -> None:
    """Attach the minimal stats stub to the app so _local_live_stats runs."""
    client.app.state.hardware_stats = _MinimalStatsStub()


# ── happy path ────────────────────────────────────────────────────────────────


class TestCpuUtilHappyPath:
    def test_cpu_util_scaled_correctly(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """psutil.cpu_percent(42.0) -> cpu_util == 0.42 in response."""
        _wire_stats(client)

        fake_psutil = types.SimpleNamespace(cpu_percent=lambda interval=None: 42.0)
        monkeypatch.setattr(hw_mod, "_psutil", fake_psutil)

        resp = client.get("/api/stats/hardware")
        assert resp.status_code == 200
        assert resp.json()["cpu_util"] == pytest.approx(0.42)

    def test_cpu_util_clamped_above_one(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A reading over 100 pct (e.g. transient kernel artifact) clamps to 1.0."""
        _wire_stats(client)

        fake_psutil = types.SimpleNamespace(cpu_percent=lambda interval=None: 110.0)
        monkeypatch.setattr(hw_mod, "_psutil", fake_psutil)

        resp = client.get("/api/stats/hardware")
        assert resp.status_code == 200
        assert resp.json()["cpu_util"] == pytest.approx(1.0)

    def test_cpu_util_zero_is_valid(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """0.0 pct (e.g. first-call prime on some kernels) -> 0.0, not None."""
        _wire_stats(client)

        fake_psutil = types.SimpleNamespace(cpu_percent=lambda interval=None: 0.0)
        monkeypatch.setattr(hw_mod, "_psutil", fake_psutil)

        resp = client.get("/api/stats/hardware")
        assert resp.status_code == 200
        assert resp.json()["cpu_util"] == pytest.approx(0.0)


# ── degraded / unavailable paths ─────────────────────────────────────────────


class TestCpuUtilUnavailable:
    def test_psutil_none_gives_cpu_util_none_endpoint_200(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When psutil could not be imported (_psutil=None), cpu_util is None
        and the endpoint still returns 200."""
        _wire_stats(client)
        monkeypatch.setattr(hw_mod, "_psutil", None)

        resp = client.get("/api/stats/hardware")
        assert resp.status_code == 200
        # None values are skipped by the merge loop — cpu_util absent or None.
        assert resp.json().get("cpu_util") is None

    def test_psutil_raises_gives_cpu_util_none_endpoint_200(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If psutil.cpu_percent() raises, cpu_util is absent/None — no 500."""
        _wire_stats(client)

        def _boom(interval=None):
            raise OSError("no /proc/stat on this exotic platform")

        fake_psutil = types.SimpleNamespace(cpu_percent=_boom)
        monkeypatch.setattr(hw_mod, "_psutil", fake_psutil)

        resp = client.get("/api/stats/hardware")
        assert resp.status_code == 200
        # None values are skipped by the merge loop — cpu_util absent or None.
        assert resp.json().get("cpu_util") is None
