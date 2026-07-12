"""Python floor parity — installer/lib/preflight.sh vs pyproject.toml.

pyproject.toml pins ``requires-python = ">=3.12"``, but preflight_python
used to accept 3.11 and only warn (not fail) on 3.10 — every stock Debian
12 / Ubuntu 22.04 system python3. install.sh treated that as non-fatal
("pip may still work") and only died minutes later, deep inside
``pip install``, with a recovery hint (``HAL0_PYTHON=python3.12``) that's a
dead end on those distros' base repos (no python3.12 package).

preflight_python now enforces the same >=3.12 floor as pyproject.toml, and
resolve_main_python (+ _main_py_autoinstall) resolves — or, when asked,
auto-installs — a compatible interpreter, mirroring
resolve_hindsight_python's pattern for the (separate) Hindsight venv.

These tests exercise the shell functions directly (subprocess, real bash —
same technique as ``tests/installer/test_bootstrap_prereq_parity.py`` and
``tests/installer/test_preflight_gpu_gate.py``) against FAKE python
interpreters that report a fixed version regardless of the real
interpreter running the test suite, plus a couple of static-text
assertions against install.sh proving the die-on-unresolved wiring exists.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PREFLIGHT = _REPO_ROOT / "installer" / "lib" / "preflight.sh"
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"

_STUBS = (
    "info() { printf 'INFO: %s\\n' \"$*\"; }\n"
    "warn() { printf 'WARN: %s\\n' \"$*\"; }\n"
    "err()  { printf 'ERR: %s\\n' \"$*\" >&2; }\n"
    'die()  { err "$*"; exit 1; }\n'
    "pkg_install_cmd() { :; }\n"
)


def _write_fake_python(path: Path, version: str) -> None:
    """A fake `pythonX` that reports ``version`` (e.g. "3.11") regardless
    of the code it's asked to run — it only pattern-matches the two exact
    snippets preflight.sh feeds an interpreter."""
    major, minor = version.split(".")
    body = f"""#!/usr/bin/env bash
if [[ "$1" == "-c" ]]; then
    case "$2" in
        *'sys.version_info[:2]'*) echo "{major}.{minor}" ;;
        *'sys.version_info[1]'*) echo "{minor}" ;;
        *) exit 1 ;;
    esac
    exit 0
fi
exit 0
"""
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run(script_body: str, bin_dir: Path, extra_env: dict[str, str] | None = None):
    # subprocess.run(["bash", ...]) resolves "bash" against the PATH we
    # hand it (not the test runner's own PATH), so the restricted bin_dir
    # needs a real bash alongside the fake python interpreters the test
    # seeded — same technique as test_bootstrap_prereq_parity.py's
    # full_bin_dir fixture.
    if not (bin_dir / "bash").exists():
        found = shutil.which("bash")
        assert found is not None, "no bash on the test runner's PATH"
        os.symlink(found, bin_dir / "bash")
    script = "set -euo pipefail\n" + _STUBS + f"source {_PREFLIGHT!s}\n" + script_body
    env = {"PATH": str(bin_dir), **(extra_env or {})}
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)


class TestPreflightPythonFloor:
    @pytest.mark.parametrize("version", ["3.12", "3.13", "3.14"])
    def test_at_or_above_floor_passes(self, tmp_path: Path, version: str) -> None:
        d = tmp_path / "bin"
        d.mkdir()
        _write_fake_python(d / "python3", version)
        proc = _run("preflight_python; exit $?\n", d)
        assert proc.returncode == 0, proc.stderr
        assert version in proc.stdout

    @pytest.mark.parametrize("version", ["3.11", "3.10"])
    def test_below_floor_fails(self, tmp_path: Path, version: str) -> None:
        d = tmp_path / "bin"
        d.mkdir()
        _write_fake_python(d / "python3", version)
        proc = _run("rc=0; preflight_python || rc=$?; exit $rc\n", d)
        assert proc.returncode != 0
        out = proc.stdout + proc.stderr
        assert "hal0 requires >=3.12" in out

    def test_3_11_is_no_longer_accepted(self, tmp_path: Path) -> None:
        # Regression guard: 3.11 used to be in the accepted regex
        # (^3\.(11|12|13|14)$) — pin the floor at 3.12 going forward.
        d = tmp_path / "bin"
        d.mkdir()
        _write_fake_python(d / "python3", "3.11")
        proc = _run("rc=0; preflight_python || rc=$?; exit $rc\n", d)
        assert proc.returncode != 0


class TestResolveMainPython:
    def test_default_already_at_floor_is_used_as_is(self, tmp_path: Path) -> None:
        d = tmp_path / "bin"
        d.mkdir()
        _write_fake_python(d / "python3", "3.13")
        proc = _run("resolve_main_python\n", d)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip().endswith("python3")

    def test_finds_a_floor_python_already_on_path(self, tmp_path: Path) -> None:
        d = tmp_path / "bin"
        d.mkdir()
        _write_fake_python(d / "python3", "3.11")  # below floor
        _write_fake_python(d / "python3.12", "3.12")  # on PATH, in-band
        proc = _run("resolve_main_python\n", d)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip().endswith("python3.12")

    def test_no_floor_python_and_no_autoinstall_fails(self, tmp_path: Path) -> None:
        d = tmp_path / "bin"
        d.mkdir()
        _write_fake_python(d / "python3", "3.11")
        # HAL0_PY_AUTOINSTALL unset — resolve_main_python must not attempt
        # to mutate the system (mirrors resolve_hindsight_python's same
        # read-only-by-default contract) and must fail closed.
        proc = _run("rc=0; resolve_main_python || rc=$?; exit $rc\n", d)
        assert proc.returncode != 0

    def test_autoinstall_failure_is_non_fatal_and_returns_1(self, tmp_path: Path) -> None:
        # HAL0_PY_AUTOINSTALL=1 but no real apt-get on PATH (restricted bin
        # dir) — the auto-install attempt fails harmlessly (command not
        # found -> `|| return 1`), never touching the network or the host.
        d = tmp_path / "bin"
        d.mkdir()
        _write_fake_python(d / "python3", "3.11")
        script = (
            "pkg_mgr() { echo apt-get; return 0; }\n"
            "distro_family() { echo debian; return 0; }\n"
            "rc=0; resolve_main_python || rc=$?; exit $rc\n"
        )
        proc = _run(script, d, extra_env={"HAL0_PY_AUTOINSTALL": "1"})
        assert proc.returncode != 0


class TestInstallShWiring:
    """install.sh must hard-die on an unresolved below-floor interpreter,
    not fall through with just a warning (the old "pip may still work"
    posture)."""

    def test_install_sh_calls_resolve_main_python(self) -> None:
        text = _INSTALL_SH.read_text(encoding="utf-8")
        assert "resolve_main_python" in text

    def test_unresolved_floor_mismatch_is_fatal(self) -> None:
        text = _INSTALL_SH.read_text(encoding="utf-8")
        m = re.search(
            r"if ! preflight_python; then(.*?)\nfi\n",
            text,
            re.DOTALL,
        )
        assert m is not None, "preflight_python guard block not found in install.sh"
        body = m.group(1)
        assert "resolve_main_python" in body
        assert re.search(r"\bdie\b", body), (
            "a below-floor interpreter that can't be resolved must die(), "
            "not just warn and continue"
        )

    def test_preflight_regex_floor_is_3_12(self) -> None:
        text = _PREFLIGHT.read_text(encoding="utf-8")
        assert r"^3\.(12|13|14)$" in text
        assert r"^3\.(11|12|13|14)$" not in text


def test_bash_syntax_check() -> None:
    for path in (_INSTALL_SH, _PREFLIGHT):
        proc = subprocess.run(
            ["bash", "-n", str(path)], capture_output=True, text=True, check=False
        )
        assert proc.returncode == 0, f"{path}: {proc.stderr}"
