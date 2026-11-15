"""Host-readiness probes for the XDNA NPU (ADR-0057). Fixtures are real values
read from a Strix Halo box (fw_version, /proc/cmdline, libxrt file names, a held
fabric clock)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.hardware import npu_host as nh
from hal0.providers.container import XDNA_ULIMITS

REAL_CMDLINE = (
    "initrd=\\EFI\\proxmox\\7.0.14-26-pve\\initrd.img-7.0.14-26-pve root=ZFS=rpool/ROOT/pve-1 "
    "boot=zfs quiet iommu=pt amd_iommu=on amd_pstate=active amdgpu.gttsize=86016"
)


def test_version_at_least() -> None:
    assert nh.version_at_least("1.1.2.65", "1.1.2.65") is True
    assert nh.version_at_least("1.1.2.64", "1.1.2.65") is False
    assert nh.version_at_least("1.1.10.1", "1.1.2.65") is True
    assert nh.version_at_least("2.25", "2.25.0") is True
    assert nh.version_at_least("2.24.9", "2.25.0") is False
    assert nh.version_at_least("garbage", "2.25.0") is None


@pytest.mark.parametrize(
    ("text", "want"),
    [("1.1.2.65\n", "1.1.2.65"), ("1.1.2.65", "1.1.2.65"), ("", None), (None, None), ("n/a", None)],
)
def test_parse_firmware_version(text: str | None, want: str | None) -> None:
    assert nh.parse_firmware_version(text) == want


def test_read_firmware_version_from_sysfs(tmp_path: Path) -> None:
    dev = tmp_path / "class" / "accel" / "accel0" / "device"
    dev.mkdir(parents=True)
    (dev / "fw_version").write_text("1.1.2.65\n")
    assert nh.read_firmware_version(tmp_path) == "1.1.2.65"
    assert nh.read_firmware_version(tmp_path / "nowhere") is None


def test_cmdline_tokens() -> None:
    assert nh.missing_cmdline_tokens(REAL_CMDLINE) == []
    assert nh.missing_cmdline_tokens("quiet iommu=pt") == ["amd_iommu=on"]
    assert nh.missing_cmdline_tokens("quiet") == ["iommu=pt", "amd_iommu=on"]
    # whole-token match only
    assert nh.missing_cmdline_tokens("noiommu=pt amd_iommu=on") == ["iommu=pt"]
    assert nh.missing_cmdline_tokens(None) is None
    assert nh.missing_cmdline_tokens("  ") is None


def test_read_missing_cmdline_tokens(tmp_path: Path) -> None:
    (tmp_path / "cmdline").write_text(REAL_CMDLINE + "\n")
    assert nh.read_missing_cmdline_tokens(tmp_path) == []
    assert nh.read_missing_cmdline_tokens(tmp_path / "nowhere") is None


def test_parse_xrt_library_version() -> None:
    assert nh.parse_xrt_library_version("libxrt_coreutil.so.2.25.00") == "2.25.0"
    assert nh.parse_xrt_library_version("libxrt_coreutil.so.2.25.10") == "2.25.10"
    assert nh.parse_xrt_library_version("libxrt_coreutil.so.2") is None
    assert nh.parse_xrt_library_version("libxrt++.so.2.25.00") is None


def test_read_xrt_version_picks_highest(tmp_path: Path) -> None:
    (tmp_path / "libxrt_coreutil.so.2.25.00").write_text("")
    (tmp_path / "libxrt_coreutil.so.2.24.01").write_text("")
    (tmp_path / "libxrt_coreutil.so.2").write_text("")
    assert nh.read_xrt_version([tmp_path / "missing", tmp_path]) == "2.25.0"
    assert nh.read_xrt_version([tmp_path / "missing"]) is None


def _fabric_tree(root: Path, perf: str, fclk: str) -> None:
    dev = root / "class" / "drm" / "card1" / "device"
    dev.mkdir(parents=True)
    (dev / "power_dpm_force_performance_level").write_text(perf + "\n")
    (dev / "pp_dpm_fclk").write_text(fclk)


def test_host_readiness_all_ok(tmp_path: Path) -> None:
    sys_root, proc_root, lib = tmp_path / "sys", tmp_path / "proc", tmp_path / "lib"
    acc = sys_root / "class" / "accel" / "accel0" / "device"
    acc.mkdir(parents=True)
    (acc / "fw_version").write_text("1.1.2.65\n")
    proc_root.mkdir()
    (proc_root / "cmdline").write_text(REAL_CMDLINE)
    lib.mkdir()
    (lib / "libxrt_coreutil.so.2.25.00").write_text("")
    _fabric_tree(sys_root, "high", "0: 400Mhz \n1: 1000Mhz \n7: 2000Mhz *\n")
    got = nh.host_readiness(sys_root=sys_root, proc_root=proc_root, lib_dirs=[lib])
    assert got["firmware_version"] == "1.1.2.65"
    assert got["firmware_ok"] is True
    assert got["cmdline_missing"] == []
    assert got["xrt_version"] == "2.25.0"
    assert got["xrt_ok"] is True
    assert got["memlock"] == "unlimited"
    assert got["fclk_held"] is True
    assert got["fclk_perf_level"] == "high"


def test_host_readiness_unreadable_is_none_not_fabricated(tmp_path: Path) -> None:
    got = nh.host_readiness(
        sys_root=tmp_path / "s", proc_root=tmp_path / "p", lib_dirs=[tmp_path / "l"]
    )
    for key in (
        "firmware_version",
        "firmware_ok",
        "cmdline_missing",
        "xrt_version",
        "xrt_ok",
        "fclk_held",
        "fclk_perf_level",
    ):
        assert got[key] is None, key


def test_host_readiness_old_firmware_and_unheld_clock(tmp_path: Path) -> None:
    sys_root = tmp_path / "sys"
    acc = sys_root / "class" / "accel" / "accel0" / "device"
    acc.mkdir(parents=True)
    (acc / "fw_version").write_text("1.0.0.63\n")
    _fabric_tree(sys_root, "auto", "0: 400Mhz *\n1: 2000Mhz \n")
    got = nh.host_readiness(sys_root=sys_root, proc_root=tmp_path / "p", lib_dirs=[])
    assert got["firmware_ok"] is False
    assert got["fclk_held"] is False
    assert got["fclk_perf_level"] == "auto"


def test_memlock_comes_from_the_launch_constant() -> None:
    assert "memlock=-1" in XDNA_ULIMITS
