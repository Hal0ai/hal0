"""Tests for GET /api/stats/power.

The router is NOT yet wired into the main app (lead mounts it in
src/hal0/api/__init__.py after this spike lands).  Tests create a
minimal FastAPI app with just the power router under the canonical prefix.

Covers:
  1. Happy path: full fake hwmon tree -> all four fields correct.
  2. Empty hwmon root -> all four null, endpoint still 200.
  3. amdgpu present but power1_average missing -> gpu_power_w null,
     other gpu/cpu fields still populated.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import hal0.api.routes.power as power_mod
from hal0.api.routes.power import router as power_router

# ── fixture: isolated app ────────────────────────────────────────────────────


@pytest.fixture()
def power_client() -> TestClient:
    """Minimal FastAPI app with only the power router mounted."""
    app = FastAPI()
    app.include_router(power_router, prefix="/api")
    return TestClient(app)


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_hwmon_tree(tmp_path: Path) -> Path:
    """Build a fake /sys/class/hwmon tree under tmp_path.

    Layout:
      hwmon0/  name="amdgpu"
               power1_average="15500000"   (15.5 W)
               temp1_input="52000"         (52.0 °C)
               freq1_input="800000000"     (800.0 MHz)
      hwmon1/  name="k10temp"
               temp1_input="61000"         (61.0 °C)
    """
    root = tmp_path / "hwmon"
    root.mkdir()

    amdgpu = root / "hwmon0"
    amdgpu.mkdir()
    (amdgpu / "name").write_text("amdgpu\n")
    (amdgpu / "power1_average").write_text("15500000\n")
    (amdgpu / "temp1_input").write_text("52000\n")
    (amdgpu / "freq1_input").write_text("800000000\n")

    k10temp = root / "hwmon1"
    k10temp.mkdir()
    (k10temp / "name").write_text("k10temp\n")
    (k10temp / "temp1_input").write_text("61000\n")

    return root


# ── 1. happy path ────────────────────────────────────────────────────────────


class TestPowerHappyPath:
    def test_all_fields_correct(
        self,
        power_client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Full fake hwmon tree returns correct scaled values for all 4 fields."""
        hwmon_root = _make_hwmon_tree(tmp_path)
        monkeypatch.setattr(power_mod, "_HWMON_ROOT", hwmon_root)

        r = power_client.get("/api/stats/power")
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["gpu_power_w"] == pytest.approx(15.5)
        assert body["gpu_temp_c"] == pytest.approx(52.0)
        assert body["gpu_sclk_mhz"] == pytest.approx(800.0)
        assert body["cpu_temp_c"] == pytest.approx(61.0)


# ── 2. empty / missing hwmon root ────────────────────────────────────────────


class TestPowerMissingTree:
    def test_empty_tree_all_null_200(
        self,
        power_client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-existent hwmon root -> all four fields null, endpoint 200."""
        monkeypatch.setattr(power_mod, "_HWMON_ROOT", tmp_path / "nonexistent")

        r = power_client.get("/api/stats/power")
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["gpu_power_w"] is None
        assert body["gpu_temp_c"] is None
        assert body["gpu_sclk_mhz"] is None
        assert body["cpu_temp_c"] is None


# ── 3. partial amdgpu: power1_average missing ────────────────────────────────


class TestPowerPartialAmdgpu:
    def test_missing_power1_average_gpu_power_null(
        self,
        power_client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """amdgpu dir present but power1_average absent -> gpu_power_w null.

        temp1_input, freq1_input, and k10temp/temp1_input still yield values.
        """
        hwmon_root = _make_hwmon_tree(tmp_path)
        # Remove the power file from hwmon0 (amdgpu)
        (hwmon_root / "hwmon0" / "power1_average").unlink()

        monkeypatch.setattr(power_mod, "_HWMON_ROOT", hwmon_root)

        r = power_client.get("/api/stats/power")
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["gpu_power_w"] is None  # missing file -> null
        assert body["gpu_temp_c"] == pytest.approx(52.0)
        assert body["gpu_sclk_mhz"] == pytest.approx(800.0)
        assert body["cpu_temp_c"] == pytest.approx(61.0)
