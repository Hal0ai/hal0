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


# ── #2307: second redaction pass, bounded diagnostics, fail-closed ──────────
#
# Every probe the report runs (systemctl, ss, podman, journalctl) is shadowed
# by a stub on PATH so the tests never depend on — or hang on — the host's
# real binaries. Secret values below are realistic SHAPES, not real keys.

_HF = "hf_QyT3vR8mWk2LpZx9NcBd7FgHs4JaUe6Yio"
_CLIENT = "h0c_9fK2mQ7xW4rT8vB3nL6pZ1sD5gH0jY2u"
_UPSTREAM = "Zq8vR2kLm4Nx7Pw1Ty5Bc3Hd9Js6Fg0Ae2Uo"  # no known prefix: literal pass only
_TOML_SECRET = "tomlS3cr3tV4lu3Kq9Wz2Xc7Rv5Bn"
_BEARER = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJoYWwwIn0.c2lnbmF0dXJlMTIzNDU2"
_URL_PW = "p4ssW0rdInUrl99"
_FLAG_TOKEN = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"

_ALL_SECRETS = (_HF, _CLIENT, _UPSTREAM, _TOML_SECRET, _BEARER, _URL_PW, _FLAG_TOKEN)


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)


def _make_box(tmp_path: Path) -> dict[str, Path]:
    """A fake /etc/hal0 + install log + stub probes carrying secrets in every
    shape the second pass has to catch."""
    etc = tmp_path / "etc-hal0"
    etc.mkdir()
    (etc / "api.env").write_text(
        f"HAL0_PORT=8080\nHF_TOKEN={_HF}\nHAL0_CLIENT_KEY='{_CLIENT}'\n"
        f'export UPSTREAM_SECRET="{_UPSTREAM}"\n'
    )
    (etc / "hal0.toml").write_text(
        f'[models]\nstore = "/var/lib/hal0/models"\n\n[upstream.openai]\napi_key = "{_TOML_SECRET}"\n'
    )
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    log = log_dir / "install-20261008-000000.log"
    log.write_text(
        "==> Step 3/16: Python environment\n"
        f"pip: fetching https://example.invalid/simple?q={_UPSTREAM}\n"
        f"curl -H 'Authorization: Bearer {_BEARER}' https://example.invalid/\n"
        f"git clone https://operator:{_URL_PW}@git.example.invalid/hal0.git\n"
        f"hf download --token {_FLAG_TOKEN} some/model\n"
        f"export HAL0_CLIENT_KEY={_CLIENT}\n"
        f"echo api_key={_TOML_SECRET}\n"
        "benign line HAL0_PORT=8080 kept\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "systemctl", 'echo "systemctl $*"; echo "hal0-api.service failed"')
    _stub(
        bin_dir, "ss", 'echo "LISTEN 0 4096 0.0.0.0:8080 0.0.0.0:* users:((\\"python\\",pid=42))"'
    )
    _stub(bin_dir, "podman", 'echo "podman $* ok"')
    _stub(
        bin_dir,
        "journalctl",
        f'echo "journal: hal0-api started with HF_TOKEN {_HF}"; echo "journalctl $*"',
    )
    return {"etc": etc, "log": log, "bin": bin_dir}


def _run_report(
    box: dict[str, Path], *, extra: str = "", env: dict[str, str] | None = None, timeout: int = 60
) -> tuple[subprocess.CompletedProcess[str], Path | None]:
    import os

    script = f"""
set -euo pipefail
source "{FAILURE_REPORT}"
export HAL0_INSTALL_LOG="{box["log"]}"
ETC_DIR="{box["etc"]}"
export HF_TOKEN="{_HF}"
{extra}
report="$(hal0_write_failure_report "Python environment")"
echo "report=$report"
"""
    run_env = dict(os.environ)
    run_env["PATH"] = f"{box['bin']}:{run_env['PATH']}"
    run_env.pop("HF_TOKEN", None)
    if env:
        run_env.update(env)
    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO),
        env=run_env,
        timeout=timeout,
    )
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("report=")), None)
    path = Path(line.removeprefix("report=")) if line and line != "report=" else None
    return proc, path


class TestSecondRedactionPass:
    def test_no_secret_literal_survives_anywhere_in_the_report(self, tmp_path: Path) -> None:
        box = _make_box(tmp_path)
        proc, report = _run_report(box)
        assert report is not None and report.is_file(), proc.stderr
        body = report.read_text()
        for secret in _ALL_SECRETS:
            assert secret not in body, secret
        # The redaction leaves evidence a secret WAS there.
        assert "***REDACTED***" in body
        assert "Bearer ***REDACTED***" in body
        assert "operator:***REDACTED***@git.example.invalid" in body
        # Non-secret context survives so the report is still useful.
        assert "benign line HAL0_PORT=8080 kept" in body
        assert "Step 3/16: Python environment" in body

    def test_new_diagnostic_sections_are_present(self, tmp_path: Path) -> None:
        box = _make_box(tmp_path)
        _proc, report = _run_report(box)
        assert report is not None
        body = report.read_text()
        for heading in (
            "systemctl --failed",
            "podman info",
            "podman images",
            "api.env (redacted)",
            "hal0.toml (redacted)",
            "journalctl -u hal0-api -n 100",
        ):
            assert heading in body, heading
        assert "podman info ok" in body
        assert "journalctl -u hal0-api -n 100 --no-pager" in body
        # Key-name pass on the structured config sections.
        assert "HF_TOKEN=***REDACTED***" in body
        assert "HAL0_PORT=8080" in body
        assert 'store = "/var/lib/hal0/models"' in body

    def test_log_tail_is_the_last_160_lines(self, tmp_path: Path) -> None:
        box = _make_box(tmp_path)
        box["log"].write_text("".join(f"logline-{i:04d}\n" for i in range(1, 301)))
        _proc, report = _run_report(box)
        assert report is not None
        body = report.read_text()
        assert "logline-0141" in body
        assert "logline-0300" in body
        assert "logline-0140" not in body

    def test_no_temp_files_left_and_report_is_owner_only(self, tmp_path: Path) -> None:
        box = _make_box(tmp_path)
        _proc, report = _run_report(box)
        assert report is not None
        leftovers = sorted(
            p.name for p in report.parent.iterdir() if p != report and p != box["log"]
        )
        assert leftovers == []
        assert report.stat().st_mode & 0o777 == 0o600


class TestBoundedDiagnostics:
    def test_a_hung_podman_is_timed_out_and_recorded(self, tmp_path: Path) -> None:
        import time

        box = _make_box(tmp_path)
        # Ignores SIGTERM too, like a wedged podman stuck on its lock.
        _stub(box["bin"], "podman", "trap '' TERM; sleep 120")
        t0 = time.monotonic()
        proc, report = _run_report(box, env={"HAL0_REPORT_PROBE_TIMEOUT": "1"}, timeout=60)
        elapsed = time.monotonic() - t0
        assert report is not None and report.is_file(), proc.stderr
        assert elapsed < 30, elapsed
        body = report.read_text()
        assert "timed out after 1s: podman info" in body
        assert "timed out after 1s: podman images" in body

    def test_probes_are_skipped_not_run_unbounded_without_timeout(self, tmp_path: Path) -> None:
        box = _make_box(tmp_path)
        _proc, report = _run_report(box, extra="_hal0_report_have_timeout() { return 1; }")
        assert report is not None
        body = report.read_text()
        assert "podman info ok" not in body
        assert "timeout(1) unavailable" in body


class TestFailClosed:
    @pytest.mark.parametrize("tool", ["awk", "sed"])
    def test_a_failing_redactor_discards_the_body(self, tmp_path: Path, tool: str) -> None:
        box = _make_box(tmp_path)
        _stub(box["bin"], tool, "exit 2")
        proc, report = _run_report(box)
        assert report is not None and report.is_file(), proc.stderr
        body = report.read_text()
        for secret in _ALL_SECRETS:
            assert secret not in body, secret
        assert "redaction could not run" in body
        assert "Step 3/16" not in body  # the log tail was NOT copied
        assert "Phase: Python environment" in body
        leftovers = sorted(
            p.name for p in report.parent.iterdir() if p != report and p != box["log"]
        )
        assert leftovers == []

    def test_a_literal_surviving_the_pass_discards_the_body(self, tmp_path: Path) -> None:
        box = _make_box(tmp_path)
        # A literal pass that silently does nothing must not ship the secret.
        proc, report = _run_report(box, extra="_hal0_report_mask_literals() { cat; }")
        assert report is not None and report.is_file(), proc.stderr
        body = report.read_text()
        for secret in _ALL_SECRETS:
            assert secret not in body, secret
        assert "redaction could not run" in body
