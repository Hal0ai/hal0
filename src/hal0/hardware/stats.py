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

from hal0.hardware import gpu_view
from hal0.hardware.gpu_view import GPUMemorySample
from hal0.hardware.probe import _amd_drm_device, _run

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

    def gpu_sample(self) -> GPUMemorySample:
        """Take a typed GPU memory + utilization sample (issue #703).

        Delegates to :func:`hal0.hardware.gpu_view.sample` with the
        memoised vendor/drm so detection isn't repeated per read.
        ``run=_run`` is resolved through THIS module's global at call
        time, so tests monkeypatching ``stats._run`` keep working.
        """
        vendor = self._vendor()
        return gpu_view.sample(vendor=vendor, drm=self._amd_drm, run=_run)

    def gpu_util(self) -> float | None:
        """Return current GPU compute utilisation as a fraction [0.0, 1.0].

        AMD: reads sysfs gpu_busy_percent directly (no subprocess). NVIDIA:
        nvidia-smi. Returns None if no utilisation counter is exposed.

        NOTE: on a forced-high AMD host (gpu-compute.service pins the perf
        level) this counter reads flat 100 regardless of load — check
        ``gpu_sample().util_is_forced_high`` before trusting it. The value
        is reported RAW either way.
        """
        return self.gpu_sample().gpu_busy

    def gpu_vram_used_mb(self) -> float | None:
        """Return current GPU VRAM usage in MiB.

        On AMD UMA (Strix Halo) returns max(vram_used, gtt_used) via sysfs
        (no subprocess). On NVIDIA, parses nvidia-smi.
        """
        return self.gpu_sample().used_mb

    def gpu_vram_total_mb(self) -> float | None:
        """Return total GPU VRAM in MiB (or GTT pool on UMA)."""
        return self.gpu_sample().total_mb

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
            gtt_used_mb, vram_used_mb, util_is_forced_high   (#703, typed
                off the GPUMemorySample for the API route)
            slot_ports_occupied: list[int]   (only when include_slot_ports=True)

        ``include_slot_ports`` defaults to False — the slot-port scan
        (19 connect_ex calls) does NOT belong on the polled hot path
        (issue #427). The polled /api/stats/hardware dashboard refresh
        only needs RAM + GPU numbers; the slot-port view is sourced
        from the dedicated config-validation / next-free-port callers.
        """
        # One sample feeds every GPU field — same sysfs/nvidia-smi cost as
        # the pre-#703 three-method spread, plus the typed split + flag the
        # /api/stats/hardware route reads off the SWR cache (no more
        # per-request sysfs re-reads in the route).
        smp = self.gpu_sample()
        out: dict[str, Any] = {
            "ram_used_gb": self.ram_used_gb(),
            "ram_available_gb": self.ram_available_gb(),
            "gpu_util": smp.gpu_busy,
            "gpu_vram_used_mb": smp.used_mb,
            "gpu_vram_total_mb": smp.total_mb,
            "gtt_used_mb": smp.gtt_used_mb,
            "vram_used_mb": smp.vram_used_mb,
            "util_is_forced_high": smp.util_is_forced_high,
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
