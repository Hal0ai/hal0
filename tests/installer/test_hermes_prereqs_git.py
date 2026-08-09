"""git coverage in installer/agents/hermes-prereqs.sh — #1726 / #1727 review.

Codex review on PR #1727 flagged that the original fix only gated
``installer/install.sh``'s INLINE Hermes provisioning call
(``"${HAL0_BIN}" agent install hermes``) on a git preflight — the
STANDALONE/deferred path (an operator running ``hal0 agent install
hermes`` manually, e.g. after ``HAL0_SKIP_HERMES=1``, or per the
remediation hint printed on a failed inline provision) goes through
``hal0.cli.agent_commands._install_hermes`` →
``installer/agents/hermes-prereqs.sh`` directly and never touched
install.sh's gate. That path stayed exposed to the original bug: a
git-less box would still hit pip's "Cannot find command 'git'" untouched.

``hermes-prereqs.sh`` is the SHARED choke point for both entry points
(install.sh shells out to the exact same ``hal0 agent install hermes``
CLI command, which runs this same script) — this fix adds git to its
existing toolchain probe/install/verify pipeline (mirroring how it
already handles python3-venv/pip/pipx), which covers both call sites at
once.

These tests exercise the real script end-to-end via subprocess (same
technique as ``tests/installer/test_bootstrap_prereq_parity.py``), with a
minimal fake toolchain (python3.12 + pipx already "present") so only the
git-specific behavior is under test, plus static-text assertions proving
git is wired into every distro family's package list and the
functional (not just presence) verification.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PREREQS = _REPO_ROOT / "installer" / "agents" / "hermes-prereqs.sh"


def _write_exe(path: Path, body: str) -> None:
    path.unlink(missing_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


_FAKE_PYTHON312 = """#!/usr/bin/env bash
if [[ "$1" == "-c" ]]; then
    exit 0
fi
if [[ "$1" == "-m" && "$2" == "pip" ]]; then
    echo "pip 24.0"
    exit 0
fi
exit 0
"""

_FAKE_PIPX = "#!/usr/bin/env bash\nexit 0\n"

_FAKE_GIT_WORKING = "#!/usr/bin/env bash\necho 'git version 2.43.0'\nexit 0\n"


@pytest.fixture
def base_bin_dir(tmp_path: Path) -> Path:
    """Real bash + a fake, always-satisfied python3.12/pipx toolchain — only
    git varies per test."""
    d = tmp_path / "bin"
    d.mkdir()
    for tool in ("bash", "dirname", "cat", "uname", "id", "chmod"):
        found = shutil.which(tool)
        if found:
            os.symlink(found, d / tool)
    _write_exe(d / "python3.12", _FAKE_PYTHON312)
    _write_exe(d / "pipx", _FAKE_PIPX)
    # Tests run as a non-root user, so hermes-prereqs.sh's `id -u` check
    # keeps the `sudo ` prefix pkg_install_cmd emits — a passthrough fake
    # sudo keeps the install commands runnable without real privileges.
    _write_exe(d / "sudo", "#!/usr/bin/env bash\nexec \"$@\"\n")
    return d


def _run_prereqs(bin_dir: Path) -> subprocess.CompletedProcess:
    env = {"PATH": str(bin_dir)}
    return subprocess.run(
        ["bash", str(_PREREQS)], env=env, capture_output=True, text=True, check=False
    )


class TestGitAlreadyPresent:
    def test_toolchain_complete_with_git_skips_install(self, base_bin_dir: Path) -> None:
        _write_exe(base_bin_dir / "git", _FAKE_GIT_WORKING)
        proc = _run_prereqs(base_bin_dir)
        assert proc.returncode == 0, proc.stderr
        assert "toolchain already present" in proc.stdout
        assert "git" in proc.stdout
        # No apt-get should have been invoked — everything was already there.
        assert not (base_bin_dir / "apt-get.called").exists()


class TestGitMissingTriggersInstall:
    def test_missing_git_installs_it_via_apt_get(self, base_bin_dir: Path) -> None:
        marker = base_bin_dir / "apt-get.called"
        apt_get_body = f"""#!/usr/bin/env bash
echo "$@" >> {marker}
if [[ "$*" == *git* ]]; then
    cat > {base_bin_dir / "git"} <<'GITEOF'
{_FAKE_GIT_WORKING}
GITEOF
    chmod +x {base_bin_dir / "git"}
fi
exit 0
"""
        _write_exe(base_bin_dir / "apt-get", apt_get_body)
        proc = _run_prereqs(base_bin_dir)
        assert proc.returncode == 0, proc.stderr
        assert marker.exists(), "apt-get was never invoked for the missing-git case"
        assert "git" in marker.read_text()
        assert "toolchain ready" in proc.stdout

    def test_apt_get_succeeds_but_git_still_unusable_is_fatal(self, base_bin_dir: Path) -> None:
        # apt-get "succeeds" (exit 0) but never actually drops a working git
        # (stale/offline apt cache, package missing from the mirror) — the
        # post-install verification must catch this and die, not report
        # "toolchain ready" only for the very next `pip install git+...` to
        # fail with the original opaque error.
        _write_exe(base_bin_dir / "apt-get", "#!/usr/bin/env bash\nexit 0\n")
        proc = _run_prereqs(base_bin_dir)
        assert proc.returncode != 0
        out = proc.stdout + proc.stderr
        assert "git" in out.lower()
        assert "still missing or not runnable" in out

    def test_non_executable_git_on_path_is_not_trusted(self, base_bin_dir: Path) -> None:
        # A `git` file that resolves on PATH but can't actually run (matches
        # the Codex "command -v doesn't prove it executes" finding) must be
        # treated the same as git being fully absent.
        bogus = base_bin_dir / "git"
        bogus.write_text("not a real executable\n", encoding="utf-8")
        bogus.chmod(0o644)  # deliberately non-executable
        _write_exe(base_bin_dir / "apt-get", "#!/usr/bin/env bash\nexit 0\n")
        proc = _run_prereqs(base_bin_dir)
        assert proc.returncode != 0
        assert "still missing or not runnable" in (proc.stdout + proc.stderr)


class TestStaticWiring:
    """Every distro family's package list must include git, and the
    functional (not just presence) predicate must be in place."""

    _TEXT = _PREREQS.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "family_line",
        [
            "debian) pkgs=(python3 python3-venv python3-pip pipx git) ;;",
            "fedora) pkgs=(python3 python3-pip pipx git) ;;",
            "arch) pkgs=(python python-pip python-pipx git) ;;",
            "suse) pkgs=(python3 python3-pip python3-pipx git) ;;",
            "alpine) pkgs=(python3 py3-pip pipx git) ;;",
        ],
    )
    def test_family_package_list_includes_git(self, family_line: str) -> None:
        assert family_line in self._TEXT

    def test_have_git_uses_functional_probe_not_command_dash_v(self) -> None:
        assert "have_git() { git --version >/dev/null 2>&1; }" in self._TEXT

    def test_already_complete_gate_requires_git(self) -> None:
        assert "have_venv && have_pip && have_pipx && have_git" in self._TEXT

    def test_post_install_verification_dies_on_missing_git(self) -> None:
        assert 'have_git || die "git still missing or not runnable' in self._TEXT


def test_bash_syntax_check() -> None:
    proc = subprocess.run(
        ["bash", "-n", str(_PREREQS)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, f"{_PREREQS}: {proc.stderr}"
