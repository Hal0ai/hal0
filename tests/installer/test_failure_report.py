"""installer/lib/failure-report.sh — redacted install-failure reports.

Same technique as test_seam_verification.py: source the file and invoke a
function directly, no root/sudo/provisioned box needed.

The bash redaction key-name pattern mirrors hal0.api._redact._SENSITIVE_RE
(SECRET|TOKEN|PASSWORD|PASS|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|
ENCRYPTION[_-]?KEY|SALT|_KEY$|^KEY$) — test_redaction_matches_the_python_pattern
pins both against the same fixture set so a drift is caught in CI.
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
            # #2384: run-together and camelCase names, and their lookalikes.
            "apikey",
            "apiKey",
            "api-key",
            "accessKey",
            "access_key",
            "accessToken",
            "privateKey",
            "encryptionKey",
            "keyboard",
            "monkey",
            "hotkey",
            "max_tokens",
            "tokenizer",
            "token_count",
            "api_key_env",
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


_REAL_TOKEN = "Zq8vR2mW9xK4tL7pQ3"


class TestHarvestOnlyPlausibleSecrets:
    """The report-text harvest masks a NAME=value's value everywhere it later
    appears, so a value that is not a plausible secret must not be harvested:
    it would erase unrelated numbers, words and variable names (#2307)."""

    def _report(self, tmp_path: Path, log_lines: str) -> str:
        box = _make_box(tmp_path)
        box["log"].write_text(log_lines)
        proc, report = _run_report(box, extra='export OPENAI_API_KEY="sk-not-shown-here"')
        assert report is not None and report.is_file(), proc.stderr
        return report.read_text()

    def test_a_numeric_value_is_not_masked_elsewhere(self, tmp_path: Path) -> None:
        body = self._report(
            tmp_path,
            f"llama: max_tokens=4096 UPSTREAM_TOKEN={_REAL_TOKEN}\n"
            "memory 4096 MiB; port 14096\n"
            f"retry https://example.invalid/hook?t={_REAL_TOKEN}\n",
        )
        assert "memory 4096 MiB; port 14096" in body
        # The real token next to it is still harvested and masked everywhere.
        assert _REAL_TOKEN not in body
        assert "hook?t=***REDACTED***" in body

    def test_a_variable_name_value_is_not_masked_elsewhere(self, tmp_path: Path) -> None:
        body = self._report(
            tmp_path,
            "provider.credential_written key=OPENAI_API_KEY upstream=openai\n"
            "migrate.model_caps.divergent key=vision model=qwen\n"
            "token_env=HF_TOKEN_FILE\n"
            f"auth: api_token={_REAL_TOKEN}\n"
            "set OPENAI_API_KEY before enabling vision support\n"
            "HF_TOKEN_FILE not readable\n"
            f"echo {_REAL_TOKEN}\n",
        )
        assert "set OPENAI_API_KEY before enabling vision support" in body
        # The env section still redacts by name, not the name itself.
        assert "OPENAI_API_KEY=***REDACTED***" in body
        assert "HF_TOKEN_FILE not readable" in body
        assert _REAL_TOKEN not in body
        assert "echo ***REDACTED***" in body


# ── #2385: TOML multi-line strings under a sensitive key ────────────────────


def _bash(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail\nsource "{FAILURE_REPORT}"\n{script}'],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO),
    )


def _toml_redact_and_harvest(tmp_path: Path, toml: str) -> tuple[str, list[str]]:
    path = tmp_path / "hal0.toml"
    path.write_text(toml)
    redacted = _bash(f'_hal0_report_redact_toml_stream <"{path}"')
    harvested = _bash(f'_hal0_report_harvest_toml_file "{path}"')
    assert redacted.returncode == 0, redacted.stderr
    assert harvested.returncode == 0, harvested.stderr
    return redacted.stdout, harvested.stdout.splitlines()


_ML_A = "mlBodySecretAlpha7Kq2Wz9"
_ML_B = "mlBodySecretBravo3Xc8Rv1"


class TestTomlMultilineStrings:
    @pytest.mark.parametrize("quote", ['"""', "'''"])
    def test_a_sensitive_multiline_body_is_masked_and_harvested(
        self, tmp_path: Path, quote: str
    ) -> None:
        out, harvested = _toml_redact_and_harvest(
            tmp_path,
            f'[upstream.x]\napi_key = {quote}\n{_ML_A}\n  {_ML_B}\n{quote}\nurl = "http://x"\n',
        )
        assert _ML_A not in out
        assert _ML_B not in out
        assert 'api_key = "***REDACTED***"' in out
        # The string's end is found: the next key is read as a key again.
        assert 'url = "http://x"' in out
        assert _ML_A in harvested
        assert _ML_B in harvested  # indentation stripped so it matches bare

    @pytest.mark.parametrize("quote", ['"""', "'''"])
    def test_a_closing_delimiter_on_a_content_line(self, tmp_path: Path, quote: str) -> None:
        out, harvested = _toml_redact_and_harvest(
            tmp_path,
            f'client_secret = {quote}{_ML_A}\n{_ML_B}{quote}\npassword_hint = "x"\nport = 8080\n',
        )
        assert _ML_A not in out
        assert _ML_B not in out
        assert "port = 8080" in out
        assert _ML_A in harvested
        assert _ML_B in harvested

    def test_a_one_line_triple_quoted_value_is_harvested(self, tmp_path: Path) -> None:
        out, harvested = _toml_redact_and_harvest(
            tmp_path, f'api_key = """{_ML_A}"""\nport = 8080\n'
        )
        assert _ML_A not in out
        assert "port = 8080" in out
        assert _ML_A in harvested

    def test_an_escaped_quote_run_does_not_close_a_basic_string(self, tmp_path: Path) -> None:
        out, harvested = _toml_redact_and_harvest(
            tmp_path, f'api_key = """\nfirst \\""" still body\n{_ML_A}\n"""\nport = 8080\n'
        )
        assert _ML_A not in out
        assert "still body" not in out
        assert "port = 8080" in out
        assert _ML_A in harvested

    def test_a_non_sensitive_multiline_value_is_left_intact(self, tmp_path: Path) -> None:
        toml = (
            'system_prompt = """\nYou are a helpful assistant.\nAnswer briefly.\n"""\n'
            f"notes = '''\nraw text\n'''\napi_key = \"{_ML_A}\"\n"
        )
        out, harvested = _toml_redact_and_harvest(tmp_path, toml)
        assert 'system_prompt = """\nYou are a helpful assistant.\nAnswer briefly.\n"""\n' in out
        assert "notes = '''\nraw text\n'''\n" in out
        # The state closed again: the key after the strings is still masked.
        assert _ML_A not in out
        assert harvested == [_ML_A]

    def test_the_report_masks_a_multiline_secret_everywhere(self, tmp_path: Path) -> None:
        box = _make_box(tmp_path)
        (box["etc"] / "hal0.toml").write_text(
            f'[models]\nstore = "/srv"\n\n[upstream.openai]\napi_key = """\n{_ML_A}\n"""\n'
        )
        box["log"].write_text(f"upstream replied for {_ML_A}\nbenign line kept\n")
        proc, report = _run_report(box)
        assert report is not None and report.is_file(), proc.stderr
        body = report.read_text()
        assert _ML_A not in body
        assert "benign line kept" in body
        assert "upstream replied for ***REDACTED***" in body
        assert 'store = "/srv"' in body


# ── #2384: run-together / camelCase secret names in free text ──────────────


def _mask_patterns(text: str) -> str:
    proc = _bash(f"_hal0_report_mask_patterns <<'__EOF__'\n{text}\n__EOF__")
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


class TestCamelCaseSecretNames:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            (
                "GET https://x.invalid/v1?apikey=abcd1234efgh&q=1",
                "GET https://x.invalid/v1?apikey=***REDACTED***&q=1",
            ),
            ('{"apiKey": "abcd1234efgh"}', '{"apiKey": "***REDACTED***"}'),
            ('{"accessKey": "abcd1234efgh"}', '{"accessKey": "***REDACTED***"}'),
            ('{"accessToken": "abcd1234efgh"}', '{"accessToken": "***REDACTED***"}'),
            ("privateKey=abcd1234efgh", "privateKey=***REDACTED***"),
            ("APIKEY=abcd1234efgh", "APIKEY=***REDACTED***"),
        ],
    )
    def test_the_pattern_pass_masks_run_together_names(self, line: str, expected: str) -> None:
        assert _mask_patterns(line).strip() == expected

    @pytest.mark.parametrize(
        "line",
        [
            "layout keyboard=us-intl-altgr",
            "zoo monkey=bananaphone99",
            "bind hotkey=ctrl-alt-del",
        ],
    )
    def test_the_pattern_pass_leaves_key_lookalikes_alone(self, line: str) -> None:
        assert _mask_patterns(line).strip() == line

    def test_a_camelcase_secret_is_masked_everywhere(self, tmp_path: Path) -> None:
        box = _make_box(tmp_path)
        box["log"].write_text(
            f"GET https://x.invalid/v1?apikey={_REAL_TOKEN}\nupstream echo {_REAL_TOKEN}\n"
        )
        proc, report = _run_report(box)
        assert report is not None and report.is_file(), proc.stderr
        body = report.read_text()
        assert _REAL_TOKEN not in body
        assert "upstream echo ***REDACTED***" in body

    def test_lookalike_values_are_not_masked_elsewhere(self, tmp_path: Path) -> None:
        """Values next to count/tokenizer/env-name fields, or under a KEY
        lookalike, are not harvested: masking them as substrings would
        erase unrelated report text."""
        box = _make_box(tmp_path)
        box["log"].write_text(
            "llama: max_tokens=40960000 token_count=123456789\n"
            "load tokenizer=Qwen/Qwen2.5-7B-Instruct\n"
            "provider.credential_written key=OPENAI_API_KEY\n"
            "api_key_env=HF_TOKEN_FILE\n"
            "layout keyboard=us-intl-altgr monkey=bananaphone99\n"
            "--- elsewhere ---\n"
            "budget 40960000 / 123456789\n"
            "model Qwen/Qwen2.5-7B-Instruct ready\n"
            "set OPENAI_API_KEY first; HF_TOKEN_FILE missing\n"
            "kbd us-intl-altgr; pet bananaphone99\n"
        )
        proc, report = _run_report(box)
        assert report is not None and report.is_file(), proc.stderr
        body = report.read_text()
        assert "budget 40960000 / 123456789" in body
        assert "model Qwen/Qwen2.5-7B-Instruct ready" in body
        assert "set OPENAI_API_KEY first; HF_TOKEN_FILE missing" in body
        assert "kbd us-intl-altgr; pet bananaphone99" in body
        assert "layout keyboard=us-intl-altgr monkey=bananaphone99" in body


# ── #2400: harvest NAME: value (JSON / YAML / header) as well as NAME=value ──


class TestColonFormsAreHarvested:
    @pytest.mark.parametrize(
        ("first", "secret"),
        [
            ('{"token": "JsonTok_88bbccdd"} then', "JsonTok_88bbccdd"),
            ("registry login password: Colon_Secret_77aa", "Colon_Secret_77aa"),
            ("curl -H 'x-api-key: HdrK3y_55eeff00'", "HdrK3y_55eeff00"),
        ],
    )
    def test_a_colon_secret_is_masked_where_it_reappears_bare(
        self, tmp_path: Path, first: str, secret: str
    ) -> None:
        box = _make_box(tmp_path)
        box["log"].write_text(f"{first}\nlater reused bare: {secret} end\n")
        proc, report = _run_report(box)
        assert report is not None and report.is_file(), proc.stderr
        body = report.read_text()
        assert secret not in body
        assert "later reused bare: ***REDACTED*** end" in body

    def test_colon_lookalikes_are_still_not_harvested(self, tmp_path: Path) -> None:
        box = _make_box(tmp_path)
        box["log"].write_text(
            '{"max_tokens": 40960000, "tokenizer": "Qwen/Qwen2.5-7B-Instruct"}\n'
            "key: OPENAI_API_KEY\n"
            "max_tokens: 4096\n"
            "keyboard: us-intl-altgr\n"
            "budget 40960000; model Qwen/Qwen2.5-7B-Instruct; set OPENAI_API_KEY\n"
            "port 14096; kbd us-intl-altgr\n"
        )
        proc, report = _run_report(box)
        assert report is not None and report.is_file(), proc.stderr
        body = report.read_text()
        assert "budget 40960000; model Qwen/Qwen2.5-7B-Instruct; set OPENAI_API_KEY" in body
        assert "port 14096; kbd us-intl-altgr" in body
        assert "keyboard: us-intl-altgr" in body


# ── #2410: a scheme-less Authorization header value ────────────────────────


class TestSchemelessAuthorization:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("Authorization: rawauth_Pl34Ok56Ij78", "Authorization: ***REDACTED***"),
            (
                "curl -H 'Authorization: rawauth_Pl34Ok56Ij78' https://x.invalid/",
                "curl -H 'Authorization: ***REDACTED***' https://x.invalid/",
            ),
            ('{"Authorization": "rawauth_Pl34Ok56Ij78"}', '{"Authorization": "***REDACTED***"}'),
            ("Authorization: Bearer abcdef123456", "Authorization: Bearer ***REDACTED***"),
            ("Authorization: Basic dXNlcjpwYXNz", "Authorization: Basic ***REDACTED***"),
            ("authorization: denied", "authorization: denied"),
            ("Authorization: ApiKey ak_live_Zx81Qw45", "Authorization: ApiKey ***REDACTED***"),
            ("Authorization: Bot MTk4NjIyNDgzNDcx", "Authorization: Bot ***REDACTED***"),
            ("Authorization: SSWS 00aBcD1234efGh", "Authorization: SSWS ***REDACTED***"),
            ("Authorization: Negotiate YIIC4wYGKwYB", "Authorization: Negotiate ***REDACTED***"),
            ("authorization: denied for bob", "authorization: denied for bob"),
            ("Authorization: abcdefghijklmnop qrstuvwxyz", "Authorization: ***REDACTED***"),
            ("authorization: required for user", "authorization: ***REDACTED***"),
        ],
    )
    def test_the_pattern_pass_masks_a_raw_header_value(self, line: str, expected: str) -> None:
        assert _mask_patterns(line).strip() == expected

    def test_the_report_does_not_carry_a_raw_header_value(self, tmp_path: Path) -> None:
        box = _make_box(tmp_path)
        box["log"].write_text("Authorization: rawauth_Pl34Ok56Ij78\n")
        proc, report = _run_report(box)
        assert report is not None and report.is_file(), proc.stderr
        assert "rawauth_Pl34Ok56Ij78" not in report.read_text()


# ── #2402: multi-line TOML arrays under a sensitive key ─────────────────────

_ARR_A = "arrSecretAlpha1Kq2Wz9Xc"  # no known prefix: literal pass only
_ARR_B = "arrSecretBravo]3Xc8Rv1"  # a `]` inside a string does not close the array


class TestTomlMultilineArrays:
    def test_every_element_line_is_masked_and_harvested(self, tmp_path: Path) -> None:
        out, harvested = _toml_redact_and_harvest(
            tmp_path,
            f"api_keys = [\n  \"{_ARR_A}\",  # first\n  '{_ARR_B}',\n]\nport = 8080\n",
        )
        assert _ARR_A not in out
        assert "arrSecretBravo" not in out
        assert 'api_keys = "***REDACTED***"' in out
        assert "port = 8080" in out  # the array's end is found
        assert _ARR_A in harvested
        assert _ARR_B in harvested

    def test_nested_and_one_line_arrays(self, tmp_path: Path) -> None:
        out, harvested = _toml_redact_and_harvest(
            tmp_path,
            f'tokens_by_host = [\n  ["h1", "{_ARR_A}"],\n  ["h2", "{_ML_A}"],\n]\n'
            f'api_tokens = ["{_ML_B}", "{_ARR_B}"]\nport = 8080\n',
        )
        for secret in (_ARR_A, _ML_A, _ML_B):
            assert secret not in out
            assert secret in harvested
        assert _ARR_B in harvested
        assert "port = 8080" in out

    def test_a_non_sensitive_array_is_left_intact(self, tmp_path: Path) -> None:
        toml = f'models = [\n  "qwen",\n  "llama",\n]\napi_key = "{_ARR_A}"\n'
        out, harvested = _toml_redact_and_harvest(tmp_path, toml)
        assert 'models = [\n  "qwen",\n  "llama",\n]\n' in out
        assert _ARR_A not in out
        assert harvested == [_ARR_A]

    def test_the_report_masks_an_array_element_everywhere(self, tmp_path: Path) -> None:
        box = _make_box(tmp_path)
        (box["etc"] / "hal0.toml").write_text(
            f'[upstream.openai]\napi_keys = [\n  "{_ARR_A}",\n]\nstore = "/srv"\n'
        )
        box["log"].write_text(f"retrying with {_ARR_A}\n")
        proc, report = _run_report(box)
        assert report is not None and report.is_file(), proc.stderr
        body = report.read_text()
        assert _ARR_A not in body
        assert "retrying with ***REDACTED***" in body
        assert 'store = "/srv"' in body


class TestReportOnExit:
    """#2438: die()/`exit 1` bypass the ERR trap; the EXIT trap covers them."""

    @staticmethod
    def _run(tmp_path: Path, body: str) -> tuple[subprocess.CompletedProcess[str], list[Path]]:
        log = tmp_path / "install.log"
        log.write_text("log\n")
        script = f"""
set -euo pipefail
source "{FAILURE_REPORT}"
export HAL0_INSTALL_LOG="{log}"
trap 'hal0_report_on_exit "$?"' EXIT
{body}
"""
        proc = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=False, cwd=str(REPO)
        )
        return proc, sorted(tmp_path.glob("hal0-install-report-*.txt"))

    def test_explicit_exit_1_writes_a_report(self, tmp_path: Path) -> None:
        proc, reports = self._run(tmp_path, "CURRENT_STEP='Pre-flight checks'; exit 1")
        assert proc.returncode == 1
        assert len(reports) == 1, proc.stderr
        assert "Phase: Pre-flight checks" in reports[0].read_text()
        assert "Failure report saved" in proc.stderr

    def test_success_writes_nothing(self, tmp_path: Path) -> None:
        proc, reports = self._run(tmp_path, "exit 0")
        assert proc.returncode == 0
        assert reports == []

    def test_already_written_report_is_not_duplicated(self, tmp_path: Path) -> None:
        proc, reports = self._run(tmp_path, "_HAL0_REPORT_WRITTEN=1; exit 1")
        assert proc.returncode == 1
        assert reports == []

    def test_install_sh_wires_exit_trap_and_err_trap_marks_written(self) -> None:
        src = (REPO / "installer" / "install.sh").read_text()
        assert "trap 'hal0_report_on_exit \"$?\"' EXIT" in src
        assert "_HAL0_REPORT_WRITTEN=1" in src
        # The EXIT trap must precede the first release-gate die().
        assert src.index("hal0_report_on_exit") < src.index(
            "Refusing to install from an unverified"
        )
