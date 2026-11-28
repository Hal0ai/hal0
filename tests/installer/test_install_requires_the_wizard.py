"""installer/install.sh needs the setup screens and has no path without them.

A release tree that lacks the wizard (its entry point or its catalog) stops
with one line before anything runs; a tree that has it stops on every unknown
flag. The tests run the real ``install.sh`` out of a scratch tree whose
``installer/`` entries are links to this checkout's files, so ``REPO_ROOT`` is
the scratch tree and the presence of the wizard is the tree's own contents.
"""

from __future__ import annotations

import glob
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


def _run(
    tree: Path, work: Path, args: list[str], *, log: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the tree's install.sh from an empty working directory.

    ``log`` presets HAL0_INSTALL_LOG, which makes ``hal0_install_log_init`` a
    no-op: a run that is expected to get past the log set-up (an unknown flag)
    then creates no file under /tmp. A run that must stop BEFORE the log
    exists leaves it unset, so a created log would show up.
    """
    work.mkdir()
    (work / "tmp").mkdir()
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(work),
        "TMPDIR": str(work / "tmp"),
        "HAL0_PLAIN": "1",
    }
    if log is not None:
        env["HAL0_INSTALL_LOG"] = str(log)
    return subprocess.run(
        ["bash", str(tree / "installer" / "install.sh"), *args],
        capture_output=True,
        text=True,
        cwd=work,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


def _install_logs() -> set[str]:
    """Install logs on this machine. The installer names them under /tmp (or
    /var/log/hal0 as root) whatever TMPDIR says, so look there; reading only."""
    return set(glob.glob("/tmp/hal0-install-*.log")) | set(glob.glob("/var/log/hal0/install-*.log"))


def _assert_stopped_at_the_check(
    proc: subprocess.CompletedProcess[str], work: Path, before: set[str]
) -> None:
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert proc.stdout == ""
    lines = proc.stderr.strip().splitlines()
    assert len(lines) == 1, proc.stderr
    assert lines[0].endswith(MISSING_LINE), proc.stderr
    assert [p.name for p in work.iterdir()] == ["tmp"]
    assert list((work / "tmp").iterdir()) == []
    assert _install_logs() == before, "an install log was created before the stop"


@pytest.mark.parametrize("args", [[], ["--dev"], ["--no-owui"], ["--help"], ["--bogus"]])
def test_a_tree_without_the_wizard_stops_with_one_line(tmp_path: Path, args: list[str]) -> None:
    tree = _tree(tmp_path / "tree", wizard=False)
    before = _install_logs()
    proc = _run(tree, tmp_path / "work", args)
    _assert_stopped_at_the_check(proc, tmp_path / "work", before)
    assert "unknown flag" not in proc.stderr
    assert not (tree / ".hal0ai").exists()


@pytest.mark.parametrize("missing", ["entry", "catalog"])
def test_a_tree_missing_one_wizard_file_stops_too(tmp_path: Path, missing: str) -> None:
    tree = _tree(tmp_path / "tree", wizard=True)
    victim = {
        "entry": tree / "src" / "hal0" / "install" / "wizard" / "__main__.py",
        "catalog": tree / "installer" / "manifests" / "setup-catalog.json",
    }[missing]
    victim.unlink()
    before = _install_logs()
    proc = _run(tree, tmp_path / "work", [])
    _assert_stopped_at_the_check(proc, tmp_path / "work", before)


@pytest.mark.parametrize("flag", ["--no-hermse", "--box", "--answers"])
def test_an_unknown_flag_always_stops(tmp_path: Path, flag: str) -> None:
    tree = _tree(tmp_path / "tree", wizard=True)
    proc = _run(tree, tmp_path / "work", [flag], log=tmp_path / "install.log")
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
