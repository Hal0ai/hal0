"""The "Pre-flight checks" step is split: detect, then one answers seam, then act.

install.sh's first step used to interleave detecting facts, asking questions
and changing the host. It now runs in two halves around ``hal0_collect_answers``:
the first half only reads the machine and writes the facts file; everything
that installs, repairs or depends on a choice comes after the seam. These tests
pin the order by text and run the slices that carry behaviour under real bash,
the technique ``test_install_component_flags.py`` uses.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
TEXT = INSTALL_SH.read_text(encoding="utf-8")

_STEP_START = '\nui_step "Pre-flight checks"'
_STEP_END = '\nui_step "System user"'
_SEAM = "\nhal0_collect_answers\n"
_FILE_BEGIN = "# file-origin: begin"
_FILE_END = "# file-origin: end\n"


def _step() -> str:
    head = TEXT.index(_STEP_START)
    return TEXT[head : TEXT.index(_STEP_END, head)]


def _code(text: str) -> str:
    """Drop whole-line comments so prose that names a function is not counted."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _first_half() -> str:
    step = _step()
    return step[: step.index(_SEAM)]


def _second_half() -> str:
    step = _step()
    return step[step.index(_SEAM) + len(_SEAM) :]


def _without_file_branch(text: str) -> str:
    i = text.index(_FILE_BEGIN)
    j = text.index(_FILE_END, i) + len(_FILE_END)
    return text[:i] + text[j:]


def _run(script: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    import os

    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": os.environ["PATH"], **(env or {})},
        check=False,
    )


def test_first_half_calls_no_mutating_function() -> None:
    first = _code(_without_file_branch(_first_half()))
    for name in (
        "persist_bootstrap_cosign",
        "resolve_main_python",
        "preflight_venv",
        "preflight_container_runtime",
        "resolve_hindsight_python",
        "chgrp",
        "_confirm_cpu_only",
    ):
        assert name not in first, f"{name} must not run before the answers seam"
    # Detection helpers are present, so the check above is looking at the right text.
    assert "preflight_python" in first
    assert "preflight_gpu" in first


def test_file_origin_branch_is_the_only_early_install() -> None:
    first = _first_half()
    assert first.count(_FILE_BEGIN) == 1
    branch = first[first.index(_FILE_BEGIN) : first.index(_FILE_END)]
    assert "HAL0_ANSWERS_FILE" in branch
    assert "Installing Python, which is needed to read your answers file." in branch
    assert "_hal0_resolve_main_python_or_die" in _code(branch)


def test_facts_written_before_seam() -> None:
    first = _first_half()
    assert first.index("hal0_facts_gather") < first.index("hal0_facts_write")
    assert 'hal0_facts_write "${HAL0_FACTS_FILE:-/run/hal0-install/facts.json}"' in first
    assert "could not write the facts file" in first
    # --dev has no `current` link: both seams come from the installer's own variables.
    assert 'HAL0_FACTS_VAR_DIR="${VAR_DIR}"' in first
    assert 'HAL0_FACTS_FHS_ROOT="${PREFIX}"' in first


def test_gpu_detection_runs_once() -> None:
    step = _step()
    calls = [ln for ln in _code(step).splitlines() if "preflight_gpu" in ln]
    assert len(calls) == 1, calls
    assert "HAL0_GPU_GATE=1 preflight_gpu" in calls[0]
    assert _first_half().index("preflight_gpu") < _first_half().index("hal0_facts_gather")


def test_gpu_branches_follow_the_seam_and_use_captured_code() -> None:
    first, second = _first_half(), _second_half()
    assert "gpu_rc=0" in first
    assert "gpu_rc=$?" in first
    assert "gpu_rc=0" not in second and "gpu_rc=$?" not in second
    for code in ("BROKEN_GID", "NO_KFD", "KFD_GID", "NO_DEVICE"):
        assert f"gpu_rc == HAL0_GPU_RC_{code}" in second
        assert f"gpu_rc == HAL0_GPU_RC_{code}" not in first
    assert "chgrp" in second
    assert "_confirm_cpu_only" in second


def test_second_half_order() -> None:
    second = _second_half()
    order = [
        "persist_bootstrap_cosign",
        "_hal0_resolve_main_python_or_die",
        "preflight_venv",
        "preflight_container_runtime",
        "gpu_rc == HAL0_GPU_RC_BROKEN_GID",
        "if [[ \"${HAL0_ANSWERS_APPLIED:-0}\" != \"1\" ]] && _interactive; then",
        "resolve_hindsight_python",
        "preflight_writable",
        "preflight_ports",
        "IS_UPGRADE=0",
    ]
    code = _code(second)
    positions = [code.index(item) for item in order]
    assert positions == sorted(positions), list(zip(order, positions, strict=True))


def test_step_total_unchanged() -> None:
    assert re.search(r"^UI_STEP_TOTAL=15$", TEXT, re.M)
    assert len(re.findall(r"^ui_step ", TEXT, re.M)) == 15


# ── the legacy prompts ──────────────────────────────────────────────────────

_PROMPTS_START = "# ── Operator input: model store + HuggingFace token"
_PROMPTS_END = "# The Hindsight memory engine"


def _prompts_script(tmp_path: Path, applied: str | None) -> str:
    head = TEXT.index(_PROMPTS_START)
    block = TEXT[head : TEXT.index(_PROMPTS_END, head)]
    log = tmp_path / "asked.log"
    preset = f"HAL0_ANSWERS_APPLIED={applied}\n" if applied is not None else ""
    return f"""
set -euo pipefail
info() {{ printf 'INFO: %s\\n' "$*"; }}
warn() {{ printf 'WARN: %s\\n' "$*"; }}
die() {{ printf 'DIE: %s\\n' "$*" >&2; exit 1; }}
_interactive() {{ return 0; }}
_tty_read() {{
    local -n _o="$1"
    printf 'ASKED: %s\\n' "$2" >> "{log}"
    _o="${{3:-}}"
}}
MODELS_DIR=/srv/models
MODELS_DIR_EXPLICIT=0
HF_TOKEN=tok
{preset}{block}
echo "MODELS_DIR=${{MODELS_DIR}}"
"""


def test_legacy_prompts_skipped_when_answers_applied(tmp_path: Path) -> None:
    proc = _run(_prompts_script(tmp_path, "1"))
    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "asked.log").exists(), "a prompt ran although answers were applied"
    assert "MODELS_DIR=/srv/models" in proc.stdout


@pytest.mark.parametrize("applied", [None, "0"])
def test_no_answers_keeps_todays_prompts(tmp_path: Path, applied: str | None) -> None:
    proc = _run(_prompts_script(tmp_path, applied))
    assert proc.returncode == 0, proc.stderr
    asked = (tmp_path / "asked.log").read_text()
    assert "ASKED: Where should downloaded models live?" in asked


# ── file origin without a usable Python ─────────────────────────────────────


def _python_first_script(tmp_path: Path, *, usable: bool, answers_file: str) -> str:
    fn_head = TEXT.index("_hal0_resolve_main_python_or_die() {")
    fn = TEXT[fn_head : TEXT.index("\n}\n", fn_head) + 3]
    head = TEXT.index("# Detection only: is the default Python usable?")
    half = TEXT[head : TEXT.index(_SEAM, head)]
    log = tmp_path / "calls.log"
    pre = "true" if usable else "false"
    return f"""
set -uo pipefail
LOG="{log}"
info() {{ echo "info: $*" >> "$LOG"; }}
warn() {{ echo "warn: $*" >> "$LOG"; }}
die() {{ echo "die: $*" >> "$LOG"; exit 1; }}
python_venv_hint() {{ echo hint; }}
preflight_python() {{ {pre}; }}
preflight_gpu() {{ echo gpu >> "$LOG"; return 0; }}
hal0_facts_gather() {{ echo gather >> "$LOG"; }}
hal0_facts_write() {{ echo write >> "$LOG"; }}
resolve_main_python() {{ echo "resolve autoinstall=${{HAL0_PY_AUTOINSTALL:-}}" >> "$LOG"; echo python3.12; }}
hal0_collect_answers() {{ echo seam >> "$LOG"; }}
DEV_MODE=0
PY=python3
VAR_DIR=/var/lib/hal0
HAL0_FHS_ROOT=/usr/lib/hal0
PREFIX=/usr/lib/hal0/hal0-0
{"HAL0_ANSWERS_FILE=" + answers_file if answers_file else "unset HAL0_ANSWERS_FILE"}
{fn}
{half}
{_SEAM}
echo "PY=${{PY}}" >> "$LOG"
"""


def _calls(tmp_path: Path) -> list[str]:
    return (tmp_path / "calls.log").read_text().splitlines()


def test_file_origin_without_python_installs_python_first(tmp_path: Path) -> None:
    proc = _run(_python_first_script(tmp_path, usable=False, answers_file="/x/answers.json"))
    assert proc.returncode == 0, proc.stderr
    calls = _calls(tmp_path)
    msg = "info: Installing Python, which is needed to read your answers file."
    assert msg in calls
    assert calls.index(msg) < calls.index("resolve autoinstall=1") < calls.index("seam")
    assert "PY=python3.12" in calls


def test_no_file_without_python_defers_install_past_seam(tmp_path: Path) -> None:
    proc = _run(_python_first_script(tmp_path, usable=False, answers_file=""))
    assert proc.returncode == 0, proc.stderr
    calls = _calls(tmp_path)
    assert not any(c.startswith("resolve") for c in calls)
    assert not any("answers file" in c for c in calls)
    assert calls[-2:] == ["seam", "PY=python3"]


def test_file_with_usable_python_installs_nothing_early(tmp_path: Path) -> None:
    proc = _run(_python_first_script(tmp_path, usable=True, answers_file="/x/answers.json"))
    assert proc.returncode == 0, proc.stderr
    assert not any(c.startswith(("resolve", "info: Installing")) for c in _calls(tmp_path))
