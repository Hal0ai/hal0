"""OpenWebUI converge arm: pull-first repin with override marker."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hal0.components import openwebui_arm
from hal0.openwebui.image_pin import OPENWEBUI_IMAGE_PIN

OLD = "sha256:" + "1" * 64
NEW = OPENWEBUI_IMAGE_PIN
UNIT = (
    "[Service]\n"
    f"ExecStartPre=podman pull ghcr.io/open-webui/open-webui@{OLD}\n"
    f"ExecStart=podman run ghcr.io/open-webui/open-webui@{OLD}\n"
)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))


def _unit(tmp_path) -> Path:
    unit = tmp_path / "hal0-openwebui.service"
    unit.write_text(UNIT, encoding="utf-8")
    return unit


def _ok_runner():
    r = MagicMock()
    r.return_value = MagicMock(returncode=0, stdout="", stderr="")
    return r


def test_no_unit_skips(tmp_path) -> None:
    res = openwebui_arm.converge_openwebui(unit_path=tmp_path / "missing.service")
    assert res["status"] == "skipped"


def test_already_pinned_converges(tmp_path) -> None:
    unit = tmp_path / "u.service"
    unit.write_text(UNIT.replace(OLD, NEW), encoding="utf-8")
    res = openwebui_arm.converge_openwebui(unit_path=unit, runner=_ok_runner())
    assert res["status"] == "converged"


def test_drift_diagnose_only_reports_stale(tmp_path) -> None:
    res = openwebui_arm.converge_openwebui(unit_path=_unit(tmp_path), apply=False, runner=_ok_runner())
    assert res["status"] == "stale"
    assert res["installed"] == OLD and res["pinned"] == NEW


def test_drift_pull_ok_repins_and_restarts(tmp_path) -> None:
    runner = _ok_runner()
    unit = _unit(tmp_path)
    res = openwebui_arm.converge_openwebui(
        unit_path=unit, runner=runner, is_hal0_user=lambda: False
    )
    assert res["status"] == "upgraded"
    text = unit.read_text(encoding="utf-8")
    assert NEW in text and OLD not in text
    pulled = [c.args[0] for c in runner.call_args_list if "pull" in c.args[0]]
    assert pulled, "must pull before repinning"


def test_pull_failure_leaves_unit_untouched(tmp_path) -> None:
    runner = MagicMock()
    runner.return_value = MagicMock(returncode=1, stdout="", stderr="no route")
    unit = _unit(tmp_path)
    res = openwebui_arm.converge_openwebui(unit_path=unit, runner=runner, is_hal0_user=lambda: False)
    assert res["status"] == "build_failed"
    assert OLD in unit.read_text(encoding="utf-8")


def test_target_digest_writes_override_marker(tmp_path) -> None:
    other = "sha256:" + "2" * 64
    unit = _unit(tmp_path)
    res = openwebui_arm.converge_openwebui(
        unit_path=unit, runner=_ok_runner(), is_hal0_user=lambda: False, target_digest=other
    )
    assert res["status"] == "upgraded"
    assert openwebui_arm.read_pin_override() == other
    # Subsequent pin-converge respects the override and reports it.
    res2 = openwebui_arm.converge_openwebui(unit_path=unit, runner=_ok_runner(), is_hal0_user=lambda: False)
    assert res2["status"] == "override"


def test_clear_override(tmp_path) -> None:
    other = "sha256:" + "2" * 64
    unit = _unit(tmp_path)
    openwebui_arm.converge_openwebui(
        unit_path=unit, runner=_ok_runner(), is_hal0_user=lambda: False, target_digest=other
    )
    res = openwebui_arm.converge_openwebui(
        unit_path=unit, runner=_ok_runner(), is_hal0_user=lambda: False, clear_override=True
    )
    assert openwebui_arm.read_pin_override() is None
    assert res["status"] == "upgraded"  # back to the release pin
