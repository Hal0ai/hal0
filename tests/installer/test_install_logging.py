"""installer/lib/logging.sh — tee'd install log path selection.

Same technique as test_seam_verification.py: source the file and invoke a
function directly. The full `exec > >(tee ...)` redirect (hal0_install_log_init)
is exercised through a real subshell so the tee side effect is observable
without touching this test process's own stdout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOGGING_SH = REPO / "installer" / "lib" / "logging.sh"


def _run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False, cwd=str(REPO)
    )


def _unwritable_even_for_root(tmp_path: Path) -> Path:
    """A "directory" path whose mkdir fails even as root: a regular file sits
    where the directory should be. (A made-up absolute path like
    /nonexistent-dir is creatable by root, so a root test run never reached
    the fallback.)"""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    return blocker


class TestLogPath:
    def test_root_gets_a_var_log_hal0_path(self) -> None:
        script = f"""
source "{LOGGING_SH}"
id() {{ echo 0; }}  # pretend to be root
hal0_install_log_path
"""
        proc = _run(script)
        path = proc.stdout.strip()
        assert path.startswith("/var/log/hal0/install-"), proc.stdout
        assert path.endswith(".log")

    def test_non_root_gets_a_tmp_fallback_path(self) -> None:
        script = f"""
source "{LOGGING_SH}"
id() {{ echo 1000; }}  # pretend to be non-root
hal0_install_log_path
"""
        proc = _run(script)
        path = proc.stdout.strip()
        assert path.startswith("/tmp/hal0-install-"), proc.stdout


class TestLogInit:
    def test_init_creates_the_log_and_captures_subsequent_output(self, tmp_path: Path) -> None:
        fake_log_dir = tmp_path / "var-log-hal0"
        script = f"""
source "{LOGGING_SH}"
id() {{ echo 0; }}
hal0_install_log_path() {{ printf '%s/install-test.log\\n' "{fake_log_dir}"; }}
hal0_install_log_init
echo "path=$HAL0_INSTALL_LOG"
echo "hello from the installer"
warn_line() {{ echo "a warning" >&2; }}
warn_line
"""
        proc = _run(script)
        assert "rc=" not in proc.stdout  # no crash marker expected
        log_path_line = next(line for line in proc.stdout.splitlines() if line.startswith("path="))
        log_path = log_path_line.removeprefix("path=")
        assert Path(log_path).is_file()
        captured = Path(log_path).read_text()
        assert "hello from the installer" in captured
        assert "a warning" in captured
        # Still visible on the terminal too — tee, not redirect.
        assert "hello from the installer" in proc.stdout
        assert "a warning" in proc.stderr

    def test_init_is_idempotent(self, tmp_path: Path) -> None:
        fake_log_dir = tmp_path / "var-log-hal0"
        script = f"""
source "{LOGGING_SH}"
id() {{ echo 0; }}
hal0_install_log_path() {{ printf '%s/install-test.log\\n' "{fake_log_dir}"; }}
hal0_install_log_init
first="$HAL0_INSTALL_LOG"
hal0_install_log_init
second="$HAL0_INSTALL_LOG"
[[ "$first" == "$second" ]] && echo "same"
"""
        proc = _run(script)
        assert "same" in proc.stdout, proc.stdout

    def test_an_unwritable_primary_path_falls_back_to_tmp(self, tmp_path: Path) -> None:
        """The FHS path (root-owned /var/log/hal0) can be unwritable even as
        root — a read-only /var, an unusual mount policy. Fall back to /tmp
        rather than aborting the install over forensics."""
        blocker = _unwritable_even_for_root(tmp_path)
        script = f"""
source "{LOGGING_SH}"
id() {{ echo 0; }}
hal0_install_log_path() {{ printf '%s/install-test.log\\n' "{blocker}"; }}
hal0_install_log_init
echo "rc=$?"
echo "log=$HAL0_INSTALL_LOG"
echo "still running"
"""
        proc = _run(script)
        assert "still running" in proc.stdout, proc.stdout
        log_line = next(line for line in proc.stdout.splitlines() if line.startswith("log="))
        fallback_path = log_line.removeprefix("log=")
        assert fallback_path.startswith("/tmp/hal0-install-"), proc.stdout
        assert Path(fallback_path).is_file()
        Path(fallback_path).unlink(missing_ok=True)


class TestLogMode:
    """#2361: the log tees installer output unredacted, so it must be
    owner-only (0600) on both the primary and the /tmp fallback path, created
    that way (umask 077) rather than chmod'ed after a world-readable birth."""

    def test_primary_log_is_owner_only(self, tmp_path: Path) -> None:
        fake_log_dir = tmp_path / "var-log-hal0"
        script = f"""
umask 022
source "{LOGGING_SH}"
id() {{ echo 0; }}
hal0_install_log_path() {{ printf '%s/install-test.log\\n' "{fake_log_dir}"; }}
hal0_install_log_init
echo "log=$HAL0_INSTALL_LOG"
echo "umask=$(umask)"
"""
        proc = _run(script)
        log_line = next(line for line in proc.stdout.splitlines() if line.startswith("log="))
        log_path = Path(log_line.removeprefix("log="))
        assert log_path.is_file(), proc.stdout
        assert (log_path.stat().st_mode & 0o777) == 0o600
        # The tighter umask is scoped to the log's creation; the rest of the
        # install keeps the caller's umask (install.sh relies on 022).
        assert "umask=0022" in proc.stdout, proc.stdout

    def test_tmp_fallback_log_is_owner_only(self, tmp_path: Path) -> None:
        blocker = _unwritable_even_for_root(tmp_path)
        script = f"""
umask 022
source "{LOGGING_SH}"
id() {{ echo 0; }}
hal0_install_log_path() {{ printf '%s/install-test.log\\n' "{blocker}"; }}
hal0_install_log_init
echo "log=$HAL0_INSTALL_LOG"
"""
        proc = _run(script)
        log_line = next(line for line in proc.stdout.splitlines() if line.startswith("log="))
        fallback_path = Path(log_line.removeprefix("log="))
        try:
            assert str(fallback_path).startswith("/tmp/hal0-install-"), proc.stdout
            assert (fallback_path.stat().st_mode & 0o777) == 0o600
        finally:
            fallback_path.unlink(missing_ok=True)

    def test_a_preexisting_world_readable_log_is_tightened(self, tmp_path: Path) -> None:
        """umask only applies at creation; a same-second rerun appending to an
        existing 0644 log must still end up 0600."""
        fake_log_dir = tmp_path / "var-log-hal0"
        fake_log_dir.mkdir()
        existing = fake_log_dir / "install-test.log"
        existing.write_text("earlier run\n")
        existing.chmod(0o644)
        script = f"""
source "{LOGGING_SH}"
id() {{ echo 0; }}
hal0_install_log_path() {{ printf '%s\\n' "{existing}"; }}
hal0_install_log_init
"""
        _run(script)
        assert (existing.stat().st_mode & 0o777) == 0o600
