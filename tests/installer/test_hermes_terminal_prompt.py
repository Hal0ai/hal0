"""installer/install.sh never switches the Hermes terminal tool on by itself.

The tool hands the agent a root-equivalent shell, so the only way it can be
enabled is an explicit ``HAL0_HERMES_TERMINAL`` value arriving from the
environment or from the setup screens' recorded answer. The block cut out here
only re-exports such a value and logs it; with nothing set the variable stays
unset, which is what makes the provisioner take its default-OFF path.

The block is inline in install.sh (not a function), so these tests slice it out
by its comment banner and run it under real bash.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALL_SH = _REPO_ROOT / "installer" / "install.sh"

_BLOCK_START = "# ── Hermes terminal tool: explicit opt-in, DEFAULTS OFF"
_BLOCK_END = "# ── hal0 brain model (v1.0)"

_STUBS = """
info() { printf 'INFO: %s\\n' "$*"; }
warn() { printf 'WARN: %s\\n' "$*"; }
err()  { printf 'ERR: %s\\n' "$*" >&2; }
"""


def _extract(text: str, start: str, end: str) -> str:
    head = text.index(start)
    return text[head : text.index(end, head)]


@pytest.fixture(scope="module")
def gating_block() -> str:
    return _extract(_INSTALL_SH.read_text(encoding="utf-8"), _BLOCK_START, _BLOCK_END)


def _code(block: str) -> str:
    """The block without its comment lines."""
    return "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))


def _run_block(
    tmp_path: Path,
    gating_block: str,
    env: dict[str, str],
    *,
    dev_mode: int = 0,
    no_start: int = 0,
    preset: str = "",
) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "block.sh"
    script.write_text(
        "set -euo pipefail\n"
        + _STUBS
        + f"DEV_MODE={dev_mode}\nNO_START={no_start}\n"
        + preset
        + gating_block
        + '\nprintf "TERMINAL=%s\\n" "${HAL0_HERMES_TERMINAL-<unset>}"\n'
        + "printf 'CHILD=%s\\n' \"$(bash -c 'printf %s \"${HAL0_HERMES_TERMINAL-<unset>}\"')\"\n",
        encoding="utf-8",
    )
    return subprocess.run(
        ["/usr/bin/env", "bash", str(script)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", **env},
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


def test_the_block_asks_nothing(gating_block: str) -> None:
    code = _code(gating_block)
    assert "_tty_read" not in code
    assert "_interactive" not in code
    assert "/dev/tty" not in code
    assert "read " not in code


def test_nothing_in_the_block_switches_the_tool_on(gating_block: str) -> None:
    # The only assignment is the re-export of the value that arrived.
    code = _code(gating_block)
    assert not re.search(r"HAL0_HERMES_TERMINAL=", code.replace("(HAL0_HERMES_TERMINAL=", "")), code
    assert code.count("export HAL0_HERMES_TERMINAL") == 1


def test_nothing_set_leaves_the_var_unset(tmp_path: Path, gating_block: str) -> None:
    # An unset variable is what makes the provisioner take its default-OFF path.
    proc = _run_block(tmp_path, gating_block, {})

    assert proc.returncode == 0, proc.stderr
    assert "TERMINAL=<unset>" in proc.stdout, proc.stdout
    assert "terminal tool" not in proc.stdout


@pytest.mark.parametrize(
    ("label", "kwargs", "env"),
    [
        ("dev mode", {"dev_mode": 1}, {}),
        ("--no-start", {"no_start": 1}, {}),
        ("hermes skipped", {}, {"HAL0_SKIP_HERMES": "1"}),
    ],
)
def test_runs_that_never_provision_hermes_leave_the_var_unset(
    tmp_path: Path,
    gating_block: str,
    label: str,
    kwargs: dict[str, int],
    env: dict[str, str],
) -> None:
    proc = _run_block(tmp_path, gating_block, env, **kwargs)

    assert proc.returncode == 0, f"{label}: {proc.stderr}"
    assert "TERMINAL=<unset>" in proc.stdout, proc.stdout


@pytest.mark.parametrize("answer", ["0", "1"])
def test_an_environment_answer_is_taken_and_re_exported(
    tmp_path: Path, gating_block: str, answer: str
) -> None:
    # HAL0_HERMES_TERMINAL is the answer. It must survive the block unchanged
    # and be exported, so `hal0 agent install hermes` and the privilege-dropped
    # provisioning under it see the same value. The value arrives here as a
    # plain shell variable (not exported), so only the block's own `export`
    # can make a child process see it.
    proc = _run_block(tmp_path, gating_block, {}, preset=f"HAL0_HERMES_TERMINAL={answer}\n")

    assert proc.returncode == 0, proc.stderr
    assert f"TERMINAL={answer}" in proc.stdout, proc.stdout
    assert f"CHILD={answer}" in proc.stdout, proc.stdout
    assert "taken from the environment" in proc.stdout


@pytest.mark.parametrize(
    ("preset", "said"),
    [
        # the reader exported the answer in a run of the setup screens
        (
            "HAL0_WIZARD_RAN=1\nHAL0_ANSWERS_EXPORTED=(HAL0_PORT HAL0_HERMES_TERMINAL)\n",
            "set by the setup screens",
        ),
        # the screens ran but the answer is the environment's own
        ("HAL0_WIZARD_RAN=1\nHAL0_ANSWERS_EXPORTED=(HAL0_PORT)\n", "taken from the environment"),
        ("HAL0_WIZARD_RAN=1\nHAL0_ANSWERS_EXPORTED=()\n", "taken from the environment"),
        # nothing was applied in this run
        ("", "taken from the environment"),
        (
            "HAL0_WIZARD_RAN=0\nHAL0_ANSWERS_EXPORTED=(HAL0_HERMES_TERMINAL)\n",
            "taken from the environment",
        ),
    ],
)
def test_the_log_line_names_where_the_answer_came_from(
    tmp_path: Path, gating_block: str, preset: str, said: str
) -> None:
    proc = _run_block(tmp_path, gating_block, {}, preset=f"HAL0_HERMES_TERMINAL=1\n{preset}")

    assert proc.returncode == 0, proc.stderr
    assert f"INFO: Hermes terminal tool: {said} (HAL0_HERMES_TERMINAL=1)" in proc.stdout, (
        proc.stdout
    )


def test_another_value_is_passed_through_not_interpreted(tmp_path: Path, gating_block: str) -> None:
    # The block neither enables nor disables on its own reading of the value;
    # the provisioner owns what a value means.
    proc = _run_block(tmp_path, gating_block, {"HAL0_HERMES_TERMINAL": "maybe"})

    assert proc.returncode == 0, proc.stderr
    assert "TERMINAL=maybe" in proc.stdout, proc.stdout
