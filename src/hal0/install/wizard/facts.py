"""Facts about the box: a small JSON file written by another program, plus
what the standard library can read itself (mounted disks, total memory).

Anything the file does not say is unknown (``None``), never guessed.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

FACTS_VERSION = 1
GPU_OUTCOMES = frozenset({"usable", "cpu_only_needs_consent", "stop"})
INSTALL_STATES = frozenset({"fresh", "upgrade", "unfinished"})
PSEUDO_FILESYSTEMS = frozenset(
    {
        "proc",
        "sysfs",
        "tmpfs",
        "devtmpfs",
        "devpts",
        "cgroup",
        "cgroup2",
        "overlay",
        "squashfs",
        "autofs",
        "mqueue",
        "debugfs",
        "tracefs",
        "securityfs",
        "pstore",
        "bpf",
        "fusectl",
        "configfs",
        "ramfs",
        "hugetlbfs",
        "rpc_pipefs",
        "nsfs",
        "binfmt_misc",
        "efivarfs",
        "selinuxfs",
        "nfsd",
        "sunrpc",
        "fuse",
    }
)
# Never probed: a dead server can block statvfs for good.
NETWORK_FILESYSTEMS = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smb3",
        "smbfs",
        "ceph",
        "glusterfs",
        "9p",
        "afs",
        "lustre",
        "davfs",
        "ncpfs",
        "ocfs2",
        "gfs2",
    }
)
_SYSTEM_MOUNTS = ("/proc", "/sys", "/dev", "/run", "/boot", "/snap")
_GIB = 2**30


class FactsError(ValueError):
    """The facts file cannot be used. The message is one line."""


@dataclass(frozen=True)
class Disk:
    mount: str
    free_gb: float
    total_gb: float


@dataclass(frozen=True)
class Facts:
    container: str | None
    podman_present: bool | None
    podman_runs: bool | None
    gpu_outcome: str
    gpu_reason: str | None
    gpu_vendor: str | None
    gpu_pool_gb: float | None
    npu_device: bool | None
    npu_flm_build: bool | None
    memory_python: bool | None
    install_state: str
    install_version: str | None
    disks: tuple[Disk, ...]
    ram_gb: float | None


def _section(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise FactsError(f"facts: {key} must be an object")
    return value


def _opt(section: Mapping[str, Any], name: str, key: str, kind: type) -> Any:
    value = section.get(key)
    if value is None:
        return None
    # bool is an int subclass: keep a number from passing as a flag and back.
    if not isinstance(value, kind) or (kind is not bool and isinstance(value, bool)):
        raise FactsError(f"facts: {name} has the wrong type")
    return value


def _number(section: Mapping[str, Any], name: str, key: str) -> float | None:
    value = section.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FactsError(f"facts: {name} has the wrong type")
    if not math.isfinite(value):
        raise FactsError(f"facts: {name} must be a finite number")
    return float(value)


def _choice(
    section: Mapping[str, Any], name: str, key: str, allowed: frozenset[str], default: str
) -> str:
    value = section.get(key)
    if value is None:
        return default
    if not isinstance(value, str) or value not in allowed:
        raise FactsError(f"facts: {name} {value!r} is not one of {', '.join(sorted(allowed))}")
    return value


def _read(path: Path) -> Mapping[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FactsError(f"facts file {path} not found") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise FactsError(f"facts file {path} cannot be read: {exc.__class__.__name__}") from None
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        raise FactsError(f"facts file {path} is not JSON") from None
    if not isinstance(data, dict):
        raise FactsError(f"facts file {path} is not a JSON object")
    return data


def _decode_mount_field(field: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def _worth_probing(fstype: str, mount: str, options: str) -> bool:
    """Whether a mount may be a local, writable disk; decided without touching it."""
    if fstype in PSEUDO_FILESYSTEMS or fstype.startswith("fuse."):
        return False
    if fstype in NETWORK_FILESYSTEMS:
        return False
    if any(mount == top or mount.startswith(top + "/") for top in _SYSTEM_MOUNTS):
        return False
    return "rw" in options.split(",")


def _same_filesystem(a: os.statvfs_result, b: os.statvfs_result) -> bool:
    fsid_a, fsid_b = getattr(a, "f_fsid", 0), getattr(b, "f_fsid", 0)
    if fsid_a and fsid_b and fsid_a == fsid_b:
        return True
    return (a.f_blocks * a.f_frsize, a.f_bavail * a.f_frsize) == (
        b.f_blocks * b.f_frsize,
        b.f_bavail * b.f_frsize,
    )


def _lies_under(mount: str, parent: str) -> bool:
    """True when `mount` is `parent` or inside it, decided on path components."""
    parent_parts = PurePosixPath(parent).parts
    return PurePosixPath(mount).parts[: len(parent_parts)] == parent_parts


def _same_pool(mount: str, st: os.statvfs_result, other: str, other_st: os.statvfs_result) -> bool:
    if _same_filesystem(st, other_st):
        return True
    free = st.f_bavail * st.f_frsize
    return free == other_st.f_bavail * other_st.f_frsize and (
        _lies_under(mount, other) or _lies_under(other, mount)
    )


def local_disks(mounts_text: str, statvfs: Callable[[str], os.statvfs_result]) -> tuple[Disk, ...]:
    """Local writable filesystems, one row each (its shortest mount), most free first.

    The root is always kept. Pseudo, network and system mounts and read-only
    ones are skipped without calling ``statvfs``; a filesystem under 1 GiB is left out.
    """
    mounts: list[str] = []
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mount = _decode_mount_field(parts[1])
        options = parts[3] if len(parts) > 3 else ""
        if mount == "/" or _worth_probing(parts[2], mount, options):
            mounts.append(mount)
    kept: list[tuple[str, os.statvfs_result]] = []
    for mount in sorted(set(mounts), key=lambda m: (len(m), m)):
        try:
            st = statvfs(mount)
        except OSError:
            continue
        if mount != "/" and st.f_blocks * st.f_frsize < _GIB:
            continue
        if any(_same_pool(mount, st, other, other_st) for other, other_st in kept):
            continue
        kept.append((mount, st))
    disks = [
        Disk(mount, st.f_bavail * st.f_frsize / _GIB, st.f_blocks * st.f_frsize / _GIB)
        for mount, st in kept
    ]
    return tuple(sorted(disks, key=lambda d: d.free_gb, reverse=True))


def total_ram_gb(meminfo_text: str) -> float | None:
    match = re.search(r"^MemTotal:\s+(\d+)\s*kB", meminfo_text, re.MULTILINE)
    return int(match.group(1)) * 1024 / _GIB if match else None


def _system_disks() -> tuple[Disk, ...]:
    try:
        text = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    return local_disks(text, os.statvfs)


def _system_ram_gb() -> float | None:
    try:
        return total_ram_gb(Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None


def load_facts(
    path: Path | None,
    *,
    disks: Sequence[Disk] | None = None,
    ram_gb: float | None = None,
) -> Facts:
    data: Mapping[str, Any] = {} if path is None else _read(path)
    version = data.get("version")
    if version is not None and version != FACTS_VERSION:
        raise FactsError(f"facts: version {version} is not supported (expected {FACTS_VERSION})")
    podman = _section(data, "podman")
    gpu = _section(data, "gpu")
    npu = _section(data, "npu")
    install = _section(data, "install")
    return Facts(
        container=_opt(data, "container", "container", str),
        podman_present=_opt(podman, "podman.present", "present", bool),
        podman_runs=_opt(podman, "podman.runs", "runs", bool),
        gpu_outcome=_choice(gpu, "gpu.outcome", "outcome", GPU_OUTCOMES, "usable"),
        gpu_reason=_opt(gpu, "gpu.reason", "reason", str),
        gpu_vendor=_opt(gpu, "gpu.vendor", "vendor", str),
        gpu_pool_gb=_number(gpu, "gpu.pool_gb", "pool_gb"),
        npu_device=_opt(npu, "npu.device", "device", bool),
        npu_flm_build=_opt(npu, "npu.flm_build", "flm_build", bool),
        memory_python=_opt(data, "memory_python", "memory_python", bool),
        install_state=_choice(install, "install.state", "state", INSTALL_STATES, "fresh"),
        install_version=_opt(install, "install.version", "version", str),
        disks=tuple(_system_disks() if disks is None else disks),
        ram_gb=_system_ram_gb() if ram_gb is None else ram_gb,
    )


def satisfied(need: str, facts: Facts) -> bool | None:
    """Whether a component's need holds on this box; ``None`` means unknown."""
    if need == "podman":
        # No podman yet means the installer will put it there.
        return True if facts.podman_present is False else facts.podman_runs
    if need == "gpu":
        return facts.gpu_outcome == "usable"
    if need == "npu_device":
        return facts.npu_device
    if need == "flm_build":
        return facts.npu_flm_build
    if need == "memory_python":
        return facts.memory_python
    return None
