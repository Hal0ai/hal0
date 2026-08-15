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


def test_permission_denied_hint_does_not_recommend_widening_the_file(
    monkeypatch, tmp_path: Path
) -> None:
    """The remedy is sudo or group membership — never `chmod 0644`.

    upstreams.toml is 0640 as of ADR-0002 (its provider inventory is not public
    information). The old hint told an operator hitting PermissionError to
    `sudo chmod 0644` the selected file, i.e. to undo that tightening by hand —
    and `hal0 doctor perms` would then converge it back, so the advice was both
    harmful and wrong. Asserts the rendered hint directly.
    """
    cfg_dir = _set_home(monkeypatch, tmp_path)
    target = cfg_dir / "upstreams.toml"
    target.write_text("[[upstream]]\nname = 'demo'\n")
    target.chmod(0o640)

    def _boom(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_text", _boom)

    result = runner.invoke(config_commands.app, ["show", "upstreams"])

    assert result.exit_code == 1, result.output
    # Rich wraps the panel, so compare with all whitespace removed — a
    # recommendation split across two lines is still a recommendation.
    out = "".join(result.output.split())
    assert "Permissiondenied" in out
    assert "0644" not in out, f"hint still names a world-readable mode: {result.output}"
    assert "sudochmod" not in out, f"hint still recommends a chmod: {result.output}"
    assert "sudo" in out
    assert "usermod-aGhal0" in out


def test_permission_denied_hint_omits_group_advice_for_owner_only_files(
    monkeypatch, tmp_path: Path
) -> None:
    """hal0.toml is 0600 by design — "join the hal0 group" would not help.

    Sending an operator through `usermod -aG` and a re-login only to hit the
    identical error is worse than no advice, so the remedy is mode-aware.
    """
    cfg_dir = _set_home(monkeypatch, tmp_path)
    target = cfg_dir / "hal0.toml"
    target.write_text("[meta]\nschema_version = 1\n")
    target.chmod(0o600)

    def _boom(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_text", _boom)

    result = runner.invoke(config_commands.app, ["show"])

    assert result.exit_code == 1, result.output
    out = "".join(result.output.split())
    assert "usermod" not in out, f"group advice on an owner-only file: {result.output}"
    assert "sudo" in out
    assert "owner-only" in out
