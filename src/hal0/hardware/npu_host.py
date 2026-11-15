"""Host-readiness facts for the XDNA NPU that hal0 can read from its own container.

ADR-0057 lists the host stack a GGML (``xdna``) NPU slot needs: NPU firmware
>= 1.1.2.65, XRT 2.25.0, ``iommu=pt amd_iommu=on`` on the kernel command line,
unlimited memlock, a held fabric clock and ``xrt-smi configure --pmode turbo``.
Each probe here reads one of those from a place that is visible inside hal0's
LXC/container (sysfs, ``/proc/cmdline``, the XRT library files, the fabric-clock
sysfs controls), is best-effort, never raises and never fabricates: a value
that cannot be read is ``None``, so the UI can say "unknown" and why.

Everything is blocking file I/O on tiny files; callers run it from the cached
facts refresh thread (``hal0.api.routes.npu``), never from the request path.

One ADR-0057 item has no readable source from inside the container and is
deliberately not probed here: the power mode. ``xrt-smi`` is not installed on
the host, the xdna runner image does not ship it, and the amdxdna driver
exposes no sysfs knob for it. ``Stats.npu_pmode`` still reads it when an
operator installed ``xrt-smi`` on the host.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

#: ADR-0057: minimum NPU firmware for the ggml-xdna lane.
FIRMWARE_MIN = "1.1.2.65"
#: ADR-0057: the XRT release the runner image is built against.
XRT_MIN = "2.25.0"
#: ADR-0057: kernel command-line tokens the NPU needs (IOMMU passthrough).
REQUIRED_CMDLINE_TOKENS: tuple[str, ...] = ("iommu=pt", "amd_iommu=on")

_XRT_LIB_RE = re.compile(r"^libxrt_coreutil\.so\.(\d+)\.(\d+)\.(\d+)$")
_XRT_LIB_DIRS: tuple[str, ...] = (
    "/usr/lib/x86_64-linux-gnu",
    "/usr/lib64",
    "/usr/lib",
    "/opt/xilinx/xrt/lib",
)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _version_tuple(raw: str) -> tuple[int, ...] | None:
    parts = raw.strip().split(".")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def version_at_least(found: str, minimum: str) -> bool | None:
    """``found >= minimum`` over dotted integers; ``None`` when either is not one.

    Shorter tuples are zero-padded, so ``2.25`` equals ``2.25.0``.
    """
    a, b = _version_tuple(found), _version_tuple(minimum)
    if a is None or b is None:
        return None
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) >= b + (0,) * (n - len(b))


def parse_firmware_version(text: str | None) -> str | None:
    """First dotted-integer token of an amdxdna ``fw_version`` file (``"1.1.2.65"``)."""
    if not text:
        return None
    for token in text.split():
        if "." in token and _version_tuple(token) is not None:
            return token
    return None


def read_firmware_version(sys_root: str | Path = "/sys") -> str | None:
    """NPU firmware version from ``/sys/class/accel/accel*/device/fw_version``."""
    try:
        paths = sorted(Path(sys_root).glob("class/accel/accel*/device/fw_version"))
    except OSError:
        return None
    for path in paths:
        version = parse_firmware_version(_read(path))
        if version:
            return version
    return None


def missing_cmdline_tokens(
    cmdline: str | None, required: Iterable[str] = REQUIRED_CMDLINE_TOKENS
) -> list[str] | None:
    """Required tokens absent from a kernel command line; ``None`` when it is unreadable.

    Whole-token match, so ``noiommu=pt`` does not satisfy ``iommu=pt``.
    """
    if cmdline is None or not cmdline.strip():
        return None
    tokens = set(cmdline.split())
    return [tok for tok in required if tok not in tokens]


def read_missing_cmdline_tokens(proc_root: str | Path = "/proc") -> list[str] | None:
    return missing_cmdline_tokens(_read(Path(proc_root) / "cmdline"))


def parse_xrt_library_version(filename: str) -> str | None:
    """``libxrt_coreutil.so.2.25.00`` -> ``"2.25.0"`` (the shipped file name zero-pads)."""
    m = _XRT_LIB_RE.match(filename)
    if not m:
        return None
    return ".".join(str(int(g)) for g in m.groups())


def read_xrt_version(lib_dirs: Iterable[str | Path] = _XRT_LIB_DIRS) -> str | None:
    """Highest host XRT version found as a ``libxrt_coreutil.so.X.Y.Z`` library, else ``None``."""
    best: tuple[int, ...] | None = None
    best_s: str | None = None
    for d in lib_dirs:
        try:
            names = [p.name for p in Path(d).glob("libxrt_coreutil.so.*.*.*")]
        except OSError:
            continue
        for name in names:
            v = parse_xrt_library_version(name)
            t = _version_tuple(v) if v else None
            if v and t and (best is None or t > best):
                best, best_s = t, v
    return best_s


def host_readiness(
    *,
    sys_root: str | Path = "/sys",
    proc_root: str | Path = "/proc",
    lib_dirs: Iterable[str | Path] = _XRT_LIB_DIRS,
) -> dict[str, Any]:
    """Every readable ADR-0057 host fact, ``None`` for each one that is not.

    ``memlock`` reports what hal0 itself configures for xdna slot containers
    (``--ulimit memlock=-1``, read off the launch constant), not the limit of
    this process.
    """
    from hal0.slots.fabric_clock_guard import read_fabric_clock

    out: dict[str, Any] = dict(NPU_READINESS_NONE)
    # Each probe is independent and best-effort: one failing leaves its keys None.
    with contextlib.suppress(Exception):
        fw = read_firmware_version(sys_root)
        out["firmware_version"] = fw
        out["firmware_ok"] = version_at_least(fw, FIRMWARE_MIN) if fw else None
    with contextlib.suppress(Exception):
        out["cmdline_missing"] = read_missing_cmdline_tokens(proc_root)
    with contextlib.suppress(Exception):
        xrt = read_xrt_version(lib_dirs)
        out["xrt_version"] = xrt
        out["xrt_ok"] = version_at_least(xrt, XRT_MIN) if xrt else None
    with contextlib.suppress(Exception):
        from hal0.providers.container import XDNA_ULIMITS

        out["memlock"] = "unlimited" if "memlock=-1" in XDNA_ULIMITS else None
    with contextlib.suppress(Exception):
        state = read_fabric_clock(sys_root)
        if state.readable:
            out["fclk_held"] = state.held
            out["fclk_perf_level"] = state.perf_level
    return out


#: The all-unknown shape (also the key list the occupancy route copies).
NPU_READINESS_NONE: dict[str, Any] = {
    "firmware_version": None,
    "firmware_min": FIRMWARE_MIN,
    "firmware_ok": None,
    "cmdline_missing": None,
    "xrt_version": None,
    "xrt_min": XRT_MIN,
    "xrt_ok": None,
    "memlock": None,
    "fclk_held": None,
    "fclk_perf_level": None,
}
