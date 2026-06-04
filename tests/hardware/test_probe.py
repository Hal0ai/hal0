"""Unit tests for hal0.hardware.probe.

Covers:
  - CPU parsing from /proc/cpuinfo
  - RAM parsing from /proc/meminfo
  - GPU detection via nvidia / amd / vulkan / lspci fallbacks (each mocked)
  - NPU detection (XDNA presence)
  - Disk free space
  - Atomic write to hardware.json

The probe is intentionally subprocess+sysfs-heavy; we mock both at module
boundaries so the suite is hermetic.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hal0.hardware import probe as probe_mod
from hal0.hardware.probe import (
    GPUInfo,
    HardwareInfo,
    HardwareProbe,
)

# ── /proc parsing ──────────────────────────────────────────────────────────────


_CPUINFO_SAMPLE = """\
processor	: 0
model name	: AMD Ryzen AI Max+ PRO 395 w/ Radeon 8060S
physical id	: 0
cpu cores	: 16
processor	: 1
model name	: AMD Ryzen AI Max+ PRO 395 w/ Radeon 8060S
physical id	: 0
cpu cores	: 16
processor	: 2
model name	: AMD Ryzen AI Max+ PRO 395 w/ Radeon 8060S
physical id	: 0
cpu cores	: 16
"""


def test_parse_cpuinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_read_text(path: Path) -> str | None:
        if str(path) == "/proc/cpuinfo":
            return _CPUINFO_SAMPLE
        return None

    monkeypatch.setattr(probe_mod, "_read_text", fake_read_text)
    model, cores, threads = probe_mod._parse_cpuinfo()
    assert model.startswith("AMD Ryzen AI Max+")
    assert cores == 16
    assert threads == 3


_MEMINFO_SAMPLE = """\
MemTotal:       131000000 kB
MemFree:         10000000 kB
MemAvailable:    50000000 kB
"""


def test_parse_meminfo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        probe_mod,
        "_read_text",
        lambda p: _MEMINFO_SAMPLE if str(p) == "/proc/meminfo" else None,
    )
    total, avail = probe_mod._parse_meminfo()
    # 131000000 kB → 127929 MiB ; 50000000 kB → 48828 MiB (integer MiB floor-divided)
    assert total == 127929
    assert avail == 48828


def test_parse_meminfo_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod, "_read_text", lambda _: None)
    assert probe_mod._parse_meminfo() == (0, 0)


# ── host identity (hostname / uptime / kernel / distro) ─────────────────────────


def test_read_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        probe_mod,
        "_read_text",
        lambda p: "hal0\n" if str(p) == "/proc/sys/kernel/hostname" else None,
    )
    assert probe_mod._read_hostname() == "hal0"


def test_read_hostname_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod, "_read_text", lambda _: None)
    assert probe_mod._read_hostname() == ""


def test_read_uptime_s(monkeypatch: pytest.MonkeyPatch) -> None:
    # /proc/uptime: "<uptime_seconds> <idle_seconds>"
    monkeypatch.setattr(
        probe_mod,
        "_read_text",
        lambda p: "123456.78 987654.32\n" if str(p) == "/proc/uptime" else None,
    )
    assert probe_mod._read_uptime_s() == 123456


def test_read_uptime_s_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod, "_read_text", lambda _: None)
    assert probe_mod._read_uptime_s() == 0


_OS_RELEASE_SAMPLE = """\
NAME="Debian GNU/Linux"
VERSION_ID="13"
VERSION="13 (trixie)"
PRETTY_NAME="Debian GNU/Linux 13 (trixie)"
ID=debian
"""


def test_read_distro(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        probe_mod,
        "_read_text",
        lambda p: _OS_RELEASE_SAMPLE if str(p) == "/etc/os-release" else None,
    )
    assert probe_mod._read_distro() == "Debian GNU/Linux 13 (trixie)"


def test_read_distro_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod, "_read_text", lambda _: None)
    assert probe_mod._read_distro() == ""


# ── GPU detection ──────────────────────────────────────────────────────────────


def _mk_run(table: dict[str, tuple[int, str, str]]):
    """Build a fake _run() that dispatches by the first arg of cmd."""

    def fake_run(cmd: list[str], timeout: float = 4.0) -> tuple[int, str, str]:
        bin_ = cmd[0]
        return table.get(bin_, (-1, "", "not mocked"))

    return fake_run


def test_detect_nvidia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        probe_mod,
        "_run",
        _mk_run({"nvidia-smi": (0, "GeForce RTX 4080, 16376, 535.171.04\n", "")}),
    )
    info = probe_mod._detect_nvidia()
    assert info is not None
    assert info.vendor == "nvidia"
    assert info.name == "GeForce RTX 4080"
    assert info.vram_mb == 16376.0
    assert info.driver.startswith("nvidia 535")
    assert info.compute_capable is True


def test_detect_nvidia_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod, "_run", _mk_run({}))
    assert probe_mod._detect_nvidia() is None


def test_detect_amd_via_drm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    drm = tmp_path / "drm" / "card1" / "device"
    drm.mkdir(parents=True)
    (drm / "mem_info_vram_total").write_text(str(512 * 1024 * 1024))  # 512 MiB carve-out
    (drm / "mem_info_gtt_total").write_text(str(96 * 1024 * 1024 * 1024))  # 96 GiB pool
    (drm / "uevent").write_text("PCI_SLOT_NAME=0000:c5:00.0\n")

    monkeypatch.setattr(probe_mod, "_amd_drm_device", lambda: drm)
    monkeypatch.setattr(
        probe_mod,
        "_run",
        _mk_run(
            {
                "lspci": (0, "c5:00.0 VGA controller: AMD Radeon Graphics [Radeon 890M]", ""),
                "rocm-smi": (0, "GPU[0] : AMD Radeon Graphics", ""),
                "nvidia-smi": (-1, "", "not found"),
            }
        ),
    )
    info = probe_mod._detect_amd()
    assert info is not None
    assert info.vendor == "amd"
    assert "Radeon" in info.name or "AMD" in info.name
    # max(vram_total, gtt_total) wins — the UMA pool
    assert info.vram_mb == pytest.approx(96 * 1024, rel=0.01)
    assert info.compute_capable is True
    assert info.vulkan_capable is True
    assert info.drm_path == str(drm)


def test_detect_amd_no_drm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod, "_amd_drm_device", lambda: None)
    assert probe_mod._detect_amd() is None


def test_detect_vulkan_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        probe_mod,
        "_run",
        _mk_run(
            {
                "vulkaninfo": (
                    0,
                    "Vulkan Instance ...\n\tdeviceName = Intel Iris Xe Graphics\n",
                    "",
                )
            }
        ),
    )
    info = probe_mod._detect_vulkan_fallback()
    assert info is not None
    assert info.vendor == "intel"
    assert info.name == "Intel Iris Xe Graphics"
    assert info.vulkan_capable is True


def test_detect_lspci_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        probe_mod,
        "_run",
        _mk_run(
            {
                "lspci": (
                    0,
                    "01:00.0 VGA compatible controller: NVIDIA Corporation [Foo] (rev a1)",
                    "",
                )
            }
        ),
    )
    info = probe_mod._detect_lspci_fallback()
    assert info is not None
    assert info.vendor == "nvidia"


def test_detect_gpu_cpu_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """No nvidia / no amd / no vulkan / no lspci → vendor=unknown empty info."""
    monkeypatch.setattr(probe_mod, "_detect_nvidia", lambda: None)
    monkeypatch.setattr(probe_mod, "_detect_amd", lambda: None)
    monkeypatch.setattr(probe_mod, "_detect_vulkan_fallback", lambda: None)
    monkeypatch.setattr(probe_mod, "_detect_lspci_fallback", lambda: None)
    info = probe_mod._detect_gpu()
    assert isinstance(info, GPUInfo)
    assert info.vendor == "unknown"
    assert info.vram_mb == 0
    assert info.compute_capable is False


def test_detect_gpu_first_match_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """If nvidia returns a value, amd/vulkan/lspci are never called."""
    called: list[str] = []

    def amd() -> None:
        called.append("amd")
        return None

    monkeypatch.setattr(probe_mod, "_detect_nvidia", lambda: GPUInfo(vendor="nvidia", name="X"))
    monkeypatch.setattr(probe_mod, "_detect_amd", amd)
    info = probe_mod._detect_gpu()
    assert info.vendor == "nvidia"
    assert called == []


# ── NPU detection ──────────────────────────────────────────────────────────────


def test_detect_npu_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    accel = tmp_path / "accel"
    accel.mkdir()
    (accel / "accel0").touch()

    real_path_cls = probe_mod.Path

    class FakePath:
        def __new__(cls, *args: Any, **kwargs: Any):
            inst = real_path_cls(*args, **kwargs)
            if str(inst) == "/dev/accel":
                return accel
            return inst

    monkeypatch.setattr(probe_mod, "Path", FakePath)
    info = probe_mod._detect_npu()
    assert info.present is True
    assert info.vendor == "amd"
    assert info.driver == "amdxdna"


def test_detect_npu_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    real_path_cls = probe_mod.Path

    class FakePath:
        def __new__(cls, *args: Any, **kwargs: Any):
            inst = real_path_cls(*args, **kwargs)
            if str(inst) in ("/dev/accel", "/sys/module/amdxdna"):
                return tmp_path / "definitely-not-here"
            return inst

    monkeypatch.setattr(probe_mod, "Path", FakePath)
    info = probe_mod._detect_npu()
    assert info.present is False


# ── Disk ───────────────────────────────────────────────────────────────────────


def test_disk_free_mb(tmp_path: Path) -> None:
    free = probe_mod._disk_free_mb(tmp_path)
    assert free > 0  # any real filesystem will have some free


def test_disk_free_mb_nonexistent(tmp_path: Path) -> None:
    """Walks up to the first existing parent."""
    free = probe_mod._disk_free_mb(tmp_path / "does" / "not" / "exist")
    assert free > 0


# ── End-to-end probe() ─────────────────────────────────────────────────────────


def test_probe_assembles_hardware_info(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(probe_mod, "_parse_cpuinfo", lambda: ("Test CPU", 8, 16))
    monkeypatch.setattr(probe_mod, "_parse_meminfo", lambda: (32768, 24576))
    monkeypatch.setattr(
        probe_mod,
        "_detect_gpu",
        lambda: GPUInfo(vendor="nvidia", name="RTX 4080", vram_mb=16000),
    )
    monkeypatch.setattr(
        probe_mod,
        "_detect_npu",
        lambda: probe_mod.NPUInfo(present=False),
    )
    monkeypatch.setattr(probe_mod, "_disk_free_mb", lambda _: 512000)

    def fake_read_text(path: Path) -> str | None:
        s = str(path)
        if s == "/proc/version":
            return "Linux version 7.0.6-2-pve (build@host) #1 SMP\n"
        if s == "/proc/sys/kernel/hostname":
            return "hal0\n"
        if s == "/proc/uptime":
            return "4242.0 9000.0\n"
        if s == "/etc/os-release":
            return _OS_RELEASE_SAMPLE
        return None

    monkeypatch.setattr(probe_mod, "_read_text", fake_read_text)

    info = HardwareProbe().probe()
    assert isinstance(info, HardwareInfo)
    assert info.cpu_model == "Test CPU"
    assert info.cpu_cores == 8
    assert info.cpu_threads == 16
    assert info.ram_mb == 32768
    assert info.ram_available_mb == 24576
    assert len(info.gpus) == 1
    assert info.gpus[0].vendor == "nvidia"
    assert info.gpus[0].vram_mb == 16000
    assert info.npu.present is False
    assert info.disk_free_mb == 512000
    assert info.probed_at  # ISO-8601 timestamp populated
    # Host identity (added for the dashboard hardware cards).
    assert info.hostname == "hal0"
    assert info.uptime_s == 4242
    assert info.kernel == "Linux version 7.0.6-2-pve"
    assert info.distro == "Debian GNU/Linux 13 (trixie)"


_DMIDECODE_SAMPLE = """\
# dmidecode 3.5
Getting SMBIOS data from sysfs.
SMBIOS 3.7.0 present.

Handle 0x0030, DMI type 16, 23 bytes
Physical Memory Array
\tLocation: System Board Or Motherboard
\tNumber Of Devices: 8

Handle 0x0033, DMI type 17, 100 bytes
Memory Device
\tArray Handle: 0x0030
\tSize: 16 GB
\tForm Factor: Other
\tLocator: DIMM 0

Handle 0x0034, DMI type 17, 100 bytes
Memory Device
\tArray Handle: 0x0030
\tSize: 16 GB
\tLocator: DIMM 1

Handle 0x0035, DMI type 17, 100 bytes
Memory Device
\tArray Handle: 0x0030
\tSize: No Module Installed
\tLocator: DIMM 2
"""


def test_dmidecode_host_ram_mb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod, "_run", lambda *_a, **_k: (0, _DMIDECODE_SAMPLE, ""))
    assert probe_mod._dmidecode_host_ram_mb() == 32 * 1024


def test_dmidecode_host_ram_mb_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod, "_run", lambda *_a, **_k: (-1, "", "binary not found"))
    assert probe_mod._dmidecode_host_ram_mb() is None


def test_derive_unified_uses_dmidecode_when_cgroup_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # LXC reports 64 GiB in /proc/meminfo, real host has 128 GiB DIMMs.
    monkeypatch.setattr(probe_mod, "_dmidecode_host_ram_mb", lambda: 128 * 1024)
    unified = probe_mod._derive_unified_memory_mb(64 * 1024, None)
    assert unified == 128 * 1024


def test_derive_unified_falls_back_to_meminfo_on_bare_metal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Bare metal: /proc/meminfo and dmidecode roughly agree → trust meminfo.
    monkeypatch.setattr(probe_mod, "_dmidecode_host_ram_mb", lambda: 32500)
    unified = probe_mod._derive_unified_memory_mb(32000, None)
    assert unified == 32000


def test_probe_assembles_unified_memory_on_uma(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod, "_parse_cpuinfo", lambda: ("Strix Halo", 16, 32))
    # LXC sees a 64 GiB cgroup but real host has 128 GiB of DIMMs.
    monkeypatch.setattr(probe_mod, "_parse_meminfo", lambda: (64 * 1024, 60 * 1024))
    monkeypatch.setattr(probe_mod, "_dmidecode_host_ram_mb", lambda: 128 * 1024)
    monkeypatch.setattr(
        probe_mod,
        "_detect_gpu",
        lambda: GPUInfo(vendor="amd", name="Radeon 8060S", vram_mb=105 * 1024),
    )
    monkeypatch.setattr(probe_mod, "_detect_npu", lambda: probe_mod.NPUInfo(present=False))
    monkeypatch.setattr(probe_mod, "_disk_free_mb", lambda _: 1024)
    monkeypatch.setattr(probe_mod, "_read_text", lambda _: None)

    info = HardwareProbe().probe()
    assert info.unified_memory_mb == 128 * 1024
    # Raw counters stay honest.
    assert info.ram_mb == 64 * 1024
    assert info.gpus[0].vram_mb == 105 * 1024


def test_probe_async(monkeypatch: pytest.MonkeyPatch) -> None:
    """probe_async returns the same value as probe() (just from a thread)."""
    monkeypatch.setattr(probe_mod, "_parse_cpuinfo", lambda: ("X", 1, 1))
    monkeypatch.setattr(probe_mod, "_parse_meminfo", lambda: (8192, 4096))
    monkeypatch.setattr(probe_mod, "_detect_gpu", lambda: GPUInfo(vendor="unknown"))
    monkeypatch.setattr(probe_mod, "_detect_npu", lambda: probe_mod.NPUInfo(present=False))
    monkeypatch.setattr(probe_mod, "_disk_free_mb", lambda _: 1024)
    monkeypatch.setattr(probe_mod, "_read_text", lambda _: None)

    import asyncio

    info = asyncio.run(HardwareProbe().probe_async())
    assert info.cpu_model == "X"


def test_write_atomic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """write() places hardware.json atomically and uses pydantic dump."""
    target = tmp_path / "etc" / "hal0" / "hardware.json"
    info = HardwareInfo(
        gpus=[GPUInfo(vendor="amd", name="Radeon")],
        ram_mb=65536,
        cpu_model="X",
        cpu_cores=4,
        cpu_threads=8,
    )
    HardwareProbe().write(info, target)
    assert target.exists()
    data = json.loads(target.read_text())
    assert data["gpus"][0]["vendor"] == "amd"
    assert data["ram_mb"] == 65536
    # tempfile must not be left behind
    assert not (target.with_suffix(target.suffix + ".tmp")).exists()


# ── _run() wrapper hygiene ─────────────────────────────────────────────────────


def test_run_handles_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any, **__: Any) -> Any:
        raise FileNotFoundError("nope")

    monkeypatch.setattr(probe_mod.subprocess, "run", boom)
    rc, out, err = probe_mod._run(["does-not-exist"])
    assert rc == -1
    assert out == ""
    assert "binary not found" in err


def test_run_handles_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=1.0)

    monkeypatch.setattr(probe_mod.subprocess, "run", boom)
    rc, _, err = probe_mod._run(["sleep", "60"], timeout=1.0)
    assert rc == -1
    assert "timeout" in err


# ── Platform detection ────────────────────────────────────────────────────────


def _stub_files(monkeypatch: pytest.MonkeyPatch, table: dict[str, str | None]) -> None:
    """Make ``probe._read_text(path)`` return canned content for ``table`` keys."""

    def fake(p: Path) -> str | None:
        return table.get(str(p))

    monkeypatch.setattr(probe_mod, "_read_text", fake)


def test_detect_platform_wsl_via_proc_version(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_files(
        monkeypatch,
        {
            "/proc/version": "Linux version 5.15.146.1-microsoft-standard-WSL2",
            "/proc/sys/kernel/osrelease": "5.15.146.1-microsoft-standard-WSL2",
            "/proc/1/cgroup": "0::/init.scope",
        },
    )
    gpu = GPUInfo(vendor="unknown")
    npu = probe_mod.NPUInfo(present=False)
    assert probe_mod._detect_platform(gpu, npu) == "wsl2"


def test_detect_platform_lxc(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_files(
        monkeypatch,
        {
            "/proc/version": "Linux version 6.6.0",
            "/proc/1/cgroup": "0::/lxc.payload.105",
            "/proc/self/cgroup": "0::/lxc.payload.105",
            "/proc/1/environ": "",
        },
    )
    gpu = GPUInfo(vendor="amd", name="Radeon")
    npu = probe_mod.NPUInfo(present=True, vendor="amd")
    # LXC short-circuits before strix-halo classification — but downstream
    # consumers can still see the NPU + AMD GPU on the HardwareInfo body.
    assert probe_mod._detect_platform(gpu, npu) == "lxc"


def test_detect_platform_kvm_via_dmi(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Stub DMI sysfs by routing _read_text on the DMI paths through a dict.
    files = {
        "/proc/version": "Linux version 6.6.0",
        "/proc/1/cgroup": "0::/init.scope",
        str(probe_mod._DMI_PATHS["product_name"]): "Standard PC (Q35 + ICH9, 2009)",
        str(probe_mod._DMI_PATHS["sys_vendor"]): "QEMU",
        str(probe_mod._DMI_PATHS["bios_vendor"]): "SeaBIOS",
        str(probe_mod._DMI_PATHS["board_vendor"]): "",
        "/proc/cmdline": "root=UUID=... ro quiet",  # no virtio hint
    }
    _stub_files(monkeypatch, files)
    monkeypatch.setattr(probe_mod, "_is_wsl", lambda: False)
    monkeypatch.setattr(probe_mod, "_is_lxc", lambda: False)
    gpu = GPUInfo(vendor="unknown")
    npu = probe_mod.NPUInfo(present=False)
    assert probe_mod._detect_platform(gpu, npu) == "kvm"


def test_detect_platform_proxmox_kvm_heuristic(monkeypatch: pytest.MonkeyPatch) -> None:
    files = {
        "/proc/version": "Linux version 6.6.0",
        "/proc/1/cgroup": "0::/init.scope",
        str(probe_mod._DMI_PATHS["product_name"]): "Standard PC (Q35 + ICH9, 2009)",
        str(probe_mod._DMI_PATHS["sys_vendor"]): "QEMU",
        str(probe_mod._DMI_PATHS["bios_vendor"]): "SeaBIOS",
        str(probe_mod._DMI_PATHS["board_vendor"]): "",
        "/proc/cmdline": "root=UUID=... ro quiet virtio_blk",
    }
    _stub_files(monkeypatch, files)
    monkeypatch.setattr(probe_mod, "_is_wsl", lambda: False)
    monkeypatch.setattr(probe_mod, "_is_lxc", lambda: False)
    gpu = GPUInfo(vendor="unknown")
    npu = probe_mod.NPUInfo(present=False)
    assert probe_mod._detect_platform(gpu, npu) == "proxmox-kvm"


def test_detect_platform_bare_metal_nvidia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod, "_is_wsl", lambda: False)
    monkeypatch.setattr(probe_mod, "_is_lxc", lambda: False)
    monkeypatch.setattr(probe_mod, "_read_dmi", lambda: {})  # bare metal: real OEM
    gpu = GPUInfo(vendor="nvidia", name="GeForce RTX 4080")
    npu = probe_mod.NPUInfo(present=False)
    assert probe_mod._detect_platform(gpu, npu) == "bare-metal-nvidia-gpu"


def test_detect_platform_strix_halo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod, "_is_wsl", lambda: False)
    monkeypatch.setattr(probe_mod, "_is_lxc", lambda: False)
    monkeypatch.setattr(probe_mod, "_read_dmi", lambda: {})
    gpu = GPUInfo(vendor="amd", name="Radeon 8060S")
    npu = probe_mod.NPUInfo(present=True, vendor="amd", name="AMD NPU (XDNA)")
    assert probe_mod._detect_platform(gpu, npu) == "strix-halo"


def test_detect_platform_cpu_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod, "_is_wsl", lambda: False)
    monkeypatch.setattr(probe_mod, "_is_lxc", lambda: False)
    monkeypatch.setattr(probe_mod, "_read_dmi", lambda: {})
    gpu = GPUInfo(vendor="unknown")
    npu = probe_mod.NPUInfo(present=False)
    assert probe_mod._detect_platform(gpu, npu) == "bare-metal-cpu-only"


# ── End-to-end: probe() populates platform + survives a WSL-shaped host ───────


def test_probe_populates_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod, "_parse_cpuinfo", lambda: ("Intel WSL CPU", 4, 8))
    monkeypatch.setattr(probe_mod, "_parse_meminfo", lambda: (16384, 12000))
    monkeypatch.setattr(probe_mod, "_detect_gpu", lambda: GPUInfo(vendor="unknown"))
    monkeypatch.setattr(probe_mod, "_detect_npu", lambda: probe_mod.NPUInfo(present=False))
    monkeypatch.setattr(probe_mod, "_disk_free_mb", lambda _: 512000)
    monkeypatch.setattr(probe_mod, "_read_text", lambda _: None)
    monkeypatch.setattr(probe_mod, "_detect_platform", lambda *_: "wsl2")

    info = HardwareProbe().probe()
    # The whole point: CPU + RAM populate even when no GPU vendor matched.
    assert info.cpu_model == "Intel WSL CPU"
    assert info.ram_mb == 16384
    assert info.platform == "wsl2"


def test_probe_includes_named_gpu_even_when_vendor_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """lspci returns a model string but vendor matched none of nvidia/amd/intel
    (e.g. a virtio GPU in a Proxmox VM). The probe should still surface the
    name so the dashboard shows "Red Hat, Inc. Virtio GPU" instead of "—".
    """
    monkeypatch.setattr(probe_mod, "_parse_cpuinfo", lambda: ("x86 CPU", 4, 8))
    monkeypatch.setattr(probe_mod, "_parse_meminfo", lambda: (4096, 2048))
    monkeypatch.setattr(
        probe_mod,
        "_detect_gpu",
        lambda: GPUInfo(vendor="unknown", name="Red Hat, Inc. Virtio 1.0 GPU"),
    )
    monkeypatch.setattr(probe_mod, "_detect_npu", lambda: probe_mod.NPUInfo(present=False))
    monkeypatch.setattr(probe_mod, "_disk_free_mb", lambda _: 1024)
    monkeypatch.setattr(probe_mod, "_read_text", lambda _: None)
    monkeypatch.setattr(probe_mod, "_detect_platform", lambda *_: "kvm")

    info = HardwareProbe().probe()
    assert len(info.gpus) == 1
    assert info.gpus[0].name.startswith("Red Hat")


def test_parse_cpuinfo_arm_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """ARM /proc/cpuinfo has Hardware/Model fields instead of 'model name'."""
    arm_sample = (
        "processor\t: 0\n"
        "BogoMIPS\t: 108.00\n"
        "Hardware\t: BCM2835\n"
        "Model\t: Raspberry Pi 4 Model B Rev 1.5\n"
    )
    monkeypatch.setattr(
        probe_mod, "_read_text", lambda p: arm_sample if str(p) == "/proc/cpuinfo" else None
    )
    model, _cores, _threads = probe_mod._parse_cpuinfo()
    assert "Raspberry Pi" in model or "BCM2835" in model


def test_detect_lspci_fallback_handles_display_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``lspci -nnk`` failed (rc!=0) but bare ``lspci`` returned a Display
    controller line — e.g. a WSL2 vGPU. We should still surface a name.
    """
    calls = {"n": 0}

    def fake_run(cmd: list[str], timeout: float = 4.0) -> tuple[int, str, str]:
        calls["n"] += 1
        if cmd == ["lspci", "-nnk"]:
            return -1, "", "binary not found: lspci"
        if cmd == ["lspci"]:
            return 0, "0000:00:00.0 Display controller: Microsoft Corporation Virtual GPU", ""
        return -1, "", "not mocked"

    monkeypatch.setattr(probe_mod, "_run", fake_run)
    info = probe_mod._detect_lspci_fallback()
    assert info is not None
    assert "Microsoft" in info.name
