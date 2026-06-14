"""GET /api/stats/power — lightweight hwmon power/thermal snapshot.

Resolves hwmon nodes by *name* (never by hardcoded index).
Every field degrades independently to null when its sysfs path is
absent, unreadable, or unparseable.  The whole probe runs in a thread
so the event-loop is never blocked on sysfs I/O.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import structlog
from fastapi import APIRouter

log = structlog.get_logger(__name__)

router = APIRouter()

# Monkeypatch point for tests — never hardcode the index.
_HWMON_ROOT = Path("/sys/class/hwmon")


# ── sysfs helpers ─────────────────────────────────────────────────────────────


def _find_hwmon(name: str) -> Path | None:
    """Return the first hwmon directory whose ``name`` file matches *name*.

    Returns None if no match or if the root does not exist.
    """
    try:
        entries = list(_HWMON_ROOT.iterdir())
    except (FileNotFoundError, PermissionError, OSError):
        return None
    for entry in entries:
        name_file = entry / "name"
        try:
            if name_file.read_text().strip() == name:
                return entry
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return None


def _read_float(path: Path) -> float | None:
    """Read a single numeric sysfs file, returning None on any error."""
    try:
        return float(path.read_text().strip())
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        return None


def _parse_pp_dpm_sclk(card_glob_root: Path) -> float | None:
    """Scan /sys/class/drm/card*/device/pp_dpm_sclk for the active (*) clock.

    Returns MHz as float, or None if not found/parseable.
    """
    drm = card_glob_root
    try:
        cards = list(drm.glob("card*/device/pp_dpm_sclk"))
    except (OSError, PermissionError):
        return None
    for sclk_path in cards:
        try:
            text = sclk_path.read_text()
        except (FileNotFoundError, PermissionError, OSError):
            continue
        for line in text.splitlines():
            if line.strip().endswith("*"):
                # Format: "0: 800Mhz *"
                m = re.search(r"(\d+)\s*[Mm]hz", line, re.IGNORECASE)
                if m:
                    return float(m.group(1))
    return None


# ── core probe (pure sync, runs in thread) ────────────────────────────────────


def _probe_power() -> dict:
    """Read hwmon + drm sysfs and return the power snapshot dict.

    All fields are independently nullable.
    """
    gpu_power_w: float | None = None
    gpu_temp_c: float | None = None
    gpu_sclk_mhz: float | None = None
    cpu_temp_c: float | None = None

    # ── amdgpu ────────────────────────────────────────────────────────────────
    amdgpu_dir = _find_hwmon("amdgpu")
    if amdgpu_dir is not None:
        # power: power1_average is in microwatts
        raw_power = _read_float(amdgpu_dir / "power1_average")
        if raw_power is not None:
            gpu_power_w = raw_power / 1_000_000.0

        # temperature: prefer temp1_input (edge), millidegrees C
        raw_temp = _read_float(amdgpu_dir / "temp1_input")
        if raw_temp is not None:
            gpu_temp_c = raw_temp / 1000.0

        # sclk: prefer freq1_input (Hz) from hwmon; fall back to pp_dpm_sclk
        raw_freq = _read_float(amdgpu_dir / "freq1_input")
        if raw_freq is not None:
            gpu_sclk_mhz = raw_freq / 1_000_000.0
        else:
            gpu_sclk_mhz = _parse_pp_dpm_sclk(Path("/sys/class/drm"))

    # ── k10temp ───────────────────────────────────────────────────────────────
    k10temp_dir = _find_hwmon("k10temp")
    if k10temp_dir is not None:
        raw_cpu = _read_float(k10temp_dir / "temp1_input")
        if raw_cpu is not None:
            cpu_temp_c = raw_cpu / 1000.0

    return {
        "gpu_power_w": gpu_power_w,
        "gpu_temp_c": gpu_temp_c,
        "gpu_sclk_mhz": gpu_sclk_mhz,
        "cpu_temp_c": cpu_temp_c,
    }


# ── route ─────────────────────────────────────────────────────────────────────


@router.get("/stats/power")
async def get_power_stats() -> dict:
    """Return a lightweight hwmon power/thermal snapshot.

    All fields are independently null when the corresponding sysfs
    path is absent or unreadable — the endpoint never raises.
    """
    return await asyncio.to_thread(_probe_power)
