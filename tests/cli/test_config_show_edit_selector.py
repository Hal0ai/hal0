"""Tests for the `hal0 config show [hal0|upstreams|providers]` and `config
edit [...]` file selector (CLI consolidation, 2026-07).

Previously both commands were hardcoded to hal0.toml, even though `hal0
config validate` already checks three files — when validate reported an
error in upstreams.toml, there was no `edit`/`show` target for it.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from hal0.cli import config_commands

runner = CliRunner()


def _set_home(monkeypatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "etc" / "hal0").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HAL0_HOME", str(home))
    return home / "etc" / "hal0"


def test_config_show_defaults_to_hal0_toml(monkeypatch, tmp_path: Path) -> None:
    cfg_dir = _set_home(monkeypatch, tmp_path)
    (cfg_dir / "hal0.toml").write_text("[meta]\nschema_version = 1\n")

    result = runner.invoke(config_commands.app, ["show"])
    assert result.exit_code == 0, result.output
    assert "schema_version" in result.output


def test_config_show_upstreams_selects_upstreams_toml(monkeypatch, tmp_path: Path) -> None:
    cfg_dir = _set_home(monkeypatch, tmp_path)
    (cfg_dir / "upstreams.toml").write_text("[[upstream]]\nname = 'demo'\n")

    result = runner.invoke(config_commands.app, ["show", "upstreams"])
    assert result.exit_code == 0, result.output
    assert "demo" in result.output


def test_config_show_providers_selects_providers_toml(monkeypatch, tmp_path: Path) -> None:
    cfg_dir = _set_home(monkeypatch, tmp_path)
    (cfg_dir / "providers.toml").write_text("[[provider]]\nname = 'demo-provider'\n")

    result = runner.invoke(config_commands.app, ["show", "providers"])
    assert result.exit_code == 0, result.output
    assert "demo-provider" in result.output


def test_config_show_missing_file_reports_dim_notice(monkeypatch, tmp_path: Path) -> None:
    cfg_dir = _set_home(monkeypatch, tmp_path)
    assert not (cfg_dir / "upstreams.toml").exists()

    result = runner.invoke(config_commands.app, ["show", "upstreams"])
    assert result.exit_code == 0, result.output
    assert "No config at" in result.output


def test_config_edit_upstreams_seeds_and_opens_upstreams_toml(monkeypatch, tmp_path: Path) -> None:
    cfg_dir = _set_home(monkeypatch, tmp_path)
    monkeypatch.setenv("EDITOR", "true")  # no-op editor available on any *nix box

    result = runner.invoke(config_commands.app, ["edit", "upstreams"])
    assert result.exit_code == 0, result.output
    seeded = cfg_dir / "upstreams.toml"
    assert seeded.exists()
    # hal0.toml's bespoke seed content must NOT leak into other files.
    assert "port_range_start" not in seeded.read_text()
