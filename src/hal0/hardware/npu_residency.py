"""NPU duty cycle from the kernel's runtime-PM residency counters.

``runtime_active_time`` and ``runtime_suspended_time`` under the accel
device's ``power/`` directory count milliseconds spent awake and asleep.
The duty cycle over an interval is ``Δactive / (Δactive + Δsuspended)``
between two reads, so a meter keeps its previous reading. Each reader owns
its own meter: two callers sharing one would each see only the slice of
time since the other's last read.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_PM_ROOT = Path("/sys/class/accel/accel0/device/power")


class NpuResidencyMeter:
    """Duty cycle in [0.0, 1.0] since the previous :meth:`read`, or None."""

    def __init__(self, root: Path = DEFAULT_PM_ROOT) -> None:
        self.root = root
        self._prev: tuple[int, int] | None = None  # (active_ms, suspended_ms)

    def read(self) -> float | None:
        """Return the duty cycle since the last call.

        None on the first call (a delta needs two reads), on any I/O or parse
        error, when no time elapsed, or when a counter went backwards (driver
        reload); the latter also restarts the baseline.
        """
        try:
            active_ms = int((self.root / "runtime_active_time").read_text().strip())
            suspended_ms = int((self.root / "runtime_suspended_time").read_text().strip())
        except Exception:
            return None

        prev = self._prev
        self._prev = (active_ms, suspended_ms)
        if prev is None:
            return None

        da = active_ms - prev[0]
        ds = suspended_ms - prev[1]
        if da < 0 or ds < 0:
            return None
        denom = da + ds
        if denom <= 0:
            return None
        return max(0.0, min(1.0, da / denom))
