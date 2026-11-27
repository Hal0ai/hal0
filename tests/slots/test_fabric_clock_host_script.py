"""Host-side ``scripts/proxmox-ve/hal0-fabric-clock`` against a fake sysfs tree."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "proxmox-ve" / "hal0-fabric-clock"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")


def _tree(tmp_path: Path, fclk: str, perf: str = "auto") -> Path:
    dev = tmp_path / "class" / "drm" / "card1" / "device"
    dev.mkdir(parents=True)
    (dev / "pp_dpm_fclk").write_text(fclk)
    (dev / "power_dpm_force_performance_level").write_text(perf + "\n")
    return dev


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "HAL0_SYSFS": str(tmp_path)}
    return subprocess.run(
        ["sh", str(SCRIPT), *args], env=env, capture_output=True, text=True, check=False
    )


def test_hold_falls_back_to_high_when_firmware_omits_active_marker(tmp_path: Path) -> None:
    # Strix Halo on 7.0.14-pve: no `*` on any pp_dpm_fclk line, so a manual pin
    # can never be verified; the hold must land on `high` instead of failing.
    dev = _tree(tmp_path, "0: 400Mhz \n7: 2000Mhz \n")
    res = _run(tmp_path, "hold")
    assert res.returncode == 0, res.stderr
    assert "held via perf level high" in res.stdout
    assert (dev / "power_dpm_force_performance_level").read_text().strip() == "high"
    assert _run(tmp_path, "status").returncode == 0


def test_status_reports_not_held_on_auto(tmp_path: Path) -> None:
    _tree(tmp_path, "0: 400Mhz *\n7: 2000Mhz \n")
    res = _run(tmp_path, "status")
    assert res.returncode != 0
    assert "NOT held" in res.stdout + res.stderr


def test_hold_wait_picks_up_a_gpu_that_appears_late(tmp_path: Path) -> None:
    # Boot race seen on a Proxmox host: the unit ran before amdgpu had created
    # its sysfs controls, failed once and left the clock unheld until the next
    # boot. `--wait` must poll for the device instead of failing on first look.
    import threading

    timer = threading.Timer(1.0, _tree, args=(tmp_path, "0: 400Mhz \n7: 2000Mhz \n"))
    timer.start()
    try:
        res = _run(tmp_path, "hold", "--wait", "10")
    finally:
        timer.join()
    assert res.returncode == 0, res.stderr
    dev = tmp_path / "class" / "drm" / "card1" / "device"
    assert (dev / "power_dpm_force_performance_level").read_text().strip() == "high"


def test_hold_wait_gives_up_when_no_gpu_ever_appears(tmp_path: Path) -> None:
    res = _run(tmp_path, "hold", "--wait", "1")
    assert res.returncode == 1
    assert "no amdgpu device" in res.stderr
    assert "waited 1s" in res.stderr


def test_hold_without_wait_still_fails_at_once(tmp_path: Path) -> None:
    res = _run(tmp_path, "hold")
    assert res.returncode == 1
    assert "waited" not in res.stderr


@pytest.mark.parametrize("bad", ["", "abc", "-3"])
def test_hold_wait_rejects_a_non_numeric_value(tmp_path: Path, bad: str) -> None:
    res = _run(tmp_path, "hold", "--wait", bad)
    assert res.returncode == 2
    assert "usage" in res.stderr


def test_unit_waits_for_the_gpu_at_boot() -> None:
    unit = (SCRIPT.parent / "hal0-fabric-clock.service").read_text()
    assert "ExecStart=/usr/local/sbin/hal0-fabric-clock hold --wait " in unit
