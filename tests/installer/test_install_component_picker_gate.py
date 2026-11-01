"""installer/install.sh's interactive component checklist call site.

The block is inline in install.sh, so it is sliced out by its banner and run
under real bash with ``ui_component_picker`` stubbed (the real widget lives in
installer/lib/ui.sh on a sibling branch). The stub records that it was called
and the rows it was given, and answers from ``PICKER_ANSWER`` (``id=on|off|
unavailable`` pairs; unlisted rows keep their default).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

_INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
_START = "# ── Components: interactive checklist ──"
_END = "# ── Hermes terminal tool: explicit opt-in, DEFAULTS OFF"

_STUBS = r"""
info() { :; }
warn() { printf 'WARN: %s\n' "$*"; }
_interactive() { [[ "${TTY:-0}" == "1" ]]; }
ui_component_picker() {
    local -n _rows="$1" _res="$2"
    : >"$PICKER_CALLED"
    local row id def
    for row in "${_rows[@]}"; do
        printf 'ROW\t%s\n' "$row" >>"$PICKER_CALLED"
        IFS=$'\t' read -r id _ _ def _ <<<"$row"
        _res["$id"]="$def"
    done
    local kv
    for kv in ${PICKER_ANSWER:-}; do _res["${kv%%=*}"]="${kv#*=}"; done
}
"""


@dataclass
class Result:
    picker_called: bool
    env: dict[str, str]
    rows: dict[str, str] = field(default_factory=dict)  # id -> reason
    states: dict[str, str] = field(default_factory=dict)
    proc: subprocess.CompletedProcess[str] | None = None


@pytest.fixture(scope="module")
def block() -> str:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    head = text.index(_START)
    return text[head : text.index(_END, head)]


@pytest.fixture
def run_block(tmp_path: Path, block: str):
    def run(
        env: dict[str, str] | None = None,
        *,
        tty: bool = False,
        answer: str = "",
        npm: bool = True,
        podman: bool = True,
        plan: str = "img\tdesc\t1",
    ) -> Result:
        bindir = tmp_path / "bin"
        venv_bin = tmp_path / "venv" / "bin"
        bindir.mkdir(exist_ok=True)
        venv_bin.mkdir(parents=True, exist_ok=True)
        if npm:
            (bindir / "npm").write_text("#!/bin/sh\nexit 0\n")
        if podman:
            (bindir / "podman").write_text("#!/bin/sh\nexit 0\n")
        for f in bindir.iterdir():
            f.chmod(0o755)
        py = venv_bin / "python"
        py.write_text(f"#!/bin/sh\nprintf '%b' '{plan}'\n")
        py.chmod(0o755)
        called = tmp_path / "called"
        called.unlink(missing_ok=True)
        script = tmp_path / "block.sh"
        script.write_text(
            "set -euo pipefail\n"
            + _STUBS
            + f'VENV_DIR="{tmp_path / "venv"}"\n'
            + 'DEV_MODE="${DEV_MODE:-0}"; NO_START="${NO_START:-0}"\n'
            + 'IS_UPGRADE="${IS_UPGRADE:-0}"; COMPONENT_FLAG_GIVEN="${COMPONENT_FLAG_GIVEN:-0}"\n'
            + 'HINDSIGHT_UNAVAILABLE_REASON="${HINDSIGHT_UNAVAILABLE_REASON:-}"\n'
            + block
            + '\nenv | grep ^HAL0_ | sed "s/^/ENV /" || true\n'
            + 'for k in "${!COMPONENT_STATES[@]}"; do printf "STATE\\t%s\\t%s\\n" "$k" "${COMPONENT_STATES[$k]}"; done\n',
            encoding="utf-8",
        )
        proc = subprocess.run(
            ["/usr/bin/env", "bash", str(script)],
            capture_output=True,
            text=True,
            env={
                "PATH": f"{bindir}:/usr/bin:/bin",
                "PICKER_CALLED": str(called),
                "PICKER_ANSWER": answer,
                "TTY": "1" if tty else "0",
                **(env or {}),
            },
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr + proc.stdout
        res = Result(picker_called=called.exists(), env={}, proc=proc)
        for line in proc.stdout.splitlines():
            if m := re.match(r"ENV (HAL0_\w+)=(.*)", line):
                res.env[m[1]] = m[2]
            elif line.startswith("STATE\t"):
                _, k, v = line.split("\t", 2)
                res.states[k] = v
        if res.picker_called:
            for line in called.read_text().splitlines():
                if line.startswith("ROW\t"):
                    parts = line.split("\t")
                    res.rows[parts[1]] = parts[5] if len(parts) > 5 else ""
        return res

    return run


def test_non_interactive_never_calls_picker(run_block) -> None:
    r = run_block(env={"HAL0_NONINTERACTIVE": "1"})
    assert r.picker_called is False and r.env.get("HAL0_SKIP_PI") is None


def test_any_component_flag_suppresses_picker(run_block) -> None:
    assert run_block(env={"COMPONENT_FLAG_GIVEN": "1"}, tty=True).picker_called is False


@pytest.mark.parametrize("env", [{"DEV_MODE": "1"}, {"NO_START": "1"}, {"IS_UPGRADE": "1"}])
def test_dev_nostart_upgrade_skip_picker(run_block, env) -> None:
    assert run_block(env=env, tty=True).picker_called is False


def test_picker_off_rows_export_knobs(run_block) -> None:
    r = run_block(
        tty=True,
        answer="openwebui=off hermes=off pi=off hindsight=on brain=off comfyui=off",
    )
    assert r.env["HAL0_SKIP_OPENWEBUI"] == "1" and r.env["HAL0_SKIP_HERMES"] == "1"
    assert r.env["HAL0_IMAGE_MODELS"] == "none" and r.env["HAL0_SKIP_BRAIN_MODEL"] == "1"
    assert "HAL0_SKIP_HINDSIGHT" not in r.env


def test_pi_on_exports_install_pi(run_block) -> None:
    r = run_block(tty=True, answer="pi=on")
    assert r.env["HAL0_INSTALL_PI"] == "1" and "HAL0_SKIP_PI" not in r.env


def test_unavailable_rows_are_passed_with_reason(run_block) -> None:
    r = run_block(
        tty=True,
        env={"HINDSIGHT_UNAVAILABLE_REASON": "no usable Python 3.11-3.14"},
        npm=False,
        podman=False,
        plan="none\\tno GPU",
    )
    assert r.rows["hindsight"] == "no usable Python 3.11-3.14"
    assert r.rows["pi"] == "needs npm (Node.js)"
    assert r.rows["openwebui"] == "needs podman"
    assert r.rows["comfyui"] == "no GPU"
    assert r.rows["hermes"] == "" and r.rows["brain"] == ""


def test_empty_image_plan_is_unavailable(run_block) -> None:
    r = run_block(tty=True, plan="")
    assert r.rows["comfyui"] == "image models cannot be planned on this box"


def test_component_states_recorded(run_block) -> None:
    r = run_block(tty=True, answer="hermes=off")
    assert r.states["hermes"] == "skipped" and r.states["openwebui"] == "selected"
    assert r.states["pi"] == "skipped"


def test_unavailable_answer_recorded_with_reason(run_block) -> None:
    r = run_block(tty=True, answer="openwebui=unavailable", podman=False)
    assert r.states["openwebui"] == "unavailable:needs podman"
    assert r.env["HAL0_SKIP_OPENWEBUI"] == "1"


def test_headless_states_come_from_environment(run_block) -> None:
    r = run_block(
        env={"HAL0_SKIP_BRAIN_MODEL": "1", "HAL0_INSTALL_PI": "1"},
    )
    assert r.picker_called is False
    assert r.states["brain"] == "skipped" and r.states["pi"] == "selected"
    assert r.states["comfyui"] == "selected"
    r2 = run_block(env={"HAL0_INSTALL_PI": "1", "HAL0_SKIP_PI": "1"})
    assert r2.states["pi"] == "skipped"
