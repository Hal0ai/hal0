"""hal0.hardware — Hardware detection and live stats.

Split into two submodules:
  probe.py  — one-shot hardware detection (GPU, NPU, RAM, disk, CPU)
              writes /etc/hal0/hardware.json at install time and on `hal0 config hardware --refresh`
  stats.py  — live metrics polling (GPU util, VRAM used, RAM available)
              consumed by /api/stats/* and CapacitySnapshot

Port target: haloai lib/hardware.py (split into probe + stats).
See PLAN.md §3 and §7 (installer hardware probe).

Key exports:
    HardwareProbe  — run hardware detection; call probe() to get HardwareInfo.
    HardwareStats  — live metrics; call snapshot() for a JSON-safe dict.
"""

from __future__ import annotations

from hal0.hardware.probe import HardwareInfo, HardwareProbe
from hal0.hardware.stats import HardwareStats

__all__ = [
    "HardwareInfo",
    "HardwareProbe",
    "HardwareStats",
]
