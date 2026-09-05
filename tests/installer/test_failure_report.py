"""installer/lib/failure-report.sh — redacted install-failure reports.

Same technique as test_seam_verification.py: source the file and invoke a
function directly, no root/sudo/provisioned box needed.

The bash redaction key-name pattern mirrors hal0.api._redact._SENSITIVE_RE
(SECRET|TOKEN|PASSWORD|PASS|API_KEY|PRIVATE_KEY|ENCRYPTION_KEY|SALT|_KEY$|
^KEY$) — test_redaction_matches_the_python_pattern pins both against the
same fixture set so a drift is caught in CI.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FAILURE_REPORT = REPO / "installer" / "lib" / "failure-report.sh"


def _is_sensitive(key: str) -> bool:
    proc = subprocess.run(
        ["bash", "-c", f'source "{FAILURE_REPORT}"; _hal0_report_key_is_sensitive "{key}"'],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO),
    )
    return proc.returncode == 0


class TestKeySensitivity:
    @pytest.mark.parametrize(
        "key",
        [
            "HF_TOKEN",
            "HUGGING_FACE_HUB_TOKEN",
            "HAL0_ADMIN_KEY",
            "HAL0_CLIENT_KEY",
            "DB_PASSWORD",
            "API_SECRET",
            "ENCRYPTION_KEY",
            "SALT",
            "PRIVATE_KEY",
        ],
    )
    def test_known_secret_shaped_keys_are_flagged(self, key: str) -> None:
        assert _is_sensitive(key) is True

    @pytest.mark.parametrize(
        "key",
        ["HAL0_PORT", "HAL0_PREFIX", "MODELS_DIR", "PATH", "KEY_ROTATION_DAYS", "KEYBOARD_LAYOUT"],
    )
    def test_ordinary_keys_are_not_flagged(self, key: str) -> None:
        assert _is_sensitive(key) is False

    def test_redaction_matches_the_python_pattern(self) -> None:
        """Fixture-parity check against hal0.api._redact.is_sensitive_key —
        catches the two regexes drifting apart."""
        from hal0.api._redact import is_sensitive_key

        fixtures = [
            "HF_TOKEN",
            "HAL0_ADMIN_KEY",
            "HAL0_CLIENT_KEY",
            "DB_PASSWORD",
            "API_SECRET",
            "ENCRYPTION_KEY",
            "SALT",
            "PRIVATE_KEY",
            "HAL0_PORT",
            "MODELS_DIR",
            "PATH",
            "KEY_ROTATION_DAYS",
            "KEYBOARD_LAYOUT",
        ]
        for key in fixtures:
            assert _is_sensitive(key) == is_sensitive_key(key), key


class TestRedactEnvStream:
    def test_sensitive_values_are_masked_key_preserved(self) -> None:
        script = f"""
source "{FAILURE_REPORT}"
printf 'HAL0_PORT=8080\\nHF_TOKEN=hf_abcdef123456\\nMODELS_DIR=/data\\n' | _hal0_report_redact_env_stream
"""
        proc = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=False, cwd=str(REPO)
        )
        lines = proc.stdout.splitlines()
        assert "HAL0_PORT=8080" in lines
        assert "MODELS_DIR=/data" in lines
        assert "HF_TOKEN=***REDACTED***" in lines
        assert "hf_abcdef123456" not in proc.stdout


class TestWriteFailureReport:
    def test_report_is_written_with_expected_sections_and_no_secret_leak(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_log = tmp_path / "install-test.log"
        fake_log.write_text("some install log content\n")
        script = f"""
source "{FAILURE_REPORT}"
export HAL0_INSTALL_LOG="{fake_log}"
export HF_TOKEN="hf_super_secret_value"
export HAL0_PORT="8080"
report="$(hal0_write_failure_report "Python environment")"
echo "report=$report"
cat "$report"
"""
        proc = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=False, cwd=str(REPO)
        )
        report_line = next(line for line in proc.stdout.splitlines() if line.startswith("report="))
        report_path = Path(report_line.removeprefix("report="))
        assert report_path.is_file(), proc.stdout

        body = report_path.read_text()
        assert "Phase: Python environment" in body
        assert "Environment (redacted)" in body
        assert "Port owners" in body
        assert "hal0 systemd units" in body
        assert "Hardware probe" in body
        assert "Install log tail" in body
        assert "some install log content" in body
        assert "hf_super_secret_value" not in body
        assert "HF_TOKEN=***REDACTED***" in body

    def test_falls_back_to_tmp_when_no_install_log_dir_is_known(self, tmp_path: Path) -> None:
        script = f"""
source "{FAILURE_REPORT}"
id() {{ echo 1000; }}
unset HAL0_INSTALL_LOG
report="$(hal0_write_failure_report "unknown")"
echo "report=$report"
"""
        proc = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=False, cwd=str(REPO)
        )
        report_line = next(line for line in proc.stdout.splitlines() if line.startswith("report="))
        report_path = Path(report_line.removeprefix("report="))
        assert report_path.is_file()
        assert str(report_path).startswith("/tmp/hal0-install-report-")
        report_path.unlink(missing_ok=True)
