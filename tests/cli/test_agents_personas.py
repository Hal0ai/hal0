"""CLI tests for ``hal0 agent personas {list,show,activate}`` (PR-3, v0.3).

These commands read + mutate the persona store under
``/var/lib/hal0/agents/hermes/personas/``. We point the personas module
at a temp dir per test so the CLI hits an isolated store.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from hal0.agents import personas as P
from hal0.cli.agent_commands import app as agent_app


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def isolated_personas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the personas module to a per-test root."""
    root = tmp_path / "personas"
    root.mkdir()
    monkeypatch.setattr(P, "PERSONAS_ROOT", root)
    return root


def test_personas_list_empty_emits_install_hint(
    cli_runner: CliRunner, isolated_personas: Path
) -> None:
    result = cli_runner.invoke(agent_app, ["personas", "list"])
    assert result.exit_code == 0
    assert "No personas seeded" in result.stdout


def test_personas_list_after_seed(cli_runner: CliRunner, isolated_personas: Path) -> None:
    P.seed_default_personas(agent_id="hermes-agent", root=isolated_personas)
    result = cli_runner.invoke(agent_app, ["personas", "list"])
    assert result.exit_code == 0
    # Both seeded personas appear; the default is marked active.
    assert "hermes" in result.stdout
    assert "coder" in result.stdout
    # Some part of "yes" appears on the active row's column.
    assert "yes" in result.stdout


def test_personas_show_emits_toml(cli_runner: CliRunner, isolated_personas: Path) -> None:
    P.seed_default_personas(agent_id="hermes-agent", root=isolated_personas)
    result = cli_runner.invoke(
        agent_app,
        ["personas", "show", "hermes"],
        env={"COLUMNS": "200"},
    )
    assert result.exit_code == 0
    # Rich's panel wraps lines; persona id marker is the most reliable
    # substring that survives terminal-width truncation.
    assert "persona: hermes" in result.stdout
    # And the body has the hermes display name from the seed.
    assert "Hermes" in result.stdout


def test_personas_show_missing_persona_exits_nonzero(
    cli_runner: CliRunner, isolated_personas: Path
) -> None:
    result = cli_runner.invoke(agent_app, ["personas", "show", "ghost"])
    assert result.exit_code != 0


def test_personas_activate_writes_pointer_and_emits_status(
    cli_runner: CliRunner,
    isolated_personas: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    P.seed_default_personas(agent_id="hermes-agent", root=isolated_personas)
    # Hot-reload nudge is best-effort. Stub to return success so the test
    # asserts the success path explicitly; the failure path lives in
    # test_personas.py's activate test.
    monkeypatch.setattr(P, "hermes_reload", lambda **_kw: (True, None))
    result = cli_runner.invoke(agent_app, ["personas", "activate", "coder"])
    assert result.exit_code == 0
    # active.txt now points at coder.
    assert (isolated_personas / P.ACTIVE_POINTER).read_text().strip() == "coder"
    assert "Activated" in result.stdout
    assert "Coder" in result.stdout


def test_personas_activate_failed_reload_warns_but_succeeds(
    cli_runner: CliRunner,
    isolated_personas: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Hermes isn't running the nudge fails but the activation
    succeeds — the file write is the durable part."""
    P.seed_default_personas(agent_id="hermes-agent", root=isolated_personas)
    monkeypatch.setattr(P, "hermes_reload", lambda **_kw: (False, "Connection refused"))
    result = cli_runner.invoke(agent_app, ["personas", "activate", "coder"])
    assert result.exit_code == 0
    assert "Hot-reload nudge skipped" in result.stdout
    # Activation still went through.
    assert (isolated_personas / P.ACTIVE_POINTER).read_text().strip() == "coder"


def test_personas_activate_missing_persona_exits_nonzero(
    cli_runner: CliRunner, isolated_personas: Path
) -> None:
    result = cli_runner.invoke(agent_app, ["personas", "activate", "ghost"])
    assert result.exit_code != 0
