"""Hermes converge arm: release-builder converge onto the requirements pin."""

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


PIN = "d923a5eb487a17c0a68801bba9ef0f56c81040a8"  # the shipped pin, v0.22.2
OLD_PIN = "3ef6bbd201263d354fd83ec55b3c306ded2eb72a"  # 0.19.0, before #2615 PR 4


def _live_release(ref=PIN, minor="3.14"):
    """A migrated layout: venvs/hermes -> hermes/current/venv -> releases/<id>/venv."""
    from hal0.agents import hermes_release as rel

    venv = var_lib() / "venvs" / "hermes"
    layout = rel.Layout.for_venv(venv)
    rid = f"{ref[:12]}-20261021T000000Z"
    rdir = layout.releases / rid
    (rdir / "venv" / "bin").mkdir(parents=True)
    (rdir / "venv" / "bin" / "python").write_text("", encoding="utf-8")
    (rdir / "release.json").write_text(
        json.dumps({"id": rid, "ref": ref, "python_minor": minor, "legacy": False}),
        encoding="utf-8",
    )
    layout.current.symlink_to(f"releases/{rid}")
    venv.parent.mkdir(parents=True, exist_ok=True)
    venv.symlink_to(layout.venv_target)
    return venv


def test_live_release_at_pin_converges(tmp_path) -> None:
    venv = _live_release()
    with patch("hal0.agents.hermes_release.ensure_release") as ensure:
        res = hermes_arm.converge_hermes(venv=venv)
    assert res["status"] == "converged"
    ensure.assert_not_called()


def test_legacy_venv_dir_is_stale_on_diagnose(tmp_path) -> None:
    # A real venvs/hermes directory is the legacy layout: the layout trigger
    # fires even when the stamp matches the pin (ADR-0032 decision 6).
    venv = _venv(tmp_path)
    hermes_arm.stamp_path().parent.mkdir(parents=True, exist_ok=True)
    hermes_arm.stamp_path().write_text(PIN[:12] + "\n", encoding="utf-8")
    res = hermes_arm.converge_hermes(venv=venv, apply=False)
    assert res["status"] == "stale"
    assert "pre-layout venv" in res["trigger"]


def test_ref_drift_is_stale_on_diagnose(tmp_path) -> None:
    venv = _live_release(ref="f" * 40)
    res = hermes_arm.converge_hermes(venv=venv, apply=False)
    assert res["status"] == "stale"
    assert "differs" in res["trigger"]


def test_a_0_19_0_release_on_312_is_stale_after_the_pin_bump(tmp_path) -> None:
    """`hal0 update` to 1.6.0: the live 0.19.0 release on 3.12 must rebuild."""
    venv = _live_release(ref=OLD_PIN, minor="3.12")
    res = hermes_arm.converge_hermes(venv=venv, apply=False)
    assert res["status"] == "stale"
    assert "differs from target d923a5eb487a" in res["trigger"]


def test_interpreter_minor_drift_triggers(tmp_path) -> None:
    venv = _live_release(minor="3.11")
    res = hermes_arm.converge_hermes(venv=venv, apply=False)
    assert res["status"] == "stale"
    assert "interpreter" in res["trigger"]


def _outcome(**kw):
    from hal0.agents.hermes_release import ReleaseOutcome

    return ReleaseOutcome(**kw)


def test_drift_builds_through_the_release_builder_without_waiting(tmp_path) -> None:
    venv = _venv(tmp_path)
    runner = MagicMock(return_value=MagicMock(returncode=0, stdout=""))
    with patch(
        "hal0.agents.hermes_release.ensure_release",
        return_value=_outcome(status="activated", release_id="r1", ref=PIN),
    ) as ensure:
        res = hermes_arm.converge_hermes(venv=venv, runner=runner, is_hal0_user=lambda: True)
    assert res["status"] == "upgraded"
    assert res["release"] == "r1"
    kw = ensure.call_args.kwargs
    assert kw["caller"] == "converge"
    assert kw["wait"] is False, "hal0 update never waits for the builder lock"
    assert kw["venv"] == venv
    # As hal0, unit stops/starts go through the hal0-systemctl seam.
    assert kw["units"]._use_seam is True


def test_busy_lock_is_a_busy_status_with_the_holder_pid(tmp_path) -> None:
    venv = _venv(tmp_path)
    holder = {"pid": 4242, "caller": "provision", "started_at": "2026-10-21T00:00:00Z"}
    with patch(
        "hal0.agents.hermes_release.ensure_release",
        return_value=_outcome(status="busy", holder=holder),
    ):
        res = hermes_arm.converge_hermes(venv=venv, is_hal0_user=lambda: False)
    assert res["status"] == "busy"
    assert res["holder_pid"] == 4242


@pytest.mark.parametrize("status", ["build_failed", "activation_failed"])
def test_failed_build_or_activation_names_a_real_remedy(tmp_path, status) -> None:
    venv = _venv(tmp_path)
    with patch(
        "hal0.agents.hermes_release.ensure_release",
        return_value=_outcome(
            status=status,
            error="verify-mcp: no mcp",
            remedy="sudo hal0 agent reprovision hermes --repair",
        ),
    ):
        res = hermes_arm.converge_hermes(venv=venv, is_hal0_user=lambda: False)
    assert res["status"] == status
    assert res["error"] == "verify-mcp: no mcp"
    # The remedy must name a real `hal0 agent` subcommand (it once named a
    # non-existent `provision`).
    from hal0.cli.agent_commands import app as agent_app

    subcommand = res["remedy"].split("hal0 agent ", 1)[1].split()[0]
    registered = {c.name or c.callback.__name__ for c in agent_app.registered_commands}
    assert subcommand in registered


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


def test_a_missing_venv_link_is_still_provisioned_and_stale(tmp_path) -> None:
    venv = _live_release()
    venv.unlink()
    res = hermes_arm.converge_hermes(venv=venv, apply=False)
    assert res["status"] == "stale"
    assert "does not point" in res["trigger"]
    assert "does not point" in hermes_arm.hermes_build_trigger(venv)


def test_build_trigger_helper_is_none_when_converged_or_absent(tmp_path) -> None:
    assert hermes_arm.hermes_build_trigger(tmp_path / "nope" / "venvs" / "hermes") is None
    venv = _live_release()
    assert hermes_arm.hermes_build_trigger(venv) is None


def test_a_tree_a_migration_crash_left_without_current_is_still_provisioned(tmp_path) -> None:
    from hal0.agents import hermes_release as rel

    venv = var_lib() / "venvs" / "hermes"
    layout = rel.Layout.for_venv(venv)
    rdir = layout.releases / "legacy-3ef6bbd20126"
    (rdir / "venv" / "bin").mkdir(parents=True)
    (rdir / "venv" / "bin" / "python").write_text("")
    (rdir / "release.json").write_text(json.dumps({"legacy": True, "ref": "3ef6bbd20126"}))
    res = hermes_arm.converge_hermes(venv=venv, apply=False)
    assert res["status"] == "stale"
    assert hermes_arm.hermes_build_trigger(venv) == "no live release"
