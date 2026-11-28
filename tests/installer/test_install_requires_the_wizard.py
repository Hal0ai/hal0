"""installer/install.sh needs the setup screens and has no path without them.

A release tree that lacks the wizard (its entry point or its catalog) stops
with one line before anything runs; a tree that has it stops on every unknown
flag. The tests run the real ``install.sh`` out of a scratch tree whose
``installer/`` entries are links to this checkout's files, so ``REPO_ROOT`` is
the scratch tree and the presence of the wizard is the tree's own contents.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "installer"

MISSING_LINE = (
    "this release tree is incomplete: the setup screens are missing "
    "(src/hal0/install/wizard/__main__.py, installer/manifests/setup-catalog.json)"
)


def _tree(root: Path, *, wizard: bool) -> Path:
    """A release tree whose installer/ files are links to the real ones."""
    inst = root / "installer"
    inst.mkdir(parents=True)
    (inst / "install.sh").symlink_to(INSTALLER / "install.sh")
    (inst / "lib").symlink_to(INSTALLER / "lib")
    if wizard:
        entry = root / "src" / "hal0" / "install" / "wizard" / "__main__.py"
        entry.parent.mkdir(parents=True)
        entry.write_text("", encoding="utf-8")
        catalog = inst / "manifests" / "setup-catalog.json"
        catalog.parent.mkdir()
        catalog.write_text("{}", encoding="utf-8")
    return root


def _run(tree: Path, work: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    work.mkdir()
    return subprocess.run(
        ["bash", str(tree / "installer" / "install.sh"), *args],
        capture_output=True,
        text=True,
        cwd=work,
        env={"PATH": os.environ["PATH"], "HOME": str(work), "HAL0_PLAIN": "1"},
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


@pytest.mark.parametrize("args", [[], ["--dev"], ["--no-owui"], ["--help"], ["--bogus"]])
def test_a_tree_without_the_wizard_stops_with_one_line(tmp_path: Path, args: list[str]) -> None:
    tree = _tree(tmp_path / "tree", wizard=False)
    proc = _run(tree, tmp_path / "work", args)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert proc.stdout == ""
    assert proc.stderr.strip().splitlines()[-1].endswith(MISSING_LINE), proc.stderr
    assert sum(MISSING_LINE in ln for ln in proc.stderr.splitlines()) == 1
    assert "unknown flag" not in proc.stderr
    assert list((tmp_path / "work").iterdir()) == []
    assert not (tmp_path / "tree" / ".hal0ai").exists()


def test_a_tree_missing_only_the_catalog_stops_too(tmp_path: Path) -> None:
    tree = _tree(tmp_path / "tree", wizard=True)
    (tree / "installer" / "manifests" / "setup-catalog.json").unlink()
    proc = _run(tree, tmp_path / "work", [])
    assert proc.returncode == 1
    assert proc.stderr.strip().splitlines()[-1].endswith(MISSING_LINE), proc.stderr


@pytest.mark.parametrize("flag", ["--no-hermse", "--box", "--answers"])
def test_an_unknown_flag_always_stops(tmp_path: Path, flag: str) -> None:
    tree = _tree(tmp_path / "tree", wizard=True)
    proc = _run(tree, tmp_path / "work", [flag])
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"unknown flag: {flag} (run with --help for the list)" in proc.stderr
    assert "ignored" not in proc.stderr
    assert MISSING_LINE not in proc.stderr


def test_no_prompt_helper_or_ignore_arm_is_left() -> None:
    for path in sorted(INSTALLER.rglob("*.sh")):
        text = path.read_text(encoding="utf-8")
        name = str(path.relative_to(REPO))
        assert "_interactive" not in text, name
        assert "_tty_read" not in text, name
        assert "(ignored)" not in text or "unknown flag" not in text, name
