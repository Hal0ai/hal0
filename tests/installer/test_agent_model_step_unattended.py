"""An unattended install downloads no starter (agent) model unless asked.

The agent-model step of installer/install.sh is cut out by its text markers and
run under real bash with a stand-in for the venv's Python: it answers `--plan`
and records any other call (the download) in a file. With no terminal and
HAL0_PULL_AGENT_MODEL unset the step must say it is not pulling and start no
download.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEXT = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")

PLAN_LINE = "starter-9b\tQwen starter 9B — 6 GB download"

FAKE_PYTHON = """#!/bin/bash
case "$*" in
    *"--plan"*) printf '%s\\n' "$FAKE_PLAN" ;;
    *"--floor"*) echo 15 ;;
    *) echo "download $*" >>"$FAKE_LOG" ;;
esac
"""


def _step() -> str:
    head = TEXT.index('_agent_plan=""')
    return TEXT[head : TEXT.index("# ── starter image models", head)]


def _run(tmp_path: Path, env: dict[str, str]) -> tuple[str, str, str]:
    """Run the step; return (stdout, stderr, what the stand-in Python was asked to download)."""
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True, exist_ok=True)
    python = venv / "bin" / "python"
    python.write_text(FAKE_PYTHON, encoding="utf-8")
    python.chmod(0o700)
    log = tmp_path / "download.log"
    script = f"""
set -euo pipefail
DEV_MODE=0
VENV_DIR="{venv}"
HF_TOKEN_VAL=""
info() {{ printf 'INFO: %s\\n' "$*"; }}
warn() {{ printf 'WARN: %s\\n' "$*" >&2; }}
{_step()}
"""
    with subprocess.Popen(
        ["bash", "-c", script],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=tmp_path,
        env={
            "PATH": os.environ["PATH"],
            "FAKE_PLAN": PLAN_LINE,
            "FAKE_LOG": str(log),
            **env,
        },
    ) as proc:
        try:
            out, err = proc.communicate(timeout=60)
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.communicate(timeout=5)
            raise
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    assert proc.returncode == 0, err
    return out, err, log.read_text(encoding="utf-8") if log.exists() else ""


def test_unattended_with_the_variable_unset_pulls_nothing(tmp_path: Path) -> None:
    out, err, downloads = _run(tmp_path, {})
    assert (
        "not pulling the Qwen starter 9B agent model (set HAL0_PULL_AGENT_MODEL=1 to opt in)" in out
    )
    assert downloads == "", downloads
    assert "WARN" not in err


def test_the_opt_in_is_what_starts_the_download(tmp_path: Path) -> None:
    # control: the same step with the variable set does ask for the model, so the
    # test above would notice a branch that pulls
    out, _err, downloads = _run(tmp_path, {"HAL0_PULL_AGENT_MODEL": "1"})
    assert "not pulling" not in out
    assert downloads.startswith("download "), downloads


def test_forcing_the_skip_pulls_nothing(tmp_path: Path) -> None:
    out, _err, downloads = _run(tmp_path, {"HAL0_PULL_AGENT_MODEL": "0"})
    assert "Skipping the agent model" in out and downloads == ""
