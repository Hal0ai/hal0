"""Bootstrap batched dep preflight — #2062.

``installer/bootstrap.sh``'s ``preflight()`` used to die on the FIRST
missing dependency (``need curl`` → die, re-run, ``need jq`` → die, …),
costing a minimal host one full retry cycle per missing tool, and never
offered to install anything even though the host's package manager was
sitting right there.

The fix collects ALL missing deps in one pass, then either auto-installs
the whole batch via the detected package manager (the install-or-fail
pattern of install.sh's ``preflight_venv`` / ``preflight_container_runtime``)
or fails ONCE listing every dep plus a single copy-pasteable install
command. Boxes with all deps present must see no behavior change.

Technique mirrors ``test_bootstrap_contract.py``: run the real script
under a hermetic PATH whose contents decide which deps "exist". ``uname``
is faked to report Linux so these tests are host-OS independent.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP = _REPO_ROOT / "installer" / "bootstrap.sh"

# Everything bootstrap preflight checks beyond curl/jq (the two the issue
# reproduced with). Real binaries are symlinked so only curl/jq are "missing".
_PRESENT_TOOLS = ("bash", "tar", "sha256sum", "python3", "mktemp", "mkdir", "rm", "cat")


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _hermetic_bin(tmp_path: Path, *, tools: tuple[str, ...] = _PRESENT_TOOLS) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in tools:
        target = shutil.which(tool)
        assert target is not None, f"test host lacks {tool}"
        os.symlink(target, bin_dir / tool)
    # Fake Linux regardless of the host running the suite.
    _write_executable(bin_dir / "uname", "#!/usr/bin/env bash\necho Linux\n")
    return bin_dir


def _run_bootstrap(bin_dir: Path, extra_env: dict[str, str] | None = None):
    bash = shutil.which("bash")
    assert bash is not None
    env = {"PATH": str(bin_dir), "TMPDIR": str(bin_dir.parent), **(extra_env or {})}
    return subprocess.run(
        [bash, str(_BOOTSTRAP)], env=env, capture_output=True, text=True, check=False
    )


class TestBatchReporting:
    def test_missing_curl_and_jq_both_reported_in_one_run(self, tmp_path: Path) -> None:
        proc = _run_bootstrap(_hermetic_bin(tmp_path))
        assert proc.returncode != 0
        assert "missing dependency: curl" in proc.stderr
        assert "missing dependency: jq" in proc.stderr

    def test_no_package_manager_fails_with_guidance(self, tmp_path: Path) -> None:
        proc = _run_bootstrap(_hermetic_bin(tmp_path))
        assert proc.returncode != 0
        assert "no supported package manager" in proc.stderr


class TestInstallCommandHint:
    def test_failed_install_lists_single_copy_pasteable_command(self, tmp_path: Path) -> None:
        bin_dir = _hermetic_bin(tmp_path)
        apt_log = tmp_path / "apt.log"
        # apt-get that records its argv and fails — deps stay missing.
        _write_executable(
            bin_dir / "apt-get",
            f'#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "{apt_log}"\nexit 1\n',
        )
        proc = _run_bootstrap(bin_dir)
        assert proc.returncode != 0
        assert "missing dependency: curl" in proc.stderr
        assert "missing dependency: jq" in proc.stderr
        # ONE command covering everything, sudo-prefixed for copy-paste.
        assert "sudo apt-get" in proc.stderr
        assert "install -y curl jq" in proc.stderr

    def test_batch_is_one_package_manager_transaction(self, tmp_path: Path) -> None:
        bin_dir = _hermetic_bin(tmp_path)
        apt_log = tmp_path / "apt.log"
        _write_executable(
            bin_dir / "apt-get",
            f'#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "{apt_log}"\nexit 1\n',
        )
        _run_bootstrap(bin_dir)
        calls = apt_log.read_text(encoding="utf-8").splitlines()
        installs = [c for c in calls if " install " in f" {c} "]
        assert len(installs) == 1, calls
        assert "curl" in installs[0] and "jq" in installs[0]


class TestAutoInstall:
    def test_auto_install_resolves_missing_deps_and_proceeds(self, tmp_path: Path) -> None:
        bin_dir = _hermetic_bin(tmp_path)
        # apt-get that "installs" curl and jq by dropping stubs onto PATH.
        _write_executable(
            bin_dir / "apt-get",
            "#!/usr/bin/env bash\n"
            'case " $* " in\n'
            "*\" install \"*)\n"
            f'    for t in curl jq; do\n'
            f'        printf \'#!/usr/bin/env bash\\nexit 0\\n\' > "{bin_dir}/$t"\n'
            f'        /bin/chmod +x "{bin_dir}/$t"\n'
            "    done ;;\n"
            "esac\n"
            "exit 0\n",
        )
        proc = _run_bootstrap(bin_dir)
        out = proc.stdout + proc.stderr
        # Preflight succeeded: no missing-dep failure. The run dies later
        # (manifest/cosign fetch against stub curl), which is fine — the
        # contract under test is the dep gate, not the trust chain.
        assert "missing dependency" not in out
        assert "installed missing dependencies: curl jq" in out


class TestNoBehaviorChangeWhenDepsPresent:
    def test_no_install_attempt_when_all_deps_present(self, tmp_path: Path) -> None:
        bin_dir = _hermetic_bin(tmp_path)
        # All deps present (stub curl/jq), plus a tripwire package manager
        # that must never be called.
        for t in ("curl", "jq"):
            _write_executable(bin_dir / t, "#!/usr/bin/env bash\nexit 0\n")
        apt_log = tmp_path / "apt.log"
        _write_executable(
            bin_dir / "apt-get",
            f'#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "{apt_log}"\nexit 0\n',
        )
        proc = _run_bootstrap(bin_dir)
        out = proc.stdout + proc.stderr
        assert "missing dependency" not in out
        assert not apt_log.exists(), apt_log.read_text(encoding="utf-8")


def test_bash_syntax_check() -> None:
    proc = subprocess.run(
        ["bash", "-n", str(_BOOTSTRAP)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
