"""Host-introspection probes for hal0-admin MCP (issue #237).

Four read-only probes exposed as autonomous-read MCP tools. Centralise
logic that several downstream consumers need to share:

* Hermes-Agent bootstrap (`env_probe` phase, #241) reads `env_report()`
  + `gpu_target_version()` + `npu_status()` + `model_store_probe(path)`
  to build its `EnvironmentReport`.
* The dashboard's host panel + future support-bundle exporters consume
  the same surface.

All probes are LXC-correct: they read `/sys`/`/proc` and `lsmod`, never
`/lib/firmware/amdnpu/` (host-only) or `modinfo amdxdna` (fails in the
container because module files live on the host). Recipes lifted
verbatim from `docs/internal/hermes-env-probe-recipes-2026-05-23.md` —
see that doc for the why behind each access pattern.

Functions are synchronous and quick (no subprocess timeout > 5 s). On
any missing input they return a structured "missing" / "unavailable"
shape rather than raising, so the MCP dispatch can serialise the result
as a normal tool response.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess  # nosec B404 — used with literal argv + timeout
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROBE_TOOLS: frozenset[str] = frozenset(
    {
        "gpu_target_version",
        "npu_status",
        "env_report",
        "model_store_probe",
    }
)


# ── Low-level helpers ────────────────────────────────────────────────────────


def _read_text(path: str | Path, *, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return default


def _run(argv: list[str], *, timeout: float = 5.0) -> tuple[int, str, str]:
    """Run ``argv`` with stdin closed + a hard timeout.

    Returns ``(returncode, stdout, stderr)`` even on failure. Probes
    must never raise — every caller (`env_report`, etc.) composes many
    of these and a single missing binary shouldn't blow the report up.
    """
    try:
        result = subprocess.run(  # nosec B603 — literal argv from caller
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return (-1, "", "")
    return (result.returncode, result.stdout, result.stderr)


# ── gpu_target_version ───────────────────────────────────────────────────────


def gpu_target_version() -> dict[str, Any]:
    """Decode KFD's ``gfx_target_version`` to a ``gfxNNNN`` string.

    Strix Halo = ``gfx1151`` (raw integer ``110501``). Returns the
    canonical string ROCm/HSA + llama.cpp/Lemonade match on so callers
    can compare to known good values directly.

    Walks every ``/sys/class/kfd/kfd/topology/nodes/*/properties`` file
    and picks the first non-zero target — node 0 is CPU on multi-node
    boxes, GPU nodes have non-zero gfx_target_version.
    """
    root = Path("/sys/class/kfd/kfd/topology/nodes")
    if not root.exists():
        return {
            "present": False,
            "reason": "kfd topology missing — amdkfd not loaded",
            "raw": None,
            "gfx": None,
        }
    for node_dir in sorted(root.iterdir(), key=lambda p: p.name):
        props = _read_text(node_dir / "properties")
        if not props:
            continue
        for line in props.splitlines():
            if not line.startswith("gfx_target_version "):
                continue
            raw_str = line.split(" ", 1)[1].strip()
            try:
                raw = int(raw_str)
            except ValueError:
                continue
            if raw == 0:
                # CPU node — keep scanning.
                break
            # KFD encodes as decimal triplets {major*10000 + minor*100 +
            # step} where minor + step render as single hex digits in
            # the canonical gfx string. 110501 → major=11 minor=5 step=1
            # → "gfx1151"; 110000 → "gfx1100"; 90400 → "gfx940".
            major = raw // 10000
            minor = (raw // 100) % 100
            step = raw % 100
            gfx = f"gfx{major}{minor:x}{step:x}"
            return {
                "present": True,
                "raw": raw,
                "gfx": gfx,
                "node": node_dir.name,
            }
    return {
        "present": False,
        "reason": "no gpu node with non-zero gfx_target_version found",
        "raw": None,
        "gfx": None,
    }


# ── npu_status ───────────────────────────────────────────────────────────────


def npu_status() -> dict[str, Any]:
    """Report XDNA NPU presence + driver binding.

    LXC-correct: checks ``/dev/accel/accel0`` (passes when dev0..dev3
    cgroup passthrough is configured) and ``lsmod | grep amdxdna``.
    Does NOT call ``modinfo amdxdna`` (fails in our container because
    module files live on the host) and does NOT read
    ``/lib/firmware/amdnpu/`` (firmware loaded on the host before LXC
    boot — absence inside the container is expected).

    PCI ID is the canonical signal: ``1022:17F0`` = XDNA2 (Strix Halo),
    ``1022:1502`` = XDNA1 (Phoenix).
    """
    device_node = "/dev/accel/accel0"
    present = Path(device_node).exists()

    lsmod_rc, lsmod_out, _ = _run(["lsmod"])
    driver_loaded = lsmod_rc == 0 and any(
        line.split() and line.split()[0] == "amdxdna" for line in lsmod_out.splitlines()
    )

    pci_id: str | None = None
    pci_root = Path("/sys/bus/pci/drivers/amdxdna")
    if pci_root.exists():
        for entry in pci_root.iterdir():
            uevent = _read_text(entry / "uevent")
            for line in uevent.splitlines():
                if line.startswith("PCI_ID="):
                    pci_id = line.split("=", 1)[1].strip()
                    break
            if pci_id:
                break

    xdna_gen: int | None = None
    if pci_id == "1022:17F0":
        xdna_gen = 2
    elif pci_id == "1022:1502":
        xdna_gen = 1

    return {
        "present": present,
        "device_node": device_node if present else None,
        "driver_loaded": driver_loaded,
        "pci_id": pci_id,
        "xdna_gen": xdna_gen,
    }


# ── model_store_probe ────────────────────────────────────────────────────────


def model_store_probe(path: str) -> dict[str, Any]:
    """Probe a candidate model-store path for usability.

    Returns ``fstype`` (zfs/ext4/nfs4/…), free + total bytes, writable
    flag, and ``is_uma_aware`` — true when the path lives on a
    filesystem we know hands the iGPU a unified-memory budget (ZFS on
    Strix Halo) versus a discrete-VRAM topology.

    Path-only contract: callers explicitly pass the path to probe.
    Returning an error envelope is preferred over raising so the MCP
    response stays JSON-serialisable.
    """
    p = Path(path)
    if not p.exists():
        return {
            "path": str(p),
            "exists": False,
            "reason": "path not found",
        }

    try:
        st = os.statvfs(p)
    except OSError as exc:
        return {
            "path": str(p),
            "exists": True,
            "reason": f"statvfs failed: {exc}",
        }

    rc, out, _ = _run(["stat", "-f", "-c", "%T", str(p)])
    fstype = out.strip() if rc == 0 else "unknown"

    free_bytes = st.f_bavail * st.f_frsize
    total_bytes = st.f_blocks * st.f_frsize
    # UMA-aware: the host's iGPU draws from the same pool the
    # filesystem sits on. ZFS on devpool is the Strix Halo recipe;
    # NFS overlays don't qualify (latency budget unsuitable for
    # weight loads).
    is_uma_aware = fstype in {"zfs", "ext4", "btrfs", "xfs"} and not fstype.startswith("nfs")

    return {
        "path": str(p),
        "exists": True,
        "fstype": fstype,
        "writable": os.access(p, os.W_OK),
        "free_bytes": free_bytes,
        "total_bytes": total_bytes,
        "free_mb": free_bytes // (1024 * 1024),
        "total_mb": total_bytes // (1024 * 1024),
        "is_uma_aware": is_uma_aware,
    }


# ── env_report — composite ───────────────────────────────────────────────────


@dataclass
class EnvReport:
    """Consolidated host snapshot returned by ``env_report``.

    Each section is a dict so partial-failure shapes are easy: a probe
    that can't read its source returns ``{"present": False, ...}`` and
    the surrounding fields still populate.

    Schema mirrors the per-section accessors so callers using only one
    field don't need the full report.
    """

    container: dict[str, Any] = field(default_factory=dict)
    cpu: dict[str, Any] = field(default_factory=dict)
    ram: dict[str, Any] = field(default_factory=dict)
    gpu: dict[str, Any] = field(default_factory=dict)
    npu: dict[str, Any] = field(default_factory=dict)
    network: dict[str, Any] = field(default_factory=dict)
    tooling: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1


def _probe_container() -> dict[str, Any]:
    # systemd-detect-virt is the cheapest oracle; fall back to PID-1
    # env-var probe on stripped images (alpine/scratch) without
    # systemd.
    container = "none"
    vm = "none"
    if shutil.which("systemd-detect-virt"):
        _, c_out, _ = _run(["systemd-detect-virt", "--container"])
        _, v_out, _ = _run(["systemd-detect-virt", "--vm"])
        container = c_out.strip() or "none"
        vm = v_out.strip() or "none"
    else:
        environ = _read_text("/proc/1/environ").replace("\x00", "\n")
        for line in environ.splitlines():
            if line.startswith("container="):
                container = line.split("=", 1)[1].strip() or "none"
                break

    if container != "none":
        layer = "container"
        kind: str | None = container
    elif vm != "none":
        layer = "vm"
        kind = vm
    else:
        layer = "bare-metal"
        kind = None

    apparmor = _read_text("/proc/self/attr/current").strip() or "unknown"
    sys_vendor = _read_text("/sys/class/dmi/id/sys_vendor").strip()
    product_name = _read_text("/sys/class/dmi/id/product_name").strip()
    return {
        "layer": layer,
        "kind": kind,
        "apparmor": apparmor,
        "sys_vendor": sys_vendor or None,
        "product_name": product_name or None,
    }


def _probe_cpu() -> dict[str, Any]:
    cpuinfo = _read_text("/proc/cpuinfo")
    model = ""
    for line in cpuinfo.splitlines():
        if line.startswith("model name"):
            model = line.split(":", 1)[1].strip()
            break
    strix_halo = "RYZEN AI MAX" in model.upper()
    logical_online = os.cpu_count() or 0
    return {
        "model": model,
        "strix_halo": strix_halo,
        "logical_online": logical_online,
    }


def _probe_ram() -> dict[str, Any]:
    meminfo = _read_text("/proc/meminfo")
    fields: dict[str, int] = {}
    for line in meminfo.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        rest = rest.strip()
        if not rest.endswith(" kB"):
            continue
        try:
            fields[key.strip()] = int(rest.split()[0]) * 1024
        except ValueError:
            continue
    return {
        "total_bytes": fields.get("MemTotal", 0),
        "available_bytes": fields.get("MemAvailable", 0),
        "free_bytes": fields.get("MemFree", 0),
        "swap_total_bytes": fields.get("SwapTotal", 0),
    }


def _probe_gpu() -> dict[str, Any]:
    drm_root = Path("/sys/class/drm")
    pci_id: str | None = None
    driver: str | None = None
    if drm_root.exists():
        for card in sorted(drm_root.glob("card[0-9]*")):
            uevent = _read_text(card / "device" / "uevent")
            local_pci_id: str | None = None
            local_driver: str | None = None
            for line in uevent.splitlines():
                if line.startswith("PCI_ID="):
                    local_pci_id = line.split("=", 1)[1].strip()
                elif line.startswith("DRIVER="):
                    local_driver = line.split("=", 1)[1].strip()
            if local_driver == "amdgpu":
                pci_id = local_pci_id
                driver = local_driver
                break
    gfx = gpu_target_version()
    return {
        "pci_id": pci_id,
        "driver": driver,
        "gfx": gfx.get("gfx"),
        "kfd_present": gfx.get("present", False),
    }


def _probe_network() -> dict[str, Any]:
    # TCP-poke localhost slot, plus internet-egress sanity. Stdlib only
    # — no `ping` (CAP_NET_RAW gated inside many containers).
    def _open(host: str, port: int, timeout: float = 1.5) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except (TimeoutError, OSError):
            return False

    return {
        "hal0_api": _open("127.0.0.1", 8080),
        "primary_slot": _open("127.0.0.1", 8000),
    }


def _probe_tooling() -> dict[str, Any]:
    return {name: shutil.which(name) for name in ("docker", "podman", "flm", "python3", "uv")}


def env_report() -> dict[str, Any]:
    """Aggregate container / CPU / RAM / GPU / NPU / network / tooling.

    Returns the dict form of :class:`EnvReport`; every section is
    independent so a partial failure (NPU absent on a non-Strix host)
    keeps the rest of the report populated.
    """
    report = EnvReport(
        container=_probe_container(),
        cpu=_probe_cpu(),
        ram=_probe_ram(),
        gpu=_probe_gpu(),
        npu=npu_status(),
        network=_probe_network(),
        tooling=_probe_tooling(),
    )
    return asdict(report)


# ── Dispatcher ───────────────────────────────────────────────────────────────


async def dispatch_probe(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a probe tool by name. Async so :mod:`admin.dispatch` can
    treat probes uniformly with the existing memory_dispatcher path."""
    if tool == "gpu_target_version":
        return gpu_target_version()
    if tool == "npu_status":
        return npu_status()
    if tool == "env_report":
        return env_report()
    if tool == "model_store_probe":
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return {
                "status": "error",
                "error": {"code": "mcp.missing_arg", "detail": "path"},
            }
        return model_store_probe(path)
    return {"status": "error", "error": {"code": "mcp.unknown_probe", "tool": tool}}


# Type alias matching admin.build_server's dispatcher hook shape.
ProbeDispatcher = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
