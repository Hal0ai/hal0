"""CLI tests for ``hal0 agent personas {list,show,activate}`` (PR-3, v0.3).

These commands read + mutate the persona store under
``/var/lib/hal0/.hermes/personas/``. We point the personas module
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
    assert "hal0-brain" in result.stdout
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


def _stub_refresh(monkeypatch: pytest.MonkeyPatch, **fields: object) -> list[object]:
    from hal0.agents import persona_live

    calls: list[object] = []
    result = {
        "status": "applied",
        "message": "Persona prompt written to Hermes's config.yaml; restarted.",
        "changed": True,
        "restarted": True,
        "failed_units": [],
        **fields,
    }
    monkeypatch.setattr(
        persona_live, "refresh_persona_prompt", lambda root, **_kw: calls.append(root) or result
    )
    return calls


def test_personas_activate_writes_pointer_and_applies_to_hermes(
    cli_runner: CliRunner,
    isolated_personas: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    P.seed_default_personas(agent_id="hermes-agent", root=isolated_personas)
    calls = _stub_refresh(monkeypatch)
    result = cli_runner.invoke(agent_app, ["personas", "activate", "hal0-brain"])
    assert result.exit_code == 0
    assert (isolated_personas / P.ACTIVE_POINTER).read_text().strip() == "hal0-brain"
    assert "Activated" in result.stdout
    assert "Brain" in result.stdout
    assert "written to Hermes" in result.stdout
    assert calls == [isolated_personas]
    # The retired claims must not come back.
    assert "next Hermes restart" not in result.stdout
    assert "nudge" not in result.stdout.lower()


def test_personas_activate_failed_apply_says_so_but_succeeds(
    cli_runner: CliRunner,
    isolated_personas: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    P.seed_default_personas(agent_id="hermes-agent", root=isolated_personas)
    _stub_refresh(
        monkeypatch,
        status="failed",
        message="Saved, but could not write Hermes's config.yaml: denied. The new prompt is "
        "not live; run `sudo hal0 agent reprovision hermes --repair` to apply it.",
    )
    result = cli_runner.invoke(agent_app, ["personas", "activate", "hal0-brain"])
    assert result.exit_code == 0
    assert "not live" in result.stdout
    assert "next Hermes restart" not in result.stdout
    assert (isolated_personas / P.ACTIVE_POINTER).read_text().strip() == "hal0-brain"


def test_personas_activate_no_apply_leaves_hermes_alone(
    cli_runner: CliRunner,
    isolated_personas: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    P.seed_default_personas(agent_id="hermes-agent", root=isolated_personas)
    calls = _stub_refresh(monkeypatch)
    result = cli_runner.invoke(agent_app, ["personas", "activate", "hal0-brain", "--no-apply"])
    assert result.exit_code == 0
    assert calls == []
    assert "Not applied" in result.stdout


def test_personas_show_hides_steward_only_fields_for_other_personas(
    cli_runner: CliRunner, isolated_personas: Path
) -> None:
    P.seed_default_personas(agent_id="hermes-agent", root=isolated_personas)
    result = cli_runner.invoke(agent_app, ["personas", "show", "hermes"])
    assert result.exit_code == 0
    assert "preferred_model =" not in result.stdout
    assert "allowed =" not in result.stdout
    assert "steward-only" in result.stdout


def test_personas_show_keeps_steward_only_fields_for_the_steward(
    cli_runner: CliRunner, isolated_personas: Path
) -> None:
    P.seed_default_personas(agent_id="hermes-agent", root=isolated_personas)
    result = cli_runner.invoke(agent_app, ["personas", "show", "hal0-brain"])
    assert result.exit_code == 0
    assert "preferred_model =" in result.stdout
    assert "allowed =" in result.stdout
    assert "steward-only" not in result.stdout


def test_personas_activate_missing_persona_exits_nonzero(
    cli_runner: CliRunner, isolated_personas: Path
) -> None:
    result = cli_runner.invoke(agent_app, ["personas", "activate", "ghost"])
    assert result.exit_code != 0
