"""`hal0 update component pi` dispatch: root gate, driver call, the old verb gone.

The pi driver runs in-process in the invoking user's context (it re-pins the
global npm prefix and redeploys into ``~/.pi``), so this component alone is
CLI-local and never reaches the daemon. These tests pin the dispatch only; the
driver's own behaviour is covered by ``tests/agents/test_pi_driver_upgrade.py``.
"""

from __future__ import annotations

import types

import pytest
from typer.testing import CliRunner

import hal0.cli.agent_commands as ac
import hal0.cli.update_commands as uc
from hal0.agents.manager import AgentError

ARGS = ["component", "pi"]


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    # A daemon call would be a bug in every test here except --check.
    monkeypatch.setattr(uc, "_api_unreachable", lambda url: pytest.fail("reached the API"))
    monkeypatch.setattr("hal0.components.pi_arm.converge_pi", lambda **_: {"status": "converged"})


def _as_root_with(monkeypatch: pytest.MonkeyPatch, upgrade: object) -> None:
    monkeypatch.setattr(uc.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        "hal0.agents.manager._driver_for",
        lambda name: types.SimpleNamespace(upgrade=upgrade),
    )


def test_update_pi_calls_driver_and_records_the_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.components.state import load_component_state

    calls: list[str] = []
    _as_root_with(monkeypatch, lambda: calls.append("pi"))
    r = CliRunner().invoke(uc.update_app, ARGS)
    assert r.exit_code == 0, r.output
    assert calls == ["pi"] and "Updated pi" in r.output
    assert load_component_state()["pi"]["status"] == "converged"


def test_update_pi_requires_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uc.os, "geteuid", lambda: 1000)
    r = CliRunner().invoke(uc.update_app, ARGS)
    assert r.exit_code == 1 and "sudo hal0 update component pi" in r.output


@pytest.mark.parametrize("flags", [["--target", "a" * 40], ["--clear-override"]])
def test_update_pi_takes_no_override(flags: list[str]) -> None:
    r = CliRunner().invoke(uc.update_app, [*ARGS, *flags])
    assert r.exit_code == 1 and "not supported for 'pi'" in r.output


def test_update_pi_check_reads_the_row_and_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(uc, "_api_unreachable", lambda url: False)
    monkeypatch.setattr(
        uc,
        "api_get",
        lambda path, **_: {
            "components": [{"id": "pi", "name": "Pi", "status": "converged"}],
            "pending": 0,
        },
    )
    monkeypatch.setattr(uc, "_update_pi", lambda: pytest.fail("--check must not update"))
    r = CliRunner().invoke(uc.update_app, [*ARGS, "--check"])
    assert r.exit_code == 0, r.output


@pytest.mark.parametrize("name", ["pi", "hermes"])
def test_agent_upgrade_is_gone(name: str) -> None:
    r = CliRunner().invoke(ac.app, ["upgrade", name])
    assert r.exit_code == 2 and "No such command" in r.output


def test_update_pi_reports_driver_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> None:
        raise AgentError("run this as the user who installed pi")

    _as_root_with(monkeypatch, _boom)
    r = CliRunner().invoke(uc.update_app, ARGS)
    assert r.exit_code == 1
    assert "run this as the user who installed pi" in r.output
    assert "Updated pi" not in r.output


def test_update_pi_driver_without_upgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uc.os, "geteuid", lambda: 0)
    monkeypatch.setattr("hal0.agents.manager._driver_for", lambda name: types.SimpleNamespace())
    r = CliRunner().invoke(uc.update_app, ARGS)
    assert r.exit_code == 1 and "pi driver has no upgrade" in r.output


@pytest.mark.parametrize("outcome", ["minted", "rotated"])
def test_update_pi_reports_a_new_memory_endpoint_key(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    _as_root_with(monkeypatch, lambda: {"memory_compat": outcome})
    r = CliRunner().invoke(uc.update_app, ARGS)
    assert r.exit_code == 0
    assert "/api/memory/compat" in r.output and outcome in r.output


@pytest.mark.parametrize("outcome", ["kept", "skipped"])
def test_update_pi_is_quiet_when_the_key_did_not_change(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    _as_root_with(monkeypatch, lambda: {"memory_compat": outcome})
    r = CliRunner().invoke(uc.update_app, ARGS)
    assert r.exit_code == 0 and "Updated pi" in r.output
    assert "/api/memory/compat" not in r.output
