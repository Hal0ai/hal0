"""The wizard package must run by file path and import as an installed package."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from hal0.install.wizard import cli

WIZARD = Path(__file__).resolve().parents[3] / "src/hal0/install/wizard"


def test_runs_by_path_in_isolated_mode() -> None:
    r = subprocess.run(
        [sys.executable, "-I", str(WIZARD / "__main__.py"), "--version"],
        capture_output=True,
        text=True,
        cwd="/",
        env={},
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "hal0 setup wizard 1"


def test_imports_as_installed_package() -> None:
    assert cli.main(["--version"]) == 0


def test_package_imports_only_standard_library() -> None:
    for f in WIZARD.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            assert set(names) <= sys.stdlib_module_names | {"hal0_wizard"}, f"{f.name}: {names}"


def test_unknown_argument_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--no-such-thing"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""
