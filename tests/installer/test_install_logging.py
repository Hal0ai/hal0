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

    def test_an_unwritable_primary_path_falls_back_to_tmp(self) -> None:
        """The FHS path (root-owned /var/log/hal0) can be unwritable even as
        root — a read-only /var, an unusual mount policy. Fall back to /tmp
        rather than aborting the install over forensics."""
        script = f"""
source "{LOGGING_SH}"
id() {{ echo 0; }}
hal0_install_log_path() {{ printf '/nonexistent-root-only-dir/install-test.log\\n'; }}
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
