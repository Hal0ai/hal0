"""Hardware-anchored tier eligibility.

Reads ``/proc/meminfo`` once per process and returns the bundle names
whose ``min_ram_gb`` floor fits the detected unified RAM. The picker
greys out ineligible tiers but still surfaces them with a tooltip, so
the operator sees the gap (rather than mysteriously missing options).

The override knob ``HAL0_HOST_RAM_GB`` is a test seam and a documented
escape hatch — operators on a too-small box who want to install a
larger bundle anyway can run with the env var set. The picker UI
respects whatever this function returns; no separate "force install"
flag exists.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from hal0.bundles.tiers import load_all_bundles

_DEFAULT_MEMINFO = Path("/proc/meminfo")


def _read_meminfo_gb(path: Path = _DEFAULT_MEMINFO) -> int:
    """Parse MemTotal out of /proc/meminfo and return whole GB.

    Returns ``0`` if the file can't be read; the picker treats that as
    "no eligibility info" and surfaces every tier without greying. The
    ``HAL0_HOST_RAM_GB`` override short-circuits the parse entirely.
    """

    override = os.environ.get("HAL0_HOST_RAM_GB", "").strip()
    if override:
        try:
            return max(0, int(override))
        except ValueError:
            return 0

    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return 0

    for line in content.splitlines():
        # MemTotal entries look like: "MemTotal:       16332620 kB"
        if not line.startswith("MemTotal:"):
            continue
        parts = line.split()
        if len(parts) < 2:
            return 0
        try:
            kb = int(parts[1])
        except ValueError:
            return 0
        # Floor-divide kilobytes to GiB. Strix Halo / NPU boxes always
        # have round GiB totals so floor-vs-round doesn't change which
        # tier is eligible in practice.
        return max(0, kb // (1024 * 1024))
    return 0


@lru_cache(maxsize=1)
def host_ram_gb() -> int:
    """Detected unified RAM in whole GB. Process-lifetime cached."""

    return _read_meminfo_gb()


@lru_cache(maxsize=1)
def eligible_tiers() -> list[str]:
    """Return bundle names whose ``min_ram_gb`` <= host RAM.

    The returned list preserves the canonical bundle order from
    :data:`hal0.bundles.tiers.BUNDLES`. If the host RAM probe failed
    (returns ``0``), every tier is treated as eligible — the picker
    falls back to a no-greying mode rather than locking the operator
    out entirely.
    """

    ram = host_ram_gb()
    if ram <= 0:
        return [manifest.bundle.name for manifest in load_all_bundles()]
    return [
        manifest.bundle.name for manifest in load_all_bundles() if manifest.bundle.min_ram_gb <= ram
    ]


def reset_cache() -> None:
    """Drop the cached probe + eligibility lists. Test-only."""

    host_ram_gb.cache_clear()
    eligible_tiers.cache_clear()


__all__ = [
    "eligible_tiers",
    "host_ram_gb",
    "reset_cache",
]
