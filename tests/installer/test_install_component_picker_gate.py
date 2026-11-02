"""installer/install.sh's interactive component checklist call site.

The block is inline in install.sh, so it is sliced out by its banner and run
under real bash with ``ui_component_picker`` stubbed (the real widget lives in
installer/lib/ui.sh on a sibling branch). The stub records that it was called
and the rows it was given, and answers from ``PICKER_ANSWER`` (``id=on|off|
unavailable`` pairs; unlisted rows keep their default).
"""

from __future__ import annotations

import re
import shutil
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
    defaults: dict[str, str] = field(default_factory=dict)
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
        pi: bool = False,
        podman: bool = True,
        plan: str = "img\tdesc\t1",
    ) -> Result:
        bindir = tmp_path / "bin"
        venv_bin = tmp_path / "venv" / "bin"
        if bindir.exists():
            shutil.rmtree(bindir)
        bindir.mkdir()
        # Only the tools the fragment needs; the host's podman/npm never leak in.
        for tool in ("env", "grep", "sed"):
            (bindir / tool).symlink_to(shutil.which(tool) or f"/usr/bin/{tool}")
        venv_bin.mkdir(parents=True, exist_ok=True)
        if npm:
            (bindir / "npm").write_text("#!/bin/sh\nexit 0\n")
        if podman:
            (bindir / "podman").write_text("#!/bin/sh\nexit 0\n")
        if pi:
            (bindir / "pi").write_text("#!/bin/sh\nexit 0\n")
        for f in ("npm", "podman", "pi"):
            if (bindir / f).exists():
                (bindir / f).chmod(0o755)
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
            [shutil.which("bash") or "/bin/bash", str(script)],
            capture_output=True,
            text=True,
            env={
                "PATH": str(bindir),
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
                    res.defaults[parts[1]] = parts[4]
        return res

    return run


def test_non_interactive_never_calls_picker(run_block) -> None:
    r = run_block(env={"HAL0_NONINTERACTIVE": "1"})
    assert r.picker_called is False and r.env == {"HAL0_NONINTERACTIVE": "1"}
    r = run_block(env={"HAL0_NONINTERACTIVE": "1", "HAL0_INSTALL_PI": "1"})
    assert r.env == {"HAL0_NONINTERACTIVE": "1", "HAL0_INSTALL_PI": "1"}


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


@pytest.mark.parametrize(
    ("env", "row"),
    [
        ({"HAL0_SKIP_OPENWEBUI": "1"}, "openwebui"),
        ({"HAL0_IMAGE_MODELS": "none"}, "comfyui"),
        ({"HAL0_IMAGE_MODELS": "0"}, "comfyui"),
        ({"HAL0_SKIP_HERMES": "1"}, "hermes"),
        ({"HAL0_SKIP_HINDSIGHT": "1"}, "hindsight"),
        ({"HAL0_SKIP_BRAIN_MODEL": "1"}, "brain"),
        ({"HAL0_SKIP_PI": "1"}, "pi"),
    ],
)
def test_env_skip_knobs_make_row_default_off(run_block, env, row) -> None:
    r = run_block(tty=True, env=env)
    assert r.defaults[row] == "off"
    assert all(v == "on" for k, v in r.defaults.items() if k not in (row, "pi"))


def test_env_install_pi_defaults_pi_on_and_survives(run_block) -> None:
    r = run_block(tty=True, env={"HAL0_INSTALL_PI": "1"})
    assert r.defaults["pi"] == "on"
    assert r.states["pi"] == "selected" and "HAL0_SKIP_PI" not in r.env


def test_default_row_defaults(run_block) -> None:
    r = run_block(tty=True)
    assert r.defaults == {
        "openwebui": "on",
        "comfyui": "on",
        "hermes": "on",
        "pi": "off",
        "hindsight": "on",
        "brain": "on",
    }


_TEXT = _INSTALL_SH.read_text(encoding="utf-8")


def _capture_lines() -> str:
    m = re.search(r"^IS_UPGRADE=0\n\[\[.*IS_UPGRADE=1\n", _TEXT, re.M)
    assert m, "IS_UPGRADE capture not found"
    return m.group(0)


def _is_upgrade(current_link: str) -> str:
    out = subprocess.run(
        [
            shutil.which("bash") or "/bin/bash",
            "-c",
            f'set -euo pipefail\nCURRENT_LINK="$1"\n{_capture_lines()}echo "$IS_UPGRADE"',
            "x",
            current_link,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def test_is_upgrade_capture(tmp_path: Path) -> None:
    assert _is_upgrade(str(tmp_path)) == "1"
    assert _is_upgrade(str(tmp_path / "missing")) == "0"
    assert _is_upgrade("") == "0"
    dangling = tmp_path / "current"
    dangling.symlink_to(tmp_path / "gone")
    assert _is_upgrade(str(dangling)) == "1"


def test_is_upgrade_captured_before_filesystem_layout() -> None:
    assert _TEXT.index("IS_UPGRADE=0\n") < _TEXT.index('ui_step "Filesystem layout"')
    assert _TEXT.index("IS_UPGRADE=0\n") < _TEXT.index('ui_step "System user"')


# ── Pi gate (Task A3): the picker answer is the only prompt ─────────────────

_PI_START = "# ── Optional: pi coding agent"
_PI_END = "# Last verify action before the summary"


@dataclass
class PiResult:
    agent_install_called: bool
    tty_read_called: bool
    out: str


@pytest.fixture
def run_pi(tmp_path: Path):
    text = _INSTALL_SH.read_text(encoding="utf-8")
    head = text.index(_PI_START)
    pi_block = text[head : text.index(_PI_END, head)]

    def run(
        env: dict[str, str] | None = None,
        *,
        tty: bool = False,
        pi_present: bool = False,
        dev: str = "0",
        no_start: str = "0",
    ) -> PiResult:
        bindir = tmp_path / "bin"
        if bindir.exists():
            shutil.rmtree(bindir)
        bindir.mkdir()
        if pi_present:
            (bindir / "pi").write_text("#!/bin/sh\nexit 0\n")
            (bindir / "pi").chmod(0o755)
        hal0 = tmp_path / "hal0-stub"
        calls = tmp_path / "hal0-calls"
        tty_flag = tmp_path / "tty-read"
        for p in (calls, tty_flag):
            p.unlink(missing_ok=True)
        hal0.write_text(f'#!/bin/sh\necho "$@" >>"{calls}"\n')
        hal0.chmod(0o755)
        script = tmp_path / "pi.sh"
        script.write_text(
            "set -euo pipefail\n"
            "info() { printf 'INFO: %s\\n' \"$*\"; }\n"
            "warn() { printf 'WARN: %s\\n' \"$*\"; }\n"
            '_interactive() { [[ "${TTY:-0}" == "1" ]]; }\n'
            f'_tty_read() {{ : >"{tty_flag}"; }}\n'
            f'HAL0_BIN="{hal0}"\n'
            f'DEV_MODE="{dev}"; NO_START="{no_start}"\n' + pi_block + "\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [shutil.which("bash") or "/bin/bash", str(script)],
            capture_output=True,
            text=True,
            env={"PATH": str(bindir), "TTY": "1" if tty else "0", **(env or {})},
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr + proc.stdout
        return PiResult(
            agent_install_called=calls.exists() and "agent install pi" in calls.read_text(),
            tty_read_called=tty_flag.exists(),
            out=proc.stdout,
        )

    return run


def test_pi_installs_only_when_install_pi_set(run_pi) -> None:
    assert run_pi(env={"HAL0_INSTALL_PI": "1"}).agent_install_called is True
    assert run_pi(env={}).agent_install_called is False
    assert run_pi(env={"HAL0_INSTALL_PI": "1", "HAL0_SKIP_PI": "1"}).agent_install_called is False
    assert run_pi(env={"HAL0_INSTALL_PI": "1"}, pi_present=True).agent_install_called is False


def test_pi_block_never_prompts(run_pi) -> None:
    r = run_pi(env={"HAL0_INSTALL_PI": "1"}, tty=True)
    assert r.tty_read_called is False
    assert run_pi(env={}, tty=True).tty_read_called is False


def test_pi_install_needs_no_tty(run_pi) -> None:
    """The picker answer replaces the prompt, so the gate no longer needs a TTY."""
    assert run_pi(env={"HAL0_INSTALL_PI": "1"}, tty=False).agent_install_called is True


def test_pi_install_guards_dev_and_no_start(run_pi) -> None:
    assert run_pi(env={"HAL0_INSTALL_PI": "1"}, dev="1").agent_install_called is False
    assert run_pi(env={"HAL0_INSTALL_PI": "1"}, no_start="1").agent_install_called is False


def test_pi_off_prints_later_install_hint(run_pi) -> None:
    out = run_pi(env={}).out
    assert "agent install pi" in out and out.count("INFO:") == 1


def test_pi_already_installed_counts_as_selected(run_block) -> None:
    res = run_block(pi=True)
    assert res.states["pi"] == "selected"


def test_pi_absent_and_not_requested_is_skipped(run_block) -> None:
    assert run_block(pi=False).states["pi"] == "skipped"


_PI_START = '    if [[ "${DEV_MODE}" -eq 0 && "${NO_START}" -eq 0 ]] \\\n       && ! command -v pi'
_PI_END = "    # Last verify action before the summary"


def _run_pi_block(tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    head = text.index(_PI_START)
    frag = text[head : text.index(_PI_END, head)]
    script = tmp_path / "pi.sh"
    script.write_text(
        "set -euo pipefail\n"
        'info() { printf "INFO: %s\\n" "$*"; }\n'
        'warn() { printf "WARN: %s\\n" "$*"; }\n'
        'HAL0_BIN="hal0"\nDEV_MODE=0\nNO_START=0\n'
        'hal0() { printf "INSTALL %s\\n" "$*"; }\n' + frag.replace('"${HAL0_BIN}"', "hal0"),
        encoding="utf-8",
    )
    bindir = tmp_path / "nopi"
    bindir.mkdir(exist_ok=True)
    return subprocess.run(
        [shutil.which("bash") or "/bin/bash", str(script)],
        capture_output=True,
        text=True,
        env={"PATH": str(bindir), **env},
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


@pytest.mark.parametrize(
    "env",
    [{"HAL0_SKIP_PI": "1"}, {"HAL0_SKIP_PI": "1", "HAL0_INSTALL_PI": "1"}, {}],
)
def test_pi_hint_printed_once_and_never_installs(tmp_path: Path, env: dict[str, str]) -> None:
    proc = _run_pi_block(tmp_path, env)
    assert proc.returncode == 0, proc.stderr
    assert "INSTALL" not in proc.stdout
    assert proc.stdout.count("INFO: pi (terminal coding agent) not installed") == 1
    assert "agent install pi" in proc.stdout


def test_pi_installs_when_requested(tmp_path: Path) -> None:
    proc = _run_pi_block(tmp_path, {"HAL0_INSTALL_PI": "1"})
    assert proc.returncode == 0, proc.stderr
    assert "INSTALL agent install pi" in proc.stdout
    assert "INFO:" not in proc.stdout


def test_pi_non_numeric_install_knob_does_not_abort(tmp_path: Path) -> None:
    proc = _run_pi_block(tmp_path, {"HAL0_INSTALL_PI": "true"})
    assert proc.returncode == 0, proc.stderr
    assert "unbound variable" not in proc.stderr
    assert "INSTALL" not in proc.stdout
    assert proc.stdout.count("INFO: pi (terminal coding agent) not installed") == 1
