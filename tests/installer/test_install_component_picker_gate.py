"""installer/install.sh's component record.

The setup screens (or the --no-* flags and env knobs) decide which optional
components get laid down; the block cut out here only RECORDS the outcome in
``COMPONENT_STATES`` and logs one ``Components:`` line. It is sliced out by its
banner and run under real bash. Nothing in it may ask anything.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

_INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
_START = "# ── Components: record the choices ──"
_END = "# ── Hermes terminal tool: explicit opt-in, DEFAULTS OFF"

_STUBS = r"""
info() { printf 'INFO: %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*"; }
"""


@dataclass
class Result:
    env: dict[str, str]
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
        pi: bool = False,
    ) -> Result:
        bindir = tmp_path / "bin"
        if bindir.exists():
            shutil.rmtree(bindir)
        bindir.mkdir()
        # Only the tools the fragment needs; the host's pi/npm never leak in.
        for tool in ("env", "grep", "sed"):
            (bindir / tool).symlink_to(shutil.which(tool) or f"/usr/bin/{tool}")
        if pi:
            (bindir / "pi").write_text("#!/bin/sh\nexit 0\n")
            (bindir / "pi").chmod(0o755)
        script = tmp_path / "block.sh"
        script.write_text(
            "set -euo pipefail\n"
            + _STUBS
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
            env={"PATH": str(bindir), **(env or {})},
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr + proc.stdout
        res = Result(env={}, proc=proc)
        for line in proc.stdout.splitlines():
            if m := re.match(r"ENV (HAL0_\w+)=(.*)", line):
                res.env[m[1]] = m[2]
            elif line.startswith("STATE\t"):
                _, k, v = line.split("\t", 2)
                res.states[k] = v
        return res

    return run


def test_block_never_asks(block: str) -> None:
    assert "ui_component_picker" not in block
    assert "_interactive" not in block
    assert "_tty_read" not in block
    assert "/dev/tty" not in block


def test_the_block_leaves_the_environment_alone(run_block) -> None:
    env = {"HAL0_SKIP_HERMES": "1", "HAL0_INSTALL_PI": "1", "HAL0_IMAGE_MODELS": "recommended"}
    assert run_block(env=env).env == env
    assert run_block().env == {}


def test_component_states_recorded(run_block) -> None:
    r = run_block(env={"HAL0_SKIP_HERMES": "1"})
    assert r.states["hermes"] == "skipped" and r.states["openwebui"] == "selected"
    assert r.states["pi"] == "skipped"


def test_hindsight_unavailable_reason_is_recorded(run_block) -> None:
    r = run_block(env={"HINDSIGHT_UNAVAILABLE_REASON": "no usable Python 3.11-3.14"})
    assert r.states["hindsight"] == "unavailable:no usable Python 3.11-3.14"


def test_environment_states(run_block) -> None:
    r = run_block(env={"HAL0_SKIP_BRAIN_MODEL": "1", "HAL0_INSTALL_PI": "1"})
    assert r.states["brain"] == "skipped" and r.states["pi"] == "selected"
    assert r.states["comfyui"] == "selected"
    r2 = run_block(env={"HAL0_INSTALL_PI": "1", "HAL0_SKIP_PI": "1"})
    assert r2.states["pi"] == "skipped"


def test_skip_setup_states_unavailable(run_block) -> None:
    r = run_block(env={"HAL0_SKIP_SETUP": "1"})
    assert r.states["brain"] == "unavailable:HAL0_SKIP_SETUP=1"
    assert r.states["comfyui"] == "unavailable:HAL0_SKIP_SETUP=1"
    assert r.states["hermes"] == "selected" and r.states["openwebui"] == "selected"


@pytest.mark.parametrize(
    ("env", "row"),
    [
        ({"HAL0_SKIP_OPENWEBUI": "1"}, "openwebui"),
        ({"HAL0_IMAGE_MODELS": "none"}, "comfyui"),
        ({"HAL0_IMAGE_MODELS": "0"}, "comfyui"),
        ({"HAL0_SKIP_HERMES": "1"}, "hermes"),
        ({"HAL0_SKIP_HINDSIGHT": "1"}, "hindsight"),
        ({"HAL0_SKIP_BRAIN_MODEL": "1"}, "brain"),
        ({"HAL0_SKIP_PI": "1", "HAL0_INSTALL_PI": "1"}, "pi"),
    ],
)
def test_env_skip_knobs_are_recorded_as_skipped(run_block, env, row) -> None:
    r = run_block(env=env)
    assert r.states[row] == "skipped"
    assert all(v == "selected" for k, v in r.states.items() if k not in (row, "pi"))


def test_default_states(run_block) -> None:
    r = run_block()
    assert r.states == {
        "openwebui": "selected",
        "comfyui": "selected",
        "hermes": "selected",
        "pi": "skipped",
        "hindsight": "selected",
        "brain": "selected",
    }


def test_comfyui_keeps_a_real_image_models_choice(run_block) -> None:
    r = run_block(env={"HAL0_IMAGE_MODELS": "recommended"})
    assert r.env["HAL0_IMAGE_MODELS"] == "recommended"
    assert r.states["comfyui"] == "selected"


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


# ── Pi gate: nothing here asks ──────────────────────────────────────────────

_PI_START = "# ── Optional: pi coding agent"
_PI_END = "# Last verify action before the summary"


@dataclass
class PiResult:
    agent_install_called: bool
    out: str


@pytest.fixture
def run_pi(tmp_path: Path):
    text = _INSTALL_SH.read_text(encoding="utf-8")
    head = text.index(_PI_START)
    pi_block = text[head : text.index(_PI_END, head)]

    def run(
        env: dict[str, str] | None = None,
        *,
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
        calls.unlink(missing_ok=True)
        hal0.write_text(f'#!/bin/sh\necho "$@" >>"{calls}"\n')
        hal0.chmod(0o755)
        script = tmp_path / "pi.sh"
        script.write_text(
            "set -euo pipefail\n"
            "info() { printf 'INFO: %s\\n' \"$*\"; }\n"
            "warn() { printf 'WARN: %s\\n' \"$*\"; }\n"
            f'HAL0_BIN="{hal0}"\n'
            f'DEV_MODE="{dev}"; NO_START="{no_start}"\n' + pi_block + "\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [shutil.which("bash") or "/bin/bash", str(script)],
            capture_output=True,
            text=True,
            env={"PATH": str(bindir), **(env or {})},
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr + proc.stdout
        return PiResult(
            agent_install_called=calls.exists() and "agent install pi" in calls.read_text(),
            out=proc.stdout,
        )

    return run


def test_pi_installs_only_when_install_pi_set(run_pi) -> None:
    assert run_pi(env={"HAL0_INSTALL_PI": "1"}).agent_install_called is True
    assert run_pi(env={}).agent_install_called is False
    assert run_pi(env={"HAL0_INSTALL_PI": "1", "HAL0_SKIP_PI": "1"}).agent_install_called is False
    assert run_pi(env={"HAL0_INSTALL_PI": "1"}, pi_present=True).agent_install_called is False


def test_pi_block_never_prompts(run_pi) -> None:
    # No prompt helper is defined in the harness: a call to one would fail the run.
    assert run_pi(env={"HAL0_INSTALL_PI": "1"}).agent_install_called is True
    assert run_pi(env={}).agent_install_called is False


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


# ── The log line ────────────────────────────────────────────────────────────


def test_chosen_set_is_logged(run_block) -> None:
    r = run_block(env={"HAL0_SKIP_HERMES": "1", "HAL0_INSTALL_PI": "1"})
    assert r.proc is not None
    assert (
        "INFO: Components: openwebui on, comfyui on, hermes off, pi on, hindsight on, brain on"
        in r.proc.stdout
    )


def test_unavailable_components_are_logged_as_such(run_block) -> None:
    r = run_block(env={"HAL0_SKIP_SETUP": "1"})
    assert r.proc is not None
    assert (
        "INFO: Components: openwebui on, comfyui unavailable, hermes on, pi off, "
        "hindsight on, brain unavailable" in r.proc.stdout
    )


# ── Skip lines name their later-install command ─────────────────────────────


def _skip_line(start: str, stop: str, env: dict[str, str]) -> str:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    prefix, _, start = start.rpartition("\n")
    head = text.index(start)
    frag = prefix + "\n" + text[head : text.index(stop, head)] + "\nelse\n    echo ELSE\nfi\n"
    proc = subprocess.run(
        [
            shutil.which("bash") or "/bin/bash",
            "-c",
            'set -euo pipefail\ninfo() { printf "INFO: %s\\n" "$*"; }\nDEV_MODE=0; NO_START=0\n'
            + frag,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_brain_skip_line_names_later_install() -> None:
    out = _skip_line(
        'if [[ "${DEV_MODE}" -eq 1 ]]; then\n    info "dev mode — skipping the brain model pull',
        "\nelse\n",
        {"HAL0_SKIP_BRAIN_MODEL": "1"},
    )
    assert "Skipping the brain model pull" in out
    assert "Pull one later with 'hal0 model pull <id>' or from the dashboard." in out


def test_comfyui_skip_line_names_later_install() -> None:
    out = _skip_line(
        "if false; then :\n"
        + 'elif [[ "${HAL0_SKIP_SETUP:-0}" == "1" || "${HAL0_IMAGE_MODELS:-}" == "none"',
        '\nelif [[ "${NO_START}" -eq 1 ]]',
        {"HAL0_IMAGE_MODELS": "none"},
    )
    assert "Skipping the starter image models" in out
    assert "Install one later with 'hal0 comfyui models install <id>' or from the dashboard." in out
