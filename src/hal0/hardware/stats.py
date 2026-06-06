"""Hardware runtime stats — GPU utilisation, RAM usage, slot port occupancy.

HardwareStats provides live metrics for:
  - GET /api/stats/gpu
  - GET /api/stats/ram
  - The Hardware dashboard view live numbers
  - CapacitySnapshot.probe()

Port target: haloai lib/hardware.py (split: probe + stats).
See PLAN.md §3.
"""

from __future__ import annotations

import contextlib
import socket
from pathlib import Path
from typing import Any

import structlog

from hal0.hardware.probe import _amd_drm_device, _read_sysfs_mb, _run

log = structlog.get_logger(__name__)


# Slot port pool (PLAN §2: "8081-8099 — slot ports assigned by config").
# The task brief says 8100-8199; PLAN.md says 8081-8099. We honour PLAN.md
# (the canonical decision) and expose the range here so the API + dashboard
# don't drift from config.SlotsConfig.port_range_{start,end}.
#
# # NOTE: PLAN §2 says ports 8081-8099 — task brief mentioned 8100-8199.
# Going with PLAN. If the brief was right the constant can be flipped here
# without touching call sites.
SLOT_PORT_RANGE_START = 8081
SLOT_PORT_RANGE_END = 8099


def _port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if anything is listening on (host, port).

    We try a non-blocking connect rather than a bind: this works even when
    we're running as a non-privileged user that can't bind low-numbered
    ports, and it doesn't transiently steal a free port.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.05)
    try:
        return s.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        s.close()


class HardwareStats:
    """Live hardware metrics reader.

    Reads from sysfs and /proc/meminfo; nvidia-smi only on NVIDIA hosts
    (vendor cached).
    All methods are synchronous; wrap in asyncio.to_thread() for async callers.
    """

    def __init__(self) -> None:
        # GPU vendor is detected once and cached. None = not yet probed.
        # Values: "nvidia", "amd", "unknown".
        self._gpu_vendor: str | None = None
        # Cached AMD DRM device sysfs dir (the .../cardN/device path), or None.
        self._amd_drm: Path | None = None

    def _vendor(self) -> str:
        """Detect GPU vendor exactly once and cache it.

        On an AMD box (DRM sysfs present) this never execs nvidia-smi. We
        only probe nvidia-smi when no AMD DRM device is found, so real
        nvidia hosts still resolve correctly while AMD hosts do zero
        subprocess work. Result is memoised on the instance.
        """
        if self._gpu_vendor is not None:
            return self._gpu_vendor
        drm = _amd_drm_device()
        if drm is not None:
            self._amd_drm = drm
            self._gpu_vendor = "amd"
            return self._gpu_vendor
        # No AMD DRM card — probe nvidia-smi once (cheap on real nvidia hosts,
        # a single failing exec at most on others, then cached forever).
        rc, out, _ = _run(
            [
                "nvidia-smi",
                "--query-gpu=name",
                "--format=csv,noheader,nounits",
            ]
        )
        self._gpu_vendor = "nvidia" if (rc == 0 and out.strip()) else "unknown"
        return self._gpu_vendor

    def gpu_util(self) -> float | None:
        """Return current GPU compute utilisation as a fraction [0.0, 1.0].

        AMD: reads sysfs gpu_busy_percent directly (no subprocess). NVIDIA:
        nvidia-smi. Returns None if no utilisation counter is exposed.
        """
        vendor = self._vendor()
        if vendor == "amd" and self._amd_drm is not None:
            txt = self._read_text(self._amd_drm / "gpu_busy_percent")
            if txt is not None:
                try:
                    return round(float(txt.strip()) / 100.0, 3)
                except ValueError:
                    pass
            return None
        if vendor == "nvidia":
            rc, out, _ = _run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu",
                    "--format=csv,noheader,nounits",
                ]
            )
            if rc == 0 and out.strip():
                try:
                    return round(float(out.strip().splitlines()[0]) / 100.0, 3)
                except ValueError:
                    pass
        return None

    def gpu_vram_used_mb(self) -> float | None:
        """Return current GPU VRAM usage in MiB.

        On AMD UMA (Strix Halo) returns max(vram_used, gtt_used) via sysfs
        (no subprocess). On NVIDIA, parses nvidia-smi.
        """
        vendor = self._vendor()
        if vendor == "amd" and self._amd_drm is not None:
            vram = _read_sysfs_mb(self._amd_drm / "mem_info_vram_used")
            gtt = _read_sysfs_mb(self._amd_drm / "mem_info_gtt_used")
            candidates = [v for v in (vram, gtt) if v is not None]
            return max(candidates) if candidates else None
        if vendor == "nvidia":
            rc, out, _ = _run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.used",
                    "--format=csv,noheader,nounits",
                ]
            )
            if rc == 0 and out.strip():
                try:
                    return round(float(out.strip().splitlines()[0]), 1)
                except ValueError:
                    pass
        return None

    def gpu_vram_total_mb(self) -> float | None:
        """Return total GPU VRAM in MiB (or GTT pool on UMA)."""
        vendor = self._vendor()
        if vendor == "amd" and self._amd_drm is not None:
            vram = _read_sysfs_mb(self._amd_drm / "mem_info_vram_total")
            gtt = _read_sysfs_mb(self._amd_drm / "mem_info_gtt_total")
            candidates = [v for v in (vram, gtt) if v is not None]
            return max(candidates) if candidates else None
        if vendor == "nvidia":
            rc, out, _ = _run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.total",
                    "--format=csv,noheader,nounits",
                ]
            )
            if rc == 0 and out.strip():
                try:
                    return round(float(out.strip().splitlines()[0]), 1)
                except ValueError:
                    pass
        return None

    def ram_used_gb(self) -> float:
        """Return current system RAM used in GiB (MemTotal - MemAvailable)."""
        total = 0
        avail = 0
        txt = self._read_text(Path("/proc/meminfo"))
        if not txt:
            return 0.0
        for line in txt.splitlines():
            if line.startswith("MemTotal:"):
                with contextlib.suppress(IndexError, ValueError):
                    total = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                with contextlib.suppress(IndexError, ValueError):
                    avail = int(line.split()[1])
        return round(max(0, total - avail) / (1024 * 1024), 2)

    def ram_available_gb(self) -> float:
        """Return current system RAM available in GiB (MemAvailable)."""
        txt = self._read_text(Path("/proc/meminfo"))
        if not txt:
            return 0.0
        for line in txt.splitlines():
            if line.startswith("MemAvailable:"):
                try:
                    return round(int(line.split()[1]) / (1024 * 1024), 2)
                except (IndexError, ValueError):
                    return 0.0
        return 0.0

    def slot_port_occupancy(self) -> dict[int, bool]:
        """Return {port: in_use} for every port in the slot pool.

        Used by SlotsConfig validation and the dashboard's "next free port"
        display. Probes 127.0.0.1 only (slots never bind public).

        NOTE: this performs a connect_ex() scan over the full slot port
        range (19 connect attempts). It is intentionally NOT part of
        snapshot() (issue #427) — the polled /api/stats/hardware path
        does not need port occupancy, and N concurrent dashboard clients
        x 19 connect_ex calls per poll wedged the single-event-loop API.
        Callers that legitimately need it (config validation, next-free-
        port display) should call this method directly.
        """
        return {p: _port_in_use(p) for p in range(SLOT_PORT_RANGE_START, SLOT_PORT_RANGE_END + 1)}

    def occupied_slot_ports(self) -> list[int]:
        """Return the sorted list of slot ports currently bound.

        See :meth:`slot_port_occupancy` for the connect_ex cost warning.
        Not invoked by the polled snapshot() — call directly if needed.
        """
        return [p for p, used in self.slot_port_occupancy().items() if used]

    def snapshot(self, *, include_slot_ports: bool = False) -> dict[str, Any]:
        """Return a JSON-safe dict of all available stats.

        Field shape mirrors the haloai /api/status response (subset):
            ram_used_gb, ram_available_gb,
            gpu_util, gpu_vram_used_mb, gpu_vram_total_mb,
            slot_ports_occupied: list[int]   (only when include_slot_ports=True)

        ``include_slot_ports`` defaults to False — the slot-port scan
        (19 connect_ex calls) does NOT belong on the polled hot path
        (issue #427). The polled /api/stats/hardware dashboard refresh
        only needs RAM + GPU numbers; the slot-port view is sourced
        from the dedicated config-validation / next-free-port callers.
        """
        out: dict[str, Any] = {
            "ram_used_gb": self.ram_used_gb(),
            "ram_available_gb": self.ram_available_gb(),
            "gpu_util": self.gpu_util(),
            "gpu_vram_used_mb": self.gpu_vram_used_mb(),
            "gpu_vram_total_mb": self.gpu_vram_total_mb(),
        }
        if include_slot_ports:
            out["slot_ports_occupied"] = self.occupied_slot_ports()
        return out

    # Internal helpers kept here (rather than imported) so tests can monkeypatch
    # this instance method without touching the probe module's globals.

    def _read_text(self, path: Path) -> str | None:
        try:
            return path.read_text()
        except OSError:
            return None


__all__ = ["SLOT_PORT_RANGE_END", "SLOT_PORT_RANGE_START", "HardwareStats"]
