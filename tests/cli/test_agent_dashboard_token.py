"""``hal0 agent dashboard-token`` — prints the per-install token (#2899)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from hal0.cli import agent_commands, agent_shim

runner = CliRunner()


@pytest.fixture(autouse=True)
def _state_under_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "state" / "agents"
    monkeypatch.setattr(agent_shim, "_STATE_AGENTS_DIR", state)
    return state


def test_prints_the_token_the_shim_minted() -> None:
    env = agent_shim._build_hermes_env(
        agent_shim.AgentConfig(
            agent_id="hermes",
            agent_type="hermes",
            home=Path("/nonexistent/home"),
            venv=Path("/nonexistent/venv"),
            host="127.0.0.1",
            port=9119,
        )
    )
    result = runner.invoke(agent_commands.app, ["dashboard-token"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == env["HERMES_DASHBOARD_SESSION_TOKEN"]


def test_missing_file_fails_and_names_the_unit() -> None:
    result = runner.invoke(agent_commands.app, ["dashboard-token", "hermes"])
    assert result.exit_code == 1
    assert "hal0-agent@hermes" in result.output
    assert result.output.strip().splitlines()[-1] != ""


def test_blank_file_counts_as_missing(_state_under_tmp: Path) -> None:
    path = _state_under_tmp / "hermes" / "dashboard-session-token"
    path.parent.mkdir(parents=True)
    path.write_text("\n")
    result = runner.invoke(agent_commands.app, ["dashboard-token"])
    assert result.exit_code == 1


def test_unreadable_file_says_run_as_root(
    _state_under_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _deny(*_a: object, **_k: object) -> str:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_text", _deny)
    result = runner.invoke(agent_commands.app, ["dashboard-token"])
    assert result.exit_code == 1
    assert "hal0 user" in result.output
