"""git preflight for Hermes provisioning — #1726.

``hal0 agent install hermes`` pip-installs hermes-agent straight from a git
URL (``pip install git+https://github.com/NousResearch/hermes-agent.git@...``),
which pip implements by shelling out to ``git clone``. Before this fix,
nothing in installer/install.sh checked that ``git`` was on PATH before
that step ran, so a stock minimal distro image without it (a fresh
Ubuntu 24.04 LXC/cloud template — no git preinstalled) sailed through
every earlier preflight check, printed "=== hal0 is ready ===", and only
surfaced the failure later via ``hal0 doctor all`` ("WARN Hermes systemd
unit inactive or absent").

``preflight_git`` (added to installer/lib/preflight.sh) closes that gap,
mirroring ``preflight_container_runtime``'s two-mode shape:

  * soft (default, ``hal0 doctor``) — warn + return 1 when git is missing,
    never mutates the system.
  * required (``HAL0_GIT_REQUIRED=1``, set by install.sh immediately
    before the Hermes provisioning step) — auto-install git via the
    detected package manager; hard-fail (return 1) with a remediation
    one-liner if that doesn't resolve it.

These tests exercise the shell function directly (subprocess, real bash —
the same technique ``tests/installer/test_preflight_python_floor.py`` and
``tests/installer/test_bootstrap_prereq_parity.py`` use) against a
restricted PATH, plus static-text assertions proving install.sh's Hermes
provisioning block gates on ``preflight_git`` and that a git-install
failure degrades gracefully (mirrors the #1584 precedent: it does not
``die()`` the overall install, it fails only the Hermes step loudly with
a git-specific remediation line).
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
)


def _write_exe(path: Path, body: str = "#!/usr/bin/env bash\nexit 0\n") -> None:
    path.unlink(missing_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# preflight.sh only sources ui.sh / distro.sh when `info` / `pkg_install_cmd`
# aren't already defined. Our _STUBS pre-defines info/warn/err/die (so ui.sh
# is skipped) but deliberately leaves pkg_install_cmd undefined so distro.sh
# gets sourced for real — preflight_git's pkg_mgr()/pkg_install_cmd() calls
# need real distro.sh logic (probing the restricted PATH via `command -v`)
# to meaningfully exercise the apt-get auto-install branch below. That
# source needs `dirname` on PATH; the apt-get-mock scripts need `cat`.
_BASE_TOOLS = ("bash", "dirname", "cat")


def _seed_base_tools(d: Path) -> None:
    for tool in _BASE_TOOLS:
        if (d / tool).exists():
            continue
        found = shutil.which(tool)
        assert found is not None, f"no {tool} on the test runner's PATH"
        os.symlink(found, d / tool)


def _run(script_body: str, bin_dir: Path, extra_env: dict[str, str] | None = None):
    # subprocess.run(["bash", ...]) resolves "bash" against the PATH we hand
    # it, not the test runner's own PATH — seed real base tools alongside the
    # restricted bin dir (same technique as test_preflight_python_floor.py).
    _seed_base_tools(bin_dir)
    script = "set -euo pipefail\n" + _STUBS + f"source {_PREFLIGHT!s}\n" + script_body
    env = {"PATH": str(bin_dir), **(extra_env or {})}
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)


@pytest.fixture
def bin_dir_without_git(tmp_path: Path) -> Path:
    """A restricted PATH dir with no git and no recognised package manager."""
    d = tmp_path / "bin"
    d.mkdir()
    return d


@pytest.fixture
def bin_dir_with_git(tmp_path: Path) -> Path:
    d = tmp_path / "bin"
    d.mkdir()
    _write_exe(d / "git", "#!/usr/bin/env bash\necho 'git version 2.43.0'\nexit 0\n")
    return d


class TestPreflightGitSoftMode:
    """Default mode (HAL0_GIT_REQUIRED unset) — used by `hal0 doctor` /
    preflight_all. Never mutates the system."""

    def test_git_present_passes(self, bin_dir_with_git: Path) -> None:
        proc = _run("preflight_git; exit $?\n", bin_dir_with_git)
        assert proc.returncode == 0, proc.stderr
        assert "git" in proc.stdout.lower()

    def test_git_absent_warns_and_returns_nonzero(self, bin_dir_without_git: Path) -> None:
        proc = _run("rc=0; preflight_git || rc=$?; exit $rc\n", bin_dir_without_git)
        assert proc.returncode != 0
        out = proc.stdout + proc.stderr
        assert "git not found" in out
        assert "Hermes" in out

    def test_git_absent_does_not_attempt_install(self, bin_dir_without_git: Path) -> None:
        # No pkg_mgr candidate (apt-get/dnf/...) exists on the restricted
        # PATH; soft mode must not even try to call one.
        proc = _run("rc=0; preflight_git || rc=$?; exit $rc\n", bin_dir_without_git)
        assert proc.returncode != 0
        assert "installing git" not in (proc.stdout + proc.stderr)


class TestPreflightGitRequiredMode:
    """HAL0_GIT_REQUIRED=1 — install.sh's mode right before Hermes
    provisioning. Auto-installs via the detected package manager (mocked
    apt-get here), hard-fails with a remediation line otherwise."""

    def test_git_already_present_short_circuits(self, bin_dir_with_git: Path) -> None:
        proc = _run(
            "preflight_git; exit $?\n", bin_dir_with_git, extra_env={"HAL0_GIT_REQUIRED": "1"}
        )
        assert proc.returncode == 0, proc.stderr
        assert "installing git" not in (proc.stdout + proc.stderr)

    def test_missing_git_triggers_auto_install_via_apt_get(self, tmp_path: Path) -> None:
        d = tmp_path / "bin"
        d.mkdir()
        # Mock apt-get: records its invocation to a marker file, then drops a
        # fake `git` onto PATH so the post-install `command -v git` re-check
        # succeeds — proves preflight_git actually re-verifies after install
        # rather than trusting the exit code blindly.
        marker = tmp_path / "apt-get.called"
        apt_get_body = f"""#!/usr/bin/env bash
echo "$@" > {marker}
cat > {d / "git"} <<'GITEOF'
#!/usr/bin/env bash
echo 'git version 2.43.0'
exit 0
GITEOF
chmod +x {d / "git"}
exit 0
"""
        _write_exe(d / "apt-get", apt_get_body)
        proc = _run("preflight_git; exit $?\n", d, extra_env={"HAL0_GIT_REQUIRED": "1"})
        assert proc.returncode == 0, proc.stderr
        assert marker.exists(), "apt-get was never invoked — auto-install path not taken"
        assert "-y" in marker.read_text()
        assert "git" in marker.read_text()

    def test_apt_get_present_but_install_fails_is_a_hard_failure(self, tmp_path: Path) -> None:
        d = tmp_path / "bin"
        d.mkdir()
        # apt-get "succeeds" (exit 0) but never actually drops a git binary
        # (e.g. package unavailable in a stale/offline apt cache) — the
        # post-install re-check must catch this and fail closed, not report
        # success just because the package-manager invocation didn't error.
        _write_exe(d / "apt-get", "#!/usr/bin/env bash\nexit 0\n")
        proc = _run(
            "rc=0; preflight_git || rc=$?; exit $rc\n", d, extra_env={"HAL0_GIT_REQUIRED": "1"}
        )
        assert proc.returncode != 0

    def test_no_package_manager_hard_fails_with_remediation(
        self, bin_dir_without_git: Path
    ) -> None:
        proc = _run(
            "pkg_mgr() { return 1; }\n"
            "pkg_install_cmd() { return 1; }\n"
            "rc=0; preflight_git || rc=$?; exit $rc\n",
            bin_dir_without_git,
            extra_env={"HAL0_GIT_REQUIRED": "1"},
        )
        assert proc.returncode != 0
        out = proc.stdout + proc.stderr
        assert "git" in out.lower()


class TestInstallShWiring:
    """install.sh must gate Hermes provisioning on preflight_git, and a git
    failure there must degrade gracefully (mirrors #1584's established
    precedent for Hermes provisioning failures: warn + remediation, not a
    fatal die() of the whole install)."""

    def test_install_sh_gates_hermes_provisioning_on_preflight_git(self) -> None:
        text = _INSTALL_SH.read_text(encoding="utf-8")
        m = re.search(
            r"provisioning Hermes agent \(toolchain \+ bootstrap\)",
            text,
        )
        assert m is not None, "Hermes provisioning info line not found in install.sh"
        # The preflight_git gate must appear in the same Hermes-provisioning
        # if/elif/else block, immediately ahead of the actual provisioning
        # call, not merely somewhere else in the file.
        window_start = max(0, m.start() - 1500)
        window = text[window_start : m.start()]
        assert "preflight_git" in window
        assert "HAL0_GIT_REQUIRED=1" in window

    def test_git_preflight_failure_does_not_die_the_install(self) -> None:
        text = _INSTALL_SH.read_text(encoding="utf-8")
        m = re.search(
            r"elif ! HAL0_GIT_REQUIRED=1 preflight_git; then(.*?)\n        else\n",
            text,
            re.DOTALL,
        )
        assert m is not None, "preflight_git gate branch not found in install.sh"
        body = m.group(1)
        # This branch must warn with a remediation line, not die()/exit the
        # installer — a git-install failure is a Hermes-provisioning-only
        # failure, consistent with every other failure mode in this block.
        assert re.search(r"\bdie\b", body) is None
        assert "exit" not in body
        assert "warn" in body
        assert "git" in body.lower()
        assert "agent install hermes" in body

    def test_preflight_all_includes_git(self) -> None:
        text = _PREFLIGHT.read_text(encoding="utf-8")
        assert re.search(r"preflight_git\s*\|\|\s*rc=\$\?", text)


def test_bash_syntax_check() -> None:
    for path in (_INSTALL_SH, _PREFLIGHT):
        proc = subprocess.run(
            ["bash", "-n", str(path)], capture_output=True, text=True, check=False
        )
        assert proc.returncode == 0, f"{path}: {proc.stderr}"
