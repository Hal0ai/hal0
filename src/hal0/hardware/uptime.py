"""Live host uptime — a single, cheap, dependency-free sysfs read.

Split out of :mod:`hal0.hardware.probe` (#1905) so API routes can read a
fresh ``uptime_s`` on every request without importing the probe module's
private GPU/NPU internals — ``hal0.api.routes.hardware`` deliberately
keeps zero imports from ``hal0.hardware.probe`` (see #703 and
``test_route_module_has_no_private_probe_imports``), since GPU/NPU state
there is meant to flow through the coalesced ``HardwareStats`` singleton,
not per-request re-probes. Uptime has no such singleton and no expensive
fanout — it's one file read — so it doesn't need that abstraction, but it
still shouldn't drag in the rest of the probe module's surface.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

_UPTIME_PATH = Path("/proc/uptime")


def read_uptime_s() -> int:
    """Return whole seconds since boot from /proc/uptime, 0 on failure.

    /proc/uptime is "<uptime_seconds> <idle_seconds>"; we take the first
    float and floor it.
    """
    try:
        txt = _UPTIME_PATH.read_text()
    except OSError:
        return 0
    with contextlib.suppress(IndexError, ValueError):
        return int(float(txt.split()[0]))
    return 0


__all__ = ["read_uptime_s"]
