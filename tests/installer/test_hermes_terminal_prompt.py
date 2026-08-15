"""installer/install.sh's Hermes terminal-tool prompt is opt-in and silent-safe.

The prompt hands the agent a root-equivalent shell when answered yes, so the
paths that must NEVER reach it — piped/headless/CI installs, ``--dev``,
``--no-start``, ``HAL0_SKIP_HERMES=1`` and upgrades of a box that already has a
hermes venv — are worth a real shell-level test rather than a static grep.

The gating block is inline in install.sh (not a function), so these tests slice
it out by its comment banner and run it under real bash together with
install.sh's own ``_interactive`` definition — the technique
``tests/installer/test_preflight_python_floor.py`` uses to exercise install.sh
fragments. ``_tty_read`` is replaced by a stub that records having been called
and answers the default: the assertion is "the operator was never asked", not
"the answer happened to be no".
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"

_BLOCK_START = "# ── Hermes terminal tool: explicit opt-in, DEFAULTS OFF"
_BLOCK_END = 'if [[ "${MODELS_DIR}" != /* ]]; then'

_STUBS = """
info() { printf 'INFO: %s\\n' "$*"; }
warn() { printf 'WARN: %s\\n' "$*"; }
err()  { printf 'ERR: %s\\n' "$*" >&2; }
"""


def _extract(text: str, start: str, end: str) -> str:
    head = text.index(start)
    return text[head : text.index(end, head)]


@pytest.fixture(scope="module")
def install_sh_text() -> str:
    return _INSTALL_SH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def gating_block(install_sh_text: str) -> str:
    return _extract(install_sh_text, _BLOCK_START, _BLOCK_END)


@pytest.fixture(scope="module")
def tty_helpers(install_sh_text: str) -> str:
    """install.sh's own ``_interactive``, verbatim — the gate under test."""
    m = re.search(r"^_interactive\(\) \{.*?^\}\n", install_sh_text, re.S | re.M)
    assert m, "install.sh no longer defines _interactive as expected"
    return m.group(0)


def _run_block(
    tmp_path: Path,
    gating_block: str,
    tty_helpers: str,
    env: dict[str, str],
    *,
    dev_mode: int = 0,
    no_start: int = 0,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the gating block; return (proc, sentinel path written iff prompted)."""
    prompted = tmp_path / "prompted"
    script = tmp_path / "block.sh"
    script.write_text(
        "set -euo pipefail\n"
        + _STUBS
        + tty_helpers
        # Any prompt at all is the failure; answer the default so the block
        # still runs to completion and its export decision can be inspected.
        + f'_tty_read() {{ local -n _o="$1"; : >"{prompted}"; _o="${{3:-}}"; }}\n'
        + f"DEV_MODE={dev_mode}\nNO_START={no_start}\n"
        + gating_block
        + '\nprintf "TERMINAL=%s\\n" "${HAL0_HERMES_TERMINAL-<unset>}"\n',
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["/usr/bin/env", "bash", str(script)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", **env},
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    return proc, prompted


def test_non_interactive_install_never_prompts_and_leaves_the_var_unset(
    tmp_path: Path, gating_block: str, tty_helpers: str
) -> None:
    # A piped `curl … | bash`, a CI run, or an explicit HAL0_NONINTERACTIVE=1
    # must sail past the prompt and export nothing: an unset variable is what
    # makes the provisioner take its default-OFF path.
    proc, prompted = _run_block(tmp_path, gating_block, tty_helpers, {"HAL0_NONINTERACTIVE": "1"})

    assert proc.returncode == 0, proc.stderr
    assert not prompted.exists(), "a headless install asked the operator a question"
    assert "TERMINAL=<unset>" in proc.stdout, proc.stdout


@pytest.mark.parametrize(
    ("label", "kwargs", "env"),
    [
        ("dev mode", {"dev_mode": 1}, {}),
        ("--no-start", {"no_start": 1}, {}),
        ("hermes skipped", {}, {"HAL0_SKIP_HERMES": "1"}),
    ],
)
def test_runs_that_never_provision_hermes_do_not_ask(
    tmp_path: Path,
    gating_block: str,
    tty_helpers: str,
    label: str,
    kwargs: dict[str, int],
    env: dict[str, str],
) -> None:
    # The answer lives only in this shell's environment, so asking for consent
    # this run would then drop on the floor is worse than not asking.
    proc, prompted = _run_block(
        tmp_path, gating_block, tty_helpers, {"HAL0_NONINTERACTIVE": "1", **env}, **kwargs
    )

    assert proc.returncode == 0, proc.stderr
    assert not prompted.exists(), f"{label} reached the terminal-tool prompt"
    assert "TERMINAL=<unset>" in proc.stdout, proc.stdout


@pytest.mark.parametrize("answer", ["0", "1"])
def test_an_environment_answer_is_taken_and_re_exported(
    tmp_path: Path, gating_block: str, tty_helpers: str, answer: str
) -> None:
    # HAL0_HERMES_TERMINAL is the unattended answer. It must survive the block
    # unchanged and be exported, so `hal0 agent install hermes` and the
    # privilege-dropped provisioning under it see the same value.
    proc, prompted = _run_block(
        tmp_path,
        gating_block,
        tty_helpers,
        {"HAL0_NONINTERACTIVE": "1", "HAL0_HERMES_TERMINAL": answer},
    )

    assert proc.returncode == 0, proc.stderr
    assert not prompted.exists(), "an answered install still prompted"
    assert f"TERMINAL={answer}" in proc.stdout, proc.stdout
    assert "taken from the environment" in proc.stdout


def test_the_prompt_defaults_to_no(gating_block: str) -> None:
    # A distracted Enter must not enable a root-equivalent shell.
    assert '"N"' in gating_block, gating_block
    assert re.search(r"\^\[Yy\]", gating_block), "the yes-match is not an explicit y/yes test"
