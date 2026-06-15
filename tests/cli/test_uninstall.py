"""Tests for the ``hal0 uninstall`` CLI subcommand.

The command is a thin wrapper around ``installer/uninstall.sh`` — it
exec's the script so the script's DELETE prompt inherits the live TTY.
These tests intercept ``os.execvp`` and assert the argv the wrapper
constructs, plus the pre-flight refusals that protect non-interactive
callers from a hung prompt.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from hal0.cli.main import app, uninstall

runner = CliRunner()


@pytest.fixture
def captured_exec(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Intercept os.execvp so we can assert argv without replacing the process."""
    captured: dict[str, Any] = {}

    def fake_execvp(file: str, argv: list[str]) -> None:
        captured["file"] = file
        captured["argv"] = list(argv)
        # Raise typer.Exit so the command returns control to the test —
        # the real execvp never returns either, so callers don't expect it to.
        raise typer.Exit(0)

    monkeypatch.setattr("os.execvp", fake_execvp)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    return captured


def test_uninstall_default_args(captured_exec: dict[str, Any]) -> None:
    """No flags → bash <script> with no extra args.

    Bypasses CliRunner (which swaps stdin for a non-TTY StringIO) and calls
    the Typer command function directly so the production isatty() check
    sees the True we monkeypatched."""
    with pytest.raises(typer.Exit) as exc:
        uninstall(purge=False, keep_data=False, force=False, dev=False)
    assert (exc.value.exit_code or 0) == 0
    assert captured_exec["file"] == "bash"
    assert captured_exec["argv"][0] == "bash"
    assert captured_exec["argv"][1].endswith("/installer/uninstall.sh")
    assert captured_exec["argv"][2:] == []


def test_uninstall_keep_data_flag(captured_exec: dict[str, Any]) -> None:
    result = runner.invoke(app, ["uninstall", "--keep-data"])
    assert result.exit_code == 0
    assert captured_exec["argv"][2:] == ["--keep-data"]


def test_uninstall_force_flag(captured_exec: dict[str, Any]) -> None:
    result = runner.invoke(app, ["uninstall", "--force"])
    assert result.exit_code == 0
    assert captured_exec["argv"][2:] == ["--force"]


def test_uninstall_short_force_flag(captured_exec: dict[str, Any]) -> None:
    result = runner.invoke(app, ["uninstall", "-f"])
    assert result.exit_code == 0
    assert captured_exec["argv"][2:] == ["--force"]


def test_uninstall_dev_flag(captured_exec: dict[str, Any]) -> None:
    result = runner.invoke(app, ["uninstall", "--dev", "--keep-data"])
    assert result.exit_code == 0
    assert captured_exec["argv"][2:] == ["--keep-data", "--dev"]


def test_uninstall_purge_flag(captured_exec: dict[str, Any]) -> None:
    """--purge forwards to the script (with --force here to skip the prompt)."""
    result = runner.invoke(app, ["uninstall", "--purge", "--force"])
    assert result.exit_code == 0
    assert captured_exec["argv"][2:] == ["--purge", "--force"]


def test_uninstall_clean_slate_alias(captured_exec: dict[str, Any]) -> None:
    """--clean-slate is an alias of --purge and forwards --purge to the script."""
    result = runner.invoke(app, ["uninstall", "--clean-slate", "--force"])
    assert result.exit_code == 0
    assert captured_exec["argv"][2:] == ["--purge", "--force"]


def test_uninstall_default_non_tty_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Conservative default never prompts, so a bare `hal0 uninstall` is safe
    non-interactively — it must NOT refuse (the old contract did, when the
    default deleted data)."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.delenv("HAL0_FORCE", raising=False)

    captured: dict[str, Any] = {}

    def fake_execvp(file: str, argv: list[str]) -> None:
        captured["argv"] = list(argv)
        raise typer.Exit(0)

    monkeypatch.setattr("os.execvp", fake_execvp)

    result = runner.invoke(app, ["uninstall"])
    assert result.exit_code == 0
    assert captured["argv"][2:] == []


def test_uninstall_purge_refuses_without_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """`hal0 uninstall --purge` from a non-TTY context must refuse, so the
    shell script's DELETE prompt doesn't hang silently."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.delenv("HAL0_FORCE", raising=False)

    called: dict[str, Any] = {}

    def fake_execvp(file: str, argv: list[str]) -> None:
        called["yes"] = True

    monkeypatch.setattr("os.execvp", fake_execvp)

    result = runner.invoke(app, ["uninstall", "--purge"])
    assert result.exit_code == 1
    assert "yes" not in called


def test_uninstall_purge_non_tty_with_force_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """--force bypasses the --purge prompt — no TTY needed."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.delenv("HAL0_FORCE", raising=False)

    captured: dict[str, Any] = {}

    def fake_execvp(file: str, argv: list[str]) -> None:
        captured["argv"] = list(argv)
        raise typer.Exit(0)

    monkeypatch.setattr("os.execvp", fake_execvp)

    result = runner.invoke(app, ["uninstall", "--purge", "--force"])
    assert result.exit_code == 0
    assert "--purge" in captured["argv"]
    assert "--force" in captured["argv"]


def test_uninstall_purge_non_tty_with_hal0_force_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """HAL0_FORCE=1 also bypasses the --purge TTY requirement."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setenv("HAL0_FORCE", "1")

    captured: dict[str, Any] = {}

    def fake_execvp(file: str, argv: list[str]) -> None:
        captured["argv"] = list(argv)
        raise typer.Exit(0)

    monkeypatch.setattr("os.execvp", fake_execvp)

    result = runner.invoke(app, ["uninstall", "--purge"])
    assert result.exit_code == 0
    assert "--purge" in captured["argv"]


def test_uninstall_resolves_fhs_path_when_not_editable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, captured_exec: dict[str, Any]
) -> None:
    """Non-editable FHS install (#495): __file__ is in the venv site-packages
    (no installer/ next to it), so the wrapper falls back to the source tree
    under the `current` symlink (paths.usr_lib())."""
    import hal0
    from hal0.config import paths

    # site-packages layout — parents[2]/installer/uninstall.sh does NOT exist.
    fake_module = (
        tmp_path / "venv" / "lib" / "python3.12" / "site-packages" / "hal0" / "__init__.py"
    )
    fake_module.parent.mkdir(parents=True)
    fake_module.write_text("")
    monkeypatch.setattr(hal0, "__file__", str(fake_module))

    # A real uninstall.sh lives under the FHS `current` tree.
    fhs_current = tmp_path / "usr-lib" / "current"
    (fhs_current / "installer").mkdir(parents=True)
    (fhs_current / "installer" / "uninstall.sh").write_text("#!/usr/bin/env bash\n")
    monkeypatch.setattr(paths, "usr_lib", lambda: fhs_current)

    with pytest.raises(typer.Exit) as exc:
        uninstall(purge=False, keep_data=False, force=True, dev=False)
    assert (exc.value.exit_code or 0) == 0
    assert captured_exec["argv"][1] == str(fhs_current / "installer" / "uninstall.sh")


def test_uninstall_missing_script_dies(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If uninstall.sh isn't where we expect, fail loudly instead of running."""
    import hal0
    from hal0.config import paths

    fake_module = tmp_path / "src" / "hal0" / "__init__.py"
    fake_module.parent.mkdir(parents=True)
    fake_module.write_text("")
    monkeypatch.setattr(hal0, "__file__", str(fake_module))
    # Neither layout has the script: point the FHS candidate at an empty tree.
    monkeypatch.setattr(paths, "usr_lib", lambda: tmp_path / "nonexistent" / "current")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    called: dict[str, Any] = {}

    def fake_execvp(file: str, argv: list[str]) -> None:
        called["yes"] = True

    monkeypatch.setattr("os.execvp", fake_execvp)

    result = runner.invoke(app, ["uninstall"])
    assert result.exit_code == 1
    assert "yes" not in called
