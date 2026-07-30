"""#1464 — argv contract of ``installer/wrappers/hal0-update``.

The sudoers grant is pinned to this binary, so the wrapper's ``case`` statement
IS the allow-list: whatever it forwards, the ``hal0`` service account can make
root do. These tests exercise the real script against a stub interpreter, so
they need no sudo, no root and no provisioned box.

The Python side re-validates everything (``hal0.updater.privileged.main``); this
suite pins the *shell* half — that a bad channel / version / directory token
never reaches the interpreter at all, and that a good one is forwarded verbatim.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[2] / "installer" / "wrappers" / "hal0-update"

_STUB_PY = """#!/bin/bash
# Records argv so the test can assert exactly what the wrapper forwarded.
printf '%s\\n' "$@" > "${HAL0_TEST_ARGV_SINK}"
"""


@pytest.fixture
def installed(tmp_path: Path) -> Path:
    """Lay the wrapper out exactly as install.sh does: <lib>/bin + <lib>/venv."""
    lib = tmp_path / "lib" / "hal0"
    (lib / "bin").mkdir(parents=True)
    (lib / "venv" / "bin").mkdir(parents=True)
    shutil.copy2(WRAPPER, lib / "bin" / "hal0-update")
    (lib / "bin" / "hal0-update").chmod(0o755)
    stub = lib / "venv" / "bin" / "python"
    stub.write_text(_STUB_PY)
    stub.chmod(0o755)
    return lib


def _run(installed: Path, *args: str) -> tuple[int, list[str], str]:
    sink = installed / "argv.txt"
    proc = subprocess.run(
        [str(installed / "bin" / "hal0-update"), *args],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HAL0_TEST_ARGV_SINK": str(sink)},
        check=False,
    )
    forwarded = sink.read_text().splitlines() if sink.exists() else []
    return proc.returncode, forwarded, proc.stderr


# ── accepted verbs ─────────────────────────────────────────────────────────────


def test_check_forwards_the_probe_verb(installed: Path) -> None:
    rc, argv, _ = _run(installed, "check")
    assert rc == 0
    assert argv == ["-I", "-m", "hal0.updater.privileged", "check"]


def test_stage_forwards_channel_and_optional_version(installed: Path) -> None:
    rc, argv, _ = _run(installed, "stage", "stable", "1.0.0")
    assert rc == 0
    assert argv == ["-I", "-m", "hal0.updater.privileged", "stage", "stable", "1.0.0"]

    rc, argv, _ = _run(installed, "stage", "nightly")
    assert rc == 0
    assert argv == ["-I", "-m", "hal0.updater.privileged", "stage", "nightly"]


def test_activate_and_discard_forward_the_dir_token(installed: Path) -> None:
    rc, argv, _ = _run(installed, "activate", "hal0-1.0.0")
    assert rc == 0
    assert argv[-2:] == ["activate", "hal0-1.0.0"]

    rc, argv, _ = _run(installed, "discard", "hal0-1.0.0-rc.1")
    assert rc == 0
    assert argv[-2:] == ["discard", "hal0-1.0.0-rc.1"]


def test_isolated_mode_is_always_used(installed: Path) -> None:
    """`-I` drops PYTHON* env vars, user site, and the CWD from sys.path.

    Without it a caller-controlled working directory containing a ``hal0/``
    package would be imported by a ROOT interpreter.
    """
    for args in (("check",), ("stage", "stable"), ("activate", "hal0-1.0.0")):
        _, argv, _ = _run(installed, *args)
        assert argv[0] == "-I"


def test_help_lists_exactly_the_granted_verbs(installed: Path) -> None:
    proc = subprocess.run(
        [str(installed / "bin" / "hal0-update"), "help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    for verb in ("check", "stage", "activate", "discard"):
        assert verb in proc.stdout


# ── rejected input (never reaches the interpreter) ─────────────────────────────


@pytest.mark.parametrize(
    "args",
    [
        ("rm-rf",),
        ("write-unit", "x"),
        ("stage",),
        ("stage", "evil"),
        ("stage", "stable; rm -rf /"),
        ("stage", "stable", "../../etc"),
        ("stage", "stable", "1.0.0; whoami"),
        ("stage", "stable", "-rf"),
        ("activate",),
        ("activate", "hal0-1.0.0/../../etc"),
        ("activate", "../hal0-1.0.0"),
        ("activate", "hal0-.."),
        ("activate", "/usr/lib/hal0/hal0-1.0.0"),
        ("activate", "venv"),
        ("activate", "current"),
        ("discard",),
        ("discard", "hal0-1.0.0 ; reboot"),
        ("discard", "hal0-" + "a" * 200),
    ],
)
def test_bad_input_is_refused_before_the_interpreter_runs(
    installed: Path, args: tuple[str, ...]
) -> None:
    rc, forwarded, stderr = _run(installed, *args)
    assert rc == 64, f"expected refusal for {args!r}"
    assert forwarded == [], f"{args!r} reached the interpreter"
    assert "hal0-update:" in stderr


def test_missing_interpreter_fails_loudly(installed: Path) -> None:
    (installed / "venv" / "bin" / "python").unlink()
    rc, _, stderr = _run(installed, "check")
    assert rc == 64
    assert "interpreter not found" in stderr


def test_wrapper_resolves_the_venv_from_its_own_location(tmp_path: Path) -> None:
    """A non-default HAL0_PREFIX install must work — no hardcoded /usr/lib."""
    lib = tmp_path / "opt" / "custom" / "hal0"
    (lib / "bin").mkdir(parents=True)
    (lib / "venv" / "bin").mkdir(parents=True)
    shutil.copy2(WRAPPER, lib / "bin" / "hal0-update")
    (lib / "bin" / "hal0-update").chmod(0o755)
    stub = lib / "venv" / "bin" / "python"
    stub.write_text(_STUB_PY)
    stub.chmod(0o755)

    rc, argv, _ = _run(lib, "check")
    assert rc == 0
    assert argv[-1] == "check"


def test_wrapper_and_python_dir_regexes_agree() -> None:
    """The shell copy is a fail-fast convenience; drift makes it a lie."""
    from hal0.updater.updater import RELEASE_DIR_RE

    text = WRAPPER.read_text()
    assert RELEASE_DIR_RE.pattern.strip("^$") in text


def test_wrapper_channel_list_matches_python() -> None:
    from hal0.updater.privileged import CHANNELS

    text = WRAPPER.read_text()
    assert (
        "|".join(sorted(CHANNELS, key=lambda c: ("stable", "preview", "nightly").index(c))) in text
    )
