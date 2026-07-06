"""Assert install.sh gathers + persists HF_TOKEN to a secrets/ EnvironmentFile.

WS-D (#1106): the HuggingFace token is gathered at install time (pre-filled
from HF_TOKEN / HUGGING_FACE_HUB_TOKEN in the installer's own env — install.sh
is non-interactive, see its own header, so there is no TTY prompt) and
persisted to a root:root 0600 ``secrets/`` EnvironmentFile — deliberately NOT
``api.env``, which stays 0644/world-readable. ``hal0-api.service`` loads the
secrets file as an *optional* EnvironmentFile so a fresh box with no token
still starts cleanly.

These tests parse ``installer/install.sh`` as text (the same approach
``tests/systemd/test_unit_files.py::TestInstallerGatewayWiring`` uses for the
hermes-gateway wiring) rather than actually running the installer — running
it end-to-end needs root, systemd, a venv build, etc., which is exercised
instead by the black-box harness at ``tests/harness/installer-test.sh``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"


@pytest.fixture(scope="module")
def install_sh_text() -> str:
    assert _INSTALL_SH.exists(), f"missing {_INSTALL_SH}"
    return _INSTALL_SH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def gather_block(install_sh_text: str) -> str:
    """The HF_TOKEN gather+persist block, isolated from the rest of the file.

    Scoping assertions to this block (rather than the whole file) keeps the
    tests precise about *which* code path they're checking — e.g. "no die
    call in the whoami validation" would be meaningless if it just grepped
    the entire 1000+ line script.
    """
    m = re.search(
        r"# ── HF_TOKEN gather \+ persist \(WS-D, #1106\).*?\nUPSTREAMS_TOML=",
        install_sh_text,
        re.DOTALL,
    )
    assert m is not None, "HF_TOKEN gather+persist block not found in install.sh"
    return m.group(0)


class TestGather:
    """Pre-fill from env; install.sh itself never prompts (non-interactive)."""

    def test_reads_hf_token_env_var(self, gather_block: str) -> None:
        assert "HF_TOKEN_VAL=" in gather_block
        assert re.search(r'HF_TOKEN_VAL="\$\{HF_TOKEN:-', gather_block)

    def test_falls_back_to_hugging_face_hub_token(self, gather_block: str) -> None:
        # Same precedence as hal0.api.routes.installer / cli.setup_install
        # (#1094): HF_TOKEN first, HUGGING_FACE_HUB_TOKEN second.
        assert "HUGGING_FACE_HUB_TOKEN" in gather_block

    def test_missing_token_is_a_clean_skip(self, gather_block: str) -> None:
        # The else branch must not die()/exit — just log and move on.
        m = re.search(r"else\n(.*?)\nfi", gather_block, re.DOTALL)
        assert m is not None, "no else (skip) branch found"
        skip_branch = m.group(1)
        assert "die" not in skip_branch
        assert "exit" not in skip_branch
        assert "no HF_TOKEN" in skip_branch


class TestWhoamiValidation:
    """Optional `hf auth whoami` validation warns, never hard-fails."""

    def test_whoami_invoked_via_venv_hf_cli(self, gather_block: str) -> None:
        assert "${VENV_DIR}/bin/hf" in gather_block
        assert "auth whoami" in gather_block

    def test_failure_path_warns_not_dies(self, gather_block: str) -> None:
        m = re.search(
            r"if HF_TOKEN=.*?auth whoami.*?\n(.*?)\nfi",
            gather_block,
            re.DOTALL,
        )
        assert m is not None, "whoami if/else block not found"
        whoami_block = m.group(1)
        assert "warn " in whoami_block
        assert "die" not in whoami_block
        assert "exit" not in whoami_block

    def test_whoami_call_is_guarded_not_bare(self, gather_block: str) -> None:
        # A bare (non-conditional) invocation would trip `set -e` on a bad
        # token and abort the whole installer — it must live inside an
        # `if`/`[[ -x ]]` guard.
        assert re.search(r"if \[\[ -x \"\$\{HF_CLI\}\" \]\]", gather_block)
        assert re.search(r"if HF_TOKEN=\S+ \"\$\{HF_CLI\}\" auth whoami", gather_block)


class TestPersistence:
    """Root:root 0600 secrets/ EnvironmentFile — never api.env."""

    def test_secrets_dir_under_var_lib_secrets(self, gather_block: str) -> None:
        assert 'SECRETS_DIR="${VAR_DIR}/secrets"' in gather_block

    def test_secrets_file_is_not_api_env(self, gather_block: str) -> None:
        assert 'HF_SECRETS_ENV="${SECRETS_DIR}/hal0-api.env"' in gather_block
        # Sanity: the two paths must differ.
        assert "HF_SECRETS_ENV" != "API_ENV"

    def test_written_mode_0600(self, gather_block: str) -> None:
        assert "chmod 0600" in gather_block

    def test_owned_root_root(self, gather_block: str) -> None:
        assert "chown root:root" in gather_block

    def test_written_atomically(self, gather_block: str) -> None:
        # mktemp + mv, not an in-place redirect — avoids a torn read by a
        # concurrent `systemctl daemon-reload`/restart.
        assert re.search(r"mktemp \"\$\{HF_SECRETS_ENV\}", gather_block)
        assert "mv -f" in gather_block

    def test_token_value_written_to_secrets_file_only(self, gather_block: str) -> None:
        # HF_TOKEN=${HF_TOKEN_VAL} must appear inside the persistence
        # heredoc (targeting HF_SECRETS_TMP), not assigned anywhere that
        # would land it in api.env.
        assert "HF_TOKEN=${HF_TOKEN_VAL}" in gather_block


class TestSystemdWiring:
    """hal0-api.service must load the secrets file as an EnvironmentFile."""

    def test_api_env_still_wired(self, install_sh_text: str) -> None:
        assert re.search(r"^EnvironmentFile=\$\{API_ENV\}$", install_sh_text, re.MULTILINE)

    def test_hf_secrets_env_wired_optionally(self, install_sh_text: str) -> None:
        # Leading `-` makes a missing file non-fatal (mirrors
        # hal0-agent@.service's `EnvironmentFile=-/etc/hal0/agents/%i.env`
        # and the hermes-gateway secrets drop-in) — a fresh box with no
        # HF_TOKEN gathered at install time must still start hal0-api.
        assert re.search(r"^EnvironmentFile=-\$\{HF_SECRETS_ENV\}$", install_sh_text, re.MULTILINE)

    def test_hf_secrets_env_wiring_precedes_execstart(self, install_sh_text: str) -> None:
        api_unit = re.search(
            r"API_UNIT=.*?\ncat > \"\$\{API_UNIT\}\" <<EOF\n(.*?)\nEOF",
            install_sh_text,
            re.DOTALL,
        )
        assert api_unit is not None, "hal0-api.service heredoc not found"
        body = api_unit.group(1)
        assert "EnvironmentFile=-${HF_SECRETS_ENV}" in body
        env_idx = body.index("EnvironmentFile=-${HF_SECRETS_ENV}")
        exec_idx = body.index("ExecStart=")
        assert env_idx < exec_idx, "EnvironmentFile must be declared before ExecStart"


class TestApiEnvNotClobbered:
    """api.env keeps its (commented, non-secret) HF_TOKEN placeholder only."""

    def test_api_env_heredoc_has_no_live_hf_token_assignment(self, install_sh_text: str) -> None:
        api_env_heredoc = re.search(
            r'API_ENV="\$\{ETC_DIR\}/api\.env".*?cat > "\$\{API_ENV\}" <<EOF\n(.*?)\nEOF',
            install_sh_text,
            re.DOTALL,
        )
        assert api_env_heredoc is not None, "api.env heredoc not found"
        body = api_env_heredoc.group(1)
        # Only a commented-out placeholder — never a live assignment.
        assert "# HF_TOKEN=" in body
        assert not re.search(r"^HF_TOKEN=", body, re.MULTILINE)


class TestShellcheckClean:
    """No NEW shellcheck findings from this change (baseline stays flat)."""

    def test_no_new_shellcheck_errors(self, install_sh_text: str) -> None:
        if shutil.which("shellcheck") is None:
            pytest.skip("shellcheck not installed")
        proc = subprocess.run(
            ["shellcheck", "--severity=error", str(_INSTALL_SH)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"shellcheck reported errors:\n{proc.stdout}\n{proc.stderr}"


def test_bash_syntax_check() -> None:
    """`bash -n` must pass — the DoD's cheapest, fastest correctness gate."""
    proc = subprocess.run(
        ["bash", "-n", str(_INSTALL_SH)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
