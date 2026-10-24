"""`hal0 agent upgrade pi` dispatch: root gate, Hermes redirect, driver call.

The pi driver runs in-process in the invoking user's context (it re-pins the
global npm prefix and redeploys into ``~/.pi``), so the command is CLI-local
like ``hal0 agent install pi``. These tests pin the dispatch only; the driver's
own behaviour is covered by ``tests/agents/test_pi_driver_upgrade.py``.
"""

from __future__ import annotations

import types

import pytest
from typer.testing import CliRunner

import hal0.cli.agent_commands as ac
from hal0.agents.manager import AgentError


def test_upgrade_pi_calls_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(ac.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        "hal0.agents.manager._driver_for",
        lambda name: types.SimpleNamespace(upgrade=lambda: calls.append(name)),
    )
    r = CliRunner().invoke(ac.app, ["upgrade", "pi"])
    assert r.exit_code == 0 and calls == ["pi"] and "Upgraded pi" in r.output


def test_upgrade_pi_requires_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ac.os, "geteuid", lambda: 1000)
    r = CliRunner().invoke(ac.app, ["upgrade", "pi"])
    assert r.exit_code == 1 and "sudo hal0 agent upgrade pi" in r.output


def test_upgrade_no_longer_takes_to(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ac.os, "geteuid", lambda: 0)
    r = CliRunner().invoke(ac.app, ["upgrade", "pi", "--to", "a" * 40])
    assert r.exit_code == 2 and "No such option" in r.output


@pytest.mark.parametrize("args", [["upgrade", "hermes"], ["upgrade"]])
def test_upgrade_hermes_is_gone_and_names_the_replacement(args: list[str]) -> None:
    r = CliRunner().invoke(ac.app, args)
    assert r.exit_code != 0
    if args == ["upgrade", "hermes"]:
        assert "hal0 update component hermes" in r.output


def test_upgrade_pi_reports_driver_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> None:
        raise AgentError("run this as the user who installed pi")

    monkeypatch.setattr(ac.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        "hal0.agents.manager._driver_for",
        lambda name: types.SimpleNamespace(upgrade=_boom),
    )
    r = CliRunner().invoke(ac.app, ["upgrade", "pi"])
    assert r.exit_code == 1
    assert "run this as the user who installed pi" in r.output
    assert "Upgraded pi" not in r.output


def test_upgrade_pi_driver_without_upgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ac.os, "geteuid", lambda: 0)
    monkeypatch.setattr("hal0.agents.manager._driver_for", lambda name: types.SimpleNamespace())
    r = CliRunner().invoke(ac.app, ["upgrade", "pi"])
    assert r.exit_code == 1 and "pi driver has no upgrade" in r.output


def test_upgrade_pi_reports_the_added_hindsight_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ac.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        "hal0.agents.manager._driver_for",
        lambda name: types.SimpleNamespace(upgrade=lambda: {"hindsight_alias_added": True}),
    )
    r = CliRunner().invoke(ac.app, ["upgrade", "pi"])
    assert r.exit_code == 0
    assert "Hindsight bank alias added to coding-agent.json" in r.output


def test_upgrade_pi_is_quiet_when_no_alias_was_added(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ac.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        "hal0.agents.manager._driver_for",
        lambda name: types.SimpleNamespace(upgrade=lambda: {"hindsight_alias_added": False}),
    )
    r = CliRunner().invoke(ac.app, ["upgrade", "pi"])
    assert r.exit_code == 0 and "Upgraded pi" in r.output
    assert "Hindsight" not in r.output
