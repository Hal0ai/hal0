"""Tests for `hal0 app list` / `hal0 app uninstall <name>` (CLI
consolidation, 2026-07) — previously `app_commands.py` only had `install`,
with no way to check state or remove an app via the CLI.
"""

from __future__ import annotations

from typing import Any

import pytest
import typer
from typer.testing import CliRunner

import hal0.cli.app_commands as app_cmds

runner = CliRunner()


def test_app_list_queries_systemctl_for_known_apps(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kw: Any):
        calls.append(cmd)

        class _R:
            stdout = "active\n" if cmd[1] == "is-active" else "enabled\n"

        return _R()

    monkeypatch.setattr(app_cmds.shutil, "which", lambda _name: "/usr/bin/systemctl")
    monkeypatch.setattr(app_cmds.subprocess, "run", fake_run)

    result = runner.invoke(app_cmds.app, ["list"])
    assert result.exit_code == 0, result.output
    assert "openwebui" in result.output
    assert "active" in result.output
    assert "enabled" in result.output
    assert any(c[:2] == ["systemctl", "is-enabled"] for c in calls)
    assert any(c[:2] == ["systemctl", "is-active"] for c in calls)


def test_app_list_without_systemctl_shows_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_cmds.shutil, "which", lambda _name: None)
    result = runner.invoke(app_cmds.app, ["list"])
    assert result.exit_code == 0, result.output
    assert "?" in result.output


def test_app_uninstall_unknown_app_dies() -> None:
    with pytest.raises((SystemExit, typer.Exit)):
        app_cmds.app_uninstall(name="comfyui", force=True)


def test_app_uninstall_disables_and_stops_the_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kw: Any):
        calls.append(cmd)

        class _R:
            returncode = 0

        return _R()

    monkeypatch.setattr(app_cmds.shutil, "which", lambda _name: "/usr/bin/systemctl")
    monkeypatch.setattr(app_cmds.subprocess, "run", fake_run)

    result = runner.invoke(app_cmds.app, ["uninstall", "openwebui", "--force"])
    assert result.exit_code == 0, result.output
    assert ["systemctl", "disable", "--now", "hal0-openwebui.service"] in calls


def test_app_uninstall_requires_confirmation_without_force(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(app_cmds.shutil, "which", lambda _name: "/usr/bin/systemctl")
    monkeypatch.setattr(
        app_cmds.subprocess,
        "run",
        lambda cmd, **_kw: calls.append(cmd) or type("R", (), {"returncode": 0})(),
    )

    result = runner.invoke(app_cmds.app, ["uninstall", "openwebui"], input="n\n")
    assert result.exit_code != 0
    assert calls == []
