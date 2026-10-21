"""``hal0.system.linger``: read-only linger probe + best-effort disable (ADR-0034)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from hal0.system import linger


def _proc(rc: int, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], rc, out, err)


@pytest.mark.parametrize(("value", "enabled"), [("yes\n", True), ("no\n", False)])
def test_read_linger_uses_loginctl_show_user(value: str, enabled: bool) -> None:
    seen: list[list[str]] = []

    def run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return _proc(0, value)

    state = linger.read_linger(run=run, which=lambda _n: "/usr/bin/loginctl")
    assert state.enabled is enabled
    assert state.source == "loginctl"
    # Read-only: show-user, never enable/disable.
    assert seen == [["loginctl", "show-user", "hal0", "-p", "Linger", "--value"]]


def test_read_linger_falls_back_to_the_linger_dir(tmp_path: Path) -> None:
    # show-user errors for a user that is neither logged in nor lingering.
    (tmp_path / "hal0").touch()
    state = linger.read_linger(
        run=lambda *_a, **_k: _proc(1, err="User hal0 is not logged in or lingering."),
        which=lambda _n: "/usr/bin/loginctl",
        linger_dir=str(tmp_path),
    )
    assert state.enabled is True
    assert state.source == "linger-dir"
    off = linger.read_linger(user="nobody", which=lambda _n: None, linger_dir=str(tmp_path))
    assert off.enabled is False


def test_read_linger_unknown_without_systemd(tmp_path: Path) -> None:
    state = linger.read_linger(which=lambda _n: None, linger_dir=str(tmp_path / "absent"))
    assert state.enabled is None
    assert state.source == "unavailable"


def test_disable_linger_reports_failure_without_raising() -> None:
    ok, msg = linger.disable_linger(
        run=lambda *_a, **_k: _proc(1, err="Access denied"), which=lambda _n: "/usr/bin/loginctl"
    )
    assert (ok, msg) == (False, "Access denied")
    assert linger.disable_linger(which=lambda _n: None) == (False, "loginctl not found")


def test_disable_linger_runs_disable_for_hal0() -> None:
    seen: list[list[str]] = []

    def run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return _proc(0)

    ok, _ = linger.disable_linger(run=run, which=lambda _n: "/usr/bin/loginctl")
    assert ok is True
    assert seen == [["loginctl", "disable-linger", "hal0"]]


def test_uninstall_script_disables_hal0_linger() -> None:
    script = (Path(__file__).parents[2] / "installer" / "uninstall.sh").read_text()
    assert "loginctl disable-linger hal0" in script


def test_gateway_unit_regen_reason(tmp_path: Path) -> None:
    unit = tmp_path / "hermes-gateway.service"
    lingers = tmp_path / "linger"
    lingers.mkdir()
    kw = {"unit": str(unit), "linger_dir": str(lingers)}
    # No gateway installed: nothing to say.
    assert linger.gateway_unit_regen_reason(**kw) is None
    # The 0.19.0 generator's shape.
    unit.write_text("[Service]\nExecStart=/v/bin/python -m hermes_cli.main gateway run\n")
    reason = linger.gateway_unit_regen_reason(**kw)
    assert reason is not None and "predates Hermes 0.22" in reason
    # A 0.22 unit, but linger off.
    unit.write_text("[Unit]\nAfter=user@999.service\nWants=user@999.service\n")
    assert linger.gateway_unit_regen_reason(**kw) == "systemd linger is off for hal0"
    # Both right.
    (lingers / "hal0").touch()
    assert linger.gateway_unit_regen_reason(**kw) is None
