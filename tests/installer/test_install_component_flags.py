"""installer/install.sh component flags only export the env knobs they name.

The arg loop is sliced out by text markers and run under real bash, the
technique ``test_hermes_terminal_prompt.py`` uses. Each flag must export exactly
the knobs in the component table and set ``COMPONENT_FLAG_GIVEN=1``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"

_START = 'SUMMARY_JSON_PATH=""'
_END = "# Banner first"

_PREAMBLE = """
info() { printf 'INFO: %s\\n' "$*"; }
warn() { printf 'WARN: %s\\n' "$*"; }
err()  { printf 'ERR: %s\\n' "$*" >&2; }
MODELS_DIR=""
DEV_MODE=0
NO_START=0
"""
_TAIL = """
env | grep '^HAL0_' | sed 's/^/ENV:/'
echo COMPONENT_FLAG_GIVEN=$COMPONENT_FLAG_GIVEN
"""

ALL_SIX = {
    "HAL0_SKIP_OPENWEBUI": "1",
    "HAL0_IMAGE_MODELS": "none",
    "HAL0_SKIP_HERMES": "1",
    "HAL0_SKIP_PI": "1",
    "HAL0_SKIP_HINDSIGHT": "1",
    "HAL0_SKIP_BRAIN_MODEL": "1",
}


def _fragment() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    head = text.index(_START)
    return text[head : text.index(_END, head)]


@pytest.fixture
def run_argloop():
    script = _PREAMBLE + _fragment() + _TAIL

    def run(args, env=None):
        proc = subprocess.run(
            ["bash", "-c", script, "bash", *args],
            env={"PATH": os.environ["PATH"], **(env or {})},
            capture_output=True,
            text=True,
            check=True,
        )
        out = {}
        given = None
        for line in proc.stdout.splitlines():
            if line.startswith("ENV:"):
                k, _, v = line[4:].partition("=")
                out[k] = v
            elif line.startswith("COMPONENT_FLAG_GIVEN="):
                given = line.split("=", 1)[1]
        run.stdout = proc.stdout
        return out, given

    return run


@pytest.mark.parametrize(
    "flag,expected",
    [
        ("--no-owui", {"HAL0_SKIP_OPENWEBUI": "1"}),
        ("--no-comfyui", {"HAL0_IMAGE_MODELS": "none"}),
        ("--no-hermes", {"HAL0_SKIP_HERMES": "1"}),
        ("--no-pi", {"HAL0_SKIP_PI": "1"}),
        ("--no-memory", {"HAL0_SKIP_HINDSIGHT": "1"}),
        ("--no-brain-model", {"HAL0_SKIP_BRAIN_MODEL": "1"}),
        ("--no-agents", {"HAL0_SKIP_HERMES": "1", "HAL0_SKIP_PI": "1"}),
    ],
)
def test_flag_exports_only_its_knobs(run_argloop, flag, expected):
    env, given = run_argloop([flag])
    assert env == expected
    assert given == "1"


def test_bare_exports_every_knob(run_argloop):
    env, given = run_argloop(["--bare"])
    assert env == ALL_SIX and given == "1"


def test_bare_inference_is_a_synonym(run_argloop):
    env, given = run_argloop(["--bare-inference"])
    assert env == ALL_SIX and given == "1"


def test_flag_overrides_env(run_argloop):
    env, _ = run_argloop(["--no-comfyui"], env={"HAL0_IMAGE_MODELS": "recommended"})
    assert env["HAL0_IMAGE_MODELS"] == "none"


def test_no_flag_sets_nothing(run_argloop):
    env, given = run_argloop([])
    assert env == {} and given == "0"


def test_bare_with_no_start_still_exports(run_argloop):
    env, _ = run_argloop(["--bare", "--no-start"])
    assert env == ALL_SIX


def test_unknown_flag_still_warns(run_argloop):
    env, given = run_argloop(["--bogus"])
    assert "WARN: unknown flag: --bogus (ignored)" in run_argloop.stdout
    assert env == {} and given == "0"


def test_help_lists_every_component_flag():
    text = INSTALL_SH.read_text()
    help_text = text.split("Usage: install.sh")[1].split("EOF")[0]
    for f in [
        "--bare",
        "--bare-inference",
        "--no-owui",
        "--no-comfyui",
        "--no-hermes",
        "--no-pi",
        "--no-agents",
        "--no-memory",
        "--no-brain-model",
    ]:
        assert f in help_text
