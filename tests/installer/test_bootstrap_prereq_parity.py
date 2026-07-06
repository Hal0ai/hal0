"""Bootstrap-prereq parity — WS-B (#1098).

``installer/bootstrap.sh`` (the ``curl|bash`` one-liner) hard-requires a
Linux host plus ``curl``/``tar``/``sha256sum`` in its own ``preflight()``
before it ever fetches the release tarball. ``installer/install.sh`` leans
on all three later in its own run (the network probe's curl call, the
rsync-fallback tar copy, the FLM ``.deb`` sha256 check) but a direct
``sudo bash install.sh`` — no bootstrap in front — never checked for them
up front, so a minimal host missing one would sail past "Pre-flight
checks" and die deep in the run with a bare "command not found" instead
of an actionable message.

``preflight_bootstrap_prereqs`` (added to ``installer/lib/preflight.sh``)
closes that gap: it mirrors bootstrap.sh's checks and message style, and
``install.sh`` calls it, hard (``die`` on failure), as part of its
"Pre-flight checks" step — before any filesystem mutation.

These tests exercise the shell function directly (subprocess, real bash —
the same technique ``tests/installer/test_preflight_gpu_gate.py`` uses for
``preflight_gpu``) plus a couple of static-text assertions against
``install.sh`` proving the call site exists and runs early.
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
_BOOTSTRAP = _REPO_ROOT / "installer" / "bootstrap.sh"
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"


def _write_exe(path: Path, body: str = "#!/usr/bin/env bash\nexit 0\n") -> None:
    # Replace any existing entry (full_bin_dir seeds tools as symlinks to the
    # real binaries) rather than writing THROUGH it: write_text() follows a
    # symlink, so overwriting the seeded `uname` symlink would try to rewrite
    # the host's /usr/bin/uname — PermissionError on a locked-down CI runner,
    # or silent mutation of the real binary when the test runs as root.
    path.unlink(missing_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# preflight.sh only sources ui.sh / distro.sh when `info` / `pkg_install_cmd`
# aren't already defined (guards it uses so install.sh, which has already
# sourced both, doesn't source them twice). Pre-defining stubs here means
# preflight_bootstrap_prereqs's own `info`/`err` calls still work (and are
# capturable), while the *only* external binaries our restricted PATH needs
# to supply are the ones the test is deliberately controlling (uname, curl,
# tar, sha256sum) — not the dozen coreutils ui.sh/distro.sh would otherwise
# need (dirname, cd, pwd, grep, ...).
_STUBS = (
    "info() { printf 'INFO: %s\\n' \"$*\"; }\n"
    "warn() { printf 'WARN: %s\\n' \"$*\"; }\n"
    "err()  { printf 'ERR: %s\\n' \"$*\" >&2; }\n"
    'die()  { err "$*"; exit 1; }\n'
    "pkg_install_cmd() { :; }\n"
)


def _run_prereqs(
    bin_dir: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Source preflight.sh with PATH pinned to ``bin_dir`` and run the check.

    Pinning PATH to a controlled, minimal directory (rather than unsetting
    real tools from the test runner's PATH) is what makes "missing X" and
    "wrong OS" reproducible independent of the host running the suite.
    """
    script = (
        "set -euo pipefail\n" + _STUBS + f"source {_PREFLIGHT!s}\n"
        "rc=0\n"
        "preflight_bootstrap_prereqs || rc=$?\n"
        "exit $rc\n"
    )
    env = {"PATH": str(bin_dir), **(extra_env or {})}
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)


@pytest.fixture
def full_bin_dir(tmp_path: Path) -> Path:
    """A PATH dir with real bash/curl/tar/sha256sum/uname — the happy path."""
    d = tmp_path / "bin"
    d.mkdir()
    for tool in ("bash", "curl", "tar", "sha256sum", "uname"):
        found = shutil.which(tool)
        if found:
            os.symlink(found, d / tool)
    return d


def _fake_uname(d: Path, os_name: str) -> None:
    _write_exe(d / "uname", f'#!/usr/bin/env bash\necho "{os_name}"\n')


class TestAllPresentOnLinux:
    def test_returns_zero(self, full_bin_dir: Path) -> None:
        proc = _run_prereqs(full_bin_dir)
        assert proc.returncode == 0, proc.stderr

    def test_reports_ok(self, full_bin_dir: Path) -> None:
        proc = _run_prereqs(full_bin_dir)
        assert "curl, tar, sha256sum present" in proc.stdout + proc.stderr


class TestMissingDependency:
    @pytest.mark.parametrize("missing", ["curl", "tar", "sha256sum"])
    def test_missing_tool_is_fatal(self, full_bin_dir: Path, missing: str) -> None:
        (full_bin_dir / missing).unlink()
        proc = _run_prereqs(full_bin_dir)
        assert proc.returncode != 0
        assert f"missing dependency: {missing}" in proc.stdout + proc.stderr

    def test_missing_tool_message_says_install_and_rerun(self, full_bin_dir: Path) -> None:
        (full_bin_dir / "tar").unlink()
        proc = _run_prereqs(full_bin_dir)
        assert "install it and re-run" in proc.stdout + proc.stderr

    def test_all_missing_reports_all_three(self, tmp_path: Path) -> None:
        d = tmp_path / "bin"
        d.mkdir()
        for tool in ("bash", "uname"):
            found = shutil.which(tool)
            if found:
                os.symlink(found, d / tool)
        proc = _run_prereqs(d)
        assert proc.returncode != 0
        out = proc.stdout + proc.stderr
        assert "missing dependency: curl" in out
        assert "missing dependency: tar" in out
        assert "missing dependency: sha256sum" in out


class TestNonLinuxHost:
    def test_darwin_is_rejected(self, full_bin_dir: Path) -> None:
        _fake_uname(full_bin_dir, "Darwin")
        proc = _run_prereqs(full_bin_dir)
        assert proc.returncode != 0
        assert "only supports Linux" in proc.stdout + proc.stderr
        assert "Darwin" in proc.stdout + proc.stderr

    def test_linux_is_accepted(self, full_bin_dir: Path) -> None:
        _fake_uname(full_bin_dir, "Linux")
        proc = _run_prereqs(full_bin_dir)
        assert proc.returncode == 0, proc.stderr


class TestMirrorsBootstrapSh:
    """Same checks bootstrap.sh's own ``preflight()`` performs."""

    def test_bootstrap_sh_checks_the_same_four_things(self) -> None:
        text = _BOOTSTRAP.read_text(encoding="utf-8")
        m = re.search(r"preflight\(\) \{(.*?)\n\}", text, re.DOTALL)
        assert m is not None, "bootstrap.sh preflight() not found"
        body = m.group(1)
        assert "uname -s" in body and "Linux" in body
        assert "need curl" in body
        assert "need tar" in body
        assert "need sha256sum" in body


class TestInstallShWiring:
    """install.sh must call the parity check, hard, early in its own run."""

    def test_install_sh_calls_preflight_bootstrap_prereqs(self) -> None:
        text = _INSTALL_SH.read_text(encoding="utf-8")
        assert "preflight_bootstrap_prereqs" in text

    def test_failure_is_fatal_not_a_warning(self) -> None:
        text = _INSTALL_SH.read_text(encoding="utf-8")
        m = re.search(r"preflight_bootstrap_prereqs \|\| (\w+)", text)
        assert m is not None, "no `preflight_bootstrap_prereqs || ...` call site in install.sh"
        assert m.group(1) == "die", "a missing base prereq must abort the install, not just warn"

    def test_runs_before_any_filesystem_mutation(self) -> None:
        # "Filesystem layout" is the first ui_step that actually mutates the
        # host (mkdir -p ...). The prereq check must run strictly before it.
        text = _INSTALL_SH.read_text(encoding="utf-8")
        prereq_idx = text.index("preflight_bootstrap_prereqs ||")
        mkdir_step_idx = text.index('ui_step "Filesystem layout"')
        assert prereq_idx < mkdir_step_idx

    def test_preflight_all_also_runs_it(self) -> None:
        # `hal0 doctor` (preflight_all) should report the same floor.
        text = _PREFLIGHT.read_text(encoding="utf-8")
        m = re.search(r"preflight_all\(\) \{(.*?)\n\}", text, re.DOTALL)
        assert m is not None
        assert "preflight_bootstrap_prereqs" in m.group(1)


def test_bash_syntax_check() -> None:
    for path in (_INSTALL_SH, _PREFLIGHT, _BOOTSTRAP):
        proc = subprocess.run(
            ["bash", "-n", str(path)], capture_output=True, text=True, check=False
        )
        assert proc.returncode == 0, f"{path}: {proc.stderr}"
