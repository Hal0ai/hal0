"""Hermes converge arm: stamp-tracked venv rebuild at the requirements pin."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from hal0.components import hermes_arm
from hal0.config.paths import var_lib


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))


def _venv(tmp_path, with_python=True):
    # Built at installed_hermes_pin()'s HAL0_HOME-aware default
    # (var_lib() / "venvs" / "hermes") rather than an arbitrary tmp_path
    # location, so the no-arg calls below (mirroring the registry's own
    # usage) resolve to the same venv these helpers just built.
    venv = var_lib() / "venvs" / "hermes"
    (venv / "bin").mkdir(parents=True)
    if with_python:
        (venv / "bin" / "python").write_text("", encoding="utf-8")
    return venv


def test_unprovisioned_skips(tmp_path) -> None:
    res = hermes_arm.converge_hermes(venv=tmp_path / "nope")
    assert res["status"] == "skipped"


def test_stamped_at_pin_converges(tmp_path) -> None:
    venv = _venv(tmp_path)
    with patch("hal0.agents.hermes_provision._hermes_version_pin", return_value="v2026.7.7.2"):
        hermes_arm.stamp_path().parent.mkdir(parents=True, exist_ok=True)
        hermes_arm.stamp_path().write_text("v2026.7.7.2\n", encoding="utf-8")
        res = hermes_arm.converge_hermes(venv=venv)
    assert res["status"] == "converged"


def test_drift_diagnose_only(tmp_path) -> None:
    venv = _venv(tmp_path)
    with patch("hal0.agents.hermes_provision._hermes_version_pin", return_value="v2"):
        res = hermes_arm.converge_hermes(venv=venv, apply=False)
    assert res["status"] == "stale"


def test_drift_rebuilds_probes_and_stamps(tmp_path) -> None:
    venv = _venv(tmp_path)
    with (
        patch("hal0.agents.hermes_provision._hermes_version_pin", return_value="v2"),
        patch("hal0.agents.hermes_provision._install_venv") as install,
        patch("hal0.agents.hermes_provision._probe_mcp_client", return_value={"ok": True}),
    ):
        res = hermes_arm.converge_hermes(venv=venv, runner=MagicMock(return_value=MagicMock(returncode=0)), is_hal0_user=lambda: False)
    assert res["status"] == "upgraded"
    install.assert_called_once()
    assert hermes_arm.installed_hermes_pin() == "v2"


def test_failed_probe_is_build_failed_and_unstamped(tmp_path) -> None:
    venv = _venv(tmp_path)
    with (
        patch("hal0.agents.hermes_provision._hermes_version_pin", return_value="v2"),
        patch("hal0.agents.hermes_provision._install_venv"),
        patch("hal0.agents.hermes_provision._probe_mcp_client", return_value={"ok": False, "error": "no mcp"}),
    ):
        res = hermes_arm.converge_hermes(venv=venv, is_hal0_user=lambda: False)
    assert res["status"] == "build_failed"
    assert hermes_arm.installed_hermes_pin() is None


def test_deprovisioned_with_stale_stamp_reads_none(tmp_path) -> None:
    # State (var_lib()) is preserved across an uninstall unless --keep-data
    # is dropped, so a stamp file can outlive the venv it describes. That
    # must read as "not installed", not as a stale converged/pending
    # verdict (Task 8's status derivation depends on this).
    hermes_arm.stamp_path().parent.mkdir(parents=True, exist_ok=True)
    hermes_arm.stamp_path().write_text("v2026.7.7.2\n", encoding="utf-8")
    assert hermes_arm.installed_hermes_pin() is None


# ── I3: provision.json checkpoint fallback (final-review) ────────────────────
#
# No box provisioned before the venv.pin stamp existed has one, so a bare
# "no stamp -> not installed" read would misreport a working provision and
# trigger a needless venv rebuild on the first `hal0 update`. The fallback
# reads hermes_version out of the provisioner's last-run report instead.


def _write_checkpoint(hermes_version) -> None:
    path = var_lib() / "state" / "agents" / "hermes" / "provision.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hermes_version": hermes_version}), encoding="utf-8")


def test_stamp_absent_checkpoint_present_falls_back_to_checkpoint(tmp_path) -> None:
    venv = _venv(tmp_path)
    _write_checkpoint("v2026.7.7.2")
    assert hermes_arm.installed_hermes_pin(venv) == "v2026.7.7.2"


def test_stamp_and_checkpoint_both_absent_reads_none(tmp_path) -> None:
    venv = _venv(tmp_path)
    assert hermes_arm.installed_hermes_pin(venv) is None


def test_stamp_wins_over_checkpoint_when_both_present(tmp_path) -> None:
    venv = _venv(tmp_path)
    hermes_arm.stamp_path().parent.mkdir(parents=True, exist_ok=True)
    hermes_arm.stamp_path().write_text("stamp-value\n", encoding="utf-8")
    _write_checkpoint("checkpoint-value")
    assert hermes_arm.installed_hermes_pin(venv) == "stamp-value"


def test_checkpoint_malformed_json_reads_none(tmp_path) -> None:
    venv = _venv(tmp_path)
    path = var_lib() / "state" / "agents" / "hermes" / "provision.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert hermes_arm.installed_hermes_pin(venv) is None


def test_checkpoint_missing_hermes_version_key_reads_none(tmp_path) -> None:
    venv = _venv(tmp_path)
    path = var_lib() / "state" / "agents" / "hermes" / "provision.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hal0_version": "1.0"}), encoding="utf-8")
    assert hermes_arm.installed_hermes_pin(venv) is None


def test_checkpoint_venv_absent_still_reads_none_regardless_of_checkpoint(tmp_path) -> None:
    # No venv -> installed_hermes_pin short-circuits to None before ever
    # consulting the stamp or the checkpoint (deprovisioned-box semantics).
    _write_checkpoint("v2026.7.7.2")
    assert hermes_arm.installed_hermes_pin(tmp_path / "nope") is None
