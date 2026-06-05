"""Tests for ``hal0 config migrate`` honesty (#503).

The command used to be a no-op stub that unconditionally printed
"no migrations pending" as if it had run a real check, while there *is*
a real migration runner in ``hal0.config.migrations``. These tests pin
the wired behaviour:

  - with no config on disk it reports there is nothing to migrate
    (instead of pretending it inspected a schema), and
  - when the on-disk config is already at the latest schema version it
    says so honestly without rewriting the file.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import tomli_w
from typer.testing import CliRunner

from hal0.cli import config_commands

runner = CliRunner()


def _set_home(monkeypatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    cfg_dir = home / "etc" / "hal0"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HAL0_HOME", str(home))
    return cfg_dir / "hal0.toml"


def test_migrate_no_config_is_honest(monkeypatch, tmp_path: Path) -> None:
    """With no hal0.toml present, migrate must not claim a successful check."""
    home = tmp_path / "home"
    (home / "etc" / "hal0").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HAL0_HOME", str(home))

    result = runner.invoke(config_commands.app, ["migrate"])
    assert result.exit_code == 0, result.output
    out = result.output.lower()
    # It should reference the missing config, not assert a schema check ran.
    assert "no config" in out or "not found" in out or "nothing to migrate" in out


def test_migrate_already_latest_does_not_rewrite(monkeypatch, tmp_path: Path) -> None:
    """A config already at the latest schema version is reported honestly."""
    from hal0.config.migrations import latest_version

    path = _set_home(monkeypatch, tmp_path)
    payload = {"meta": {"schema_version": latest_version()}, "slots": {"port_range_start": 8081}}
    with open(path, "wb") as f:
        tomli_w.dump(payload, f)
    before = path.read_bytes()

    result = runner.invoke(config_commands.app, ["migrate"])
    assert result.exit_code == 0, result.output
    out = result.output.lower()
    assert "up to date" in out or "already" in out or "latest" in out
    # No migration was needed → the file must be left byte-for-byte intact.
    assert path.read_bytes() == before


def test_migrate_runs_the_real_runner_when_behind(monkeypatch, tmp_path: Path) -> None:
    """A config behind the latest schema is migrated forward and stamped.

    Only v1 is registered today, so to exercise the real upgrade path we
    register a temporary v2 migration. The command must run it, stamp
    ``meta.schema_version = 2``, and leave valid TOML on disk.
    """
    from hal0.config import migrations as mig

    path = _set_home(monkeypatch, tmp_path)
    with open(path, "wb") as f:
        tomli_w.dump({"meta": {"schema_version": 1}, "slots": {"port_range_start": 8081}}, f)

    def _v2(data: dict) -> dict:
        out = dict(data)
        out["slots"] = {**out.get("slots", {}), "migrated_by_v2": True}
        return out

    # Register a throwaway v2 migration just for this test, then clean up.
    monkeypatch.setitem(mig.MIGRATIONS, 2, _v2)

    result = runner.invoke(config_commands.app, ["migrate"])
    assert result.exit_code == 0, result.output

    with open(path, "rb") as f:
        data = tomllib.load(f)
    assert data["meta"]["schema_version"] == 2
    assert data["slots"]["migrated_by_v2"] is True
