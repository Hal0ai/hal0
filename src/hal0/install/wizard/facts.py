"""Facts about the box: a small JSON file written by another program, plus
what the standard library can read itself (mounted disks, total memory).

Anything the file does not say is unknown (``None``), never guessed.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
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
    }
)
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


def local_disks(mounts_text: str, statvfs: Callable[[str], os.statvfs_result]) -> tuple[Disk, ...]:
    """Real filesystems, one per device (its shortest mount), most free first."""
    chosen: dict[str, str] = {}
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[2] in PSEUDO_FILESYSTEMS:
            continue
        device, mount = parts[0], _decode_mount_field(parts[1])
        known = chosen.get(device)
        if known is None or len(mount) < len(known):
            chosen[device] = mount
    disks: list[Disk] = []
    for mount in chosen.values():
        try:
            st = statvfs(mount)
        except OSError:
            continue
        disks.append(
            Disk(mount, st.f_bavail * st.f_frsize / _GIB, st.f_blocks * st.f_frsize / _GIB)
        )
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
        return facts.podman_runs
    if need == "gpu":
        return facts.gpu_outcome == "usable"
    if need == "npu_device":
        return facts.npu_device
    if need == "flm_build":
        return facts.npu_flm_build
    if need == "memory_python":
        return facts.memory_python
    return None
