"""install.sh stops and disables an installed assistant that confirmed screens turned off.

The Hermes step is cut out of install.sh by text anchors and run under real
bash, on a PATH that holds only stubs (`systemctl`, `hal0`, the venv's python):
every call is recorded in one log and nothing touches the real system.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEXT = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")

_FUNCS_START = "unit_enable_state() {\n"
_FUNCS_END = "gateway_install_note() {"
_START = '    _gateway_state="$(unit_enable_state hermes-gateway.service)"\n'
_END = "    # ── Optional: pi coding agent"
_VENV = "/var/lib/hal0/venvs/hermes/bin/hermes"
_AGENT = "hal0-agent@hermes.service"
_GATEWAY = "hermes-gateway.service"
_MANUAL = f"systemctl disable --now {_AGENT} {_GATEWAY}"


def _kill_group(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)


def _run_group(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    with subprocess.Popen(argv, start_new_session=True, **kwargs) as proc:  # type: ignore[call-overload]
        try:
            out, err = proc.communicate(timeout=30)
        except BaseException:
            _kill_group(proc.pid)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.communicate(timeout=5)
            raise
        _kill_group(proc.pid)
        return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def _functions() -> str:
    head = TEXT.index(_FUNCS_START)
    tail = TEXT.index(_FUNCS_END, head)
    return TEXT[head : TEXT.index("\n}\n", tail) + 3]


def _region() -> str:
    head = TEXT.index(_START)
    return TEXT[head : TEXT.index(_END, head)]


_STUB_SYSTEMCTL = """#!/bin/bash
printf 'systemctl %s\\n' "$*" >>"$STUB_LOG"
case "$1" in
    is-enabled)
        if [[ "$2" == hermes-gateway.service ]]; then printf '%s\\n' "${STUB_GW_STATE:-enabled}"; fi
        exit 0 ;;
    is-active) exit "${STUB_GW_ACTIVE_RC:-1}" ;;
    disable)
        if [[ "$*" == *hal0-agent@hermes.service* ]]; then exit "${STUB_DISABLE_RC:-0}"; fi
        exit "${STUB_GW_DISABLE_RC:-0}" ;;
esac
exit 0
"""
_STUB_HAL0 = """#!/bin/bash
printf 'hal0 %s\\n' "$*" >>"$STUB_LOG"
exit 0
"""
_STUB_PYTHON = """#!/bin/bash
printf 'python %s\\n' "$*" >>"$STUB_LOG"
exit 0
"""


def _exe(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _run(
    tmp_path: Path,
    *,
    stop: str | None = None,
    skip: str | None = None,
    terminal: str | None = None,
    venv: bool = True,
    unit: bool = True,
    gw_state: str = "enabled",
    disable_rc: int = 0,
    gw_disable_rc: int = 0,
    name: str = "run",
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the Hermes step; return the process and the recorded calls (in order)."""
    work = tmp_path / name
    bindir = work / "bin"
    _exe(bindir / "systemctl", _STUB_SYSTEMCTL)
    hal0 = work / "hal0"
    _exe(hal0, _STUB_HAL0)
    venv_dir = work / "venv"
    _exe(venv_dir / "bin" / "python", _STUB_PYTHON)
    fake_hermes = work / "hermes"
    if venv:
        _exe(fake_hermes, "#!/bin/sh\n")
    unit_dir = work / "units"
    unit_dir.mkdir(parents=True, exist_ok=True)
    if unit:
        (unit_dir / "hal0-agent@.service").write_text("", encoding="utf-8")
    (unit_dir / "hermes-gateway.service").write_text("", encoding="utf-8")
    log = work / "calls.log"
    region = _region()
    assert _VENV in region
    script = (
        "set -euo pipefail\n"
        'info() { printf \'INFO: %s\\n\' "$*" | tee -a "$STUB_LOG"; }\n'
        'warn() { printf \'WARN: %s\\n\' "$*" | tee -a "$STUB_LOG"; }\n'
        "wait_active() { return 0; }\n"
        "preflight_git() { return 0; }\n"
        + _functions()
        + "declare -A COMPONENT_STATES=([hermes]=skipped)\n"
        "_hermes_install_args=()\n"
        f'AGENT_UNIT_DST="{unit_dir}/hal0-agent@.service"\n'
        f'UNIT_DIR="{unit_dir}"\n'
        f'VENV_DIR="{venv_dir}"\n'
        f'HAL0_BIN="{hal0}"\n' + region.replace(_VENV, str(fake_hermes)) + "\necho REGION_DONE\n"
        'echo "STATE=${COMPONENT_STATES[hermes]}"\n'
    )
    # tee is not a builtin: give the script a PATH of stubs plus the tools bash needs.
    for tool in ("tee",):
        found = next(p for p in ("/usr/bin/" + tool, "/bin/" + tool) if os.path.exists(p))
        link = bindir / tool
        if not link.exists():
            link.symlink_to(found)
    env = {
        "PATH": str(bindir),
        "STUB_LOG": str(log),
        "STUB_GW_STATE": gw_state,
        "STUB_DISABLE_RC": str(disable_rc),
        "STUB_GW_DISABLE_RC": str(gw_disable_rc),
    }
    if stop is not None:
        env["HAL0_HERMES_STOP"] = stop
    if skip is not None:
        env["HAL0_SKIP_HERMES"] = skip
    if terminal is not None:
        env["HAL0_HERMES_TERMINAL"] = terminal
    log.write_text("", encoding="utf-8")
    proc = _run_group(
        ["/bin/bash", "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    calls = log.read_text(encoding="utf-8").splitlines()
    return proc, calls


def _state(proc: subprocess.CompletedProcess[str]) -> str:
    (line,) = [ln for ln in proc.stdout.splitlines() if ln.startswith("STATE=")]
    return line[len("STATE=") :]


def _sys(calls: list[str]) -> list[str]:
    return [c for c in calls if c.startswith("systemctl ")]


def _ok(proc: subprocess.CompletedProcess[str]) -> None:
    assert proc.returncode == 0 and "REGION_DONE" in proc.stdout, proc.stdout + proc.stderr


def test_stop_signal_on_an_installed_assistant_disables_both_units_and_enables_nothing(
    tmp_path: Path,
) -> None:
    proc, calls = _run(tmp_path, stop="1", skip="1", terminal="0")
    _ok(proc)
    disables = [c for c in _sys(calls) if c.startswith("systemctl disable")]
    assert disables == [
        f"systemctl disable --now {_AGENT}",
        f"systemctl disable --now {_GATEWAY}",
    ]
    assert not [c for c in _sys(calls) if "enable --now" in c or c.startswith("systemctl enable")]
    assert not [c for c in calls if c.startswith("python ")]  # no gateway unit rewrite
    assert _state(proc) == "stopped"
    info = [c for c in calls if c.startswith("INFO:") and "stays installed" in c]
    assert len(info) == 1
    assert "systemctl enable --now hal0-agent@hermes" in info[0]
    assert "WARN" not in proc.stdout
    # the terminal-tool "off" is applied first, as before, and only once
    assert calls.count("hal0 agent reprovision hermes") == 1
    assert calls.index("hal0 agent reprovision hermes") < calls.index(
        f"systemctl disable --now {_AGENT}"
    )


def test_stop_signal_without_the_assistant_on_the_box_is_a_plain_skip(tmp_path: Path) -> None:
    for i, kwargs in enumerate(({"venv": False}, {"unit": False}, {"venv": False, "unit": False})):
        proc, calls = _run(tmp_path, stop="1", skip="1", name=f"n{i}", **kwargs)
        _ok(proc)
        assert not [
            c for c in _sys(calls) if c.startswith(("systemctl disable", "systemctl enable"))
        ], kwargs
        assert _state(proc) == "skipped", kwargs
        assert not [c for c in calls if "stays installed" in c], kwargs


def test_a_skip_without_the_stop_signal_leaves_an_installed_assistant_running(
    tmp_path: Path,
) -> None:
    # today's behaviour: the enable block still runs
    proc, calls = _run(tmp_path, skip="1")
    _ok(proc)
    assert f"systemctl enable --now {_AGENT}" in calls
    assert not [c for c in _sys(calls) if c.startswith("systemctl disable")]
    assert _state(proc) == "skipped"


@pytest.mark.parametrize("value", ["0", "true", "yes", "", "11", " 1"])
def test_only_the_exact_value_one_is_a_stop_signal(tmp_path: Path, value: str) -> None:
    proc, calls = _run(tmp_path, stop=value, skip="1")
    _ok(proc)
    assert f"systemctl enable --now {_AGENT}" in calls
    assert not [c for c in _sys(calls) if c.startswith("systemctl disable")]
    assert _state(proc) == "skipped"


@pytest.mark.parametrize("state", ["masked", "masked-runtime", "disabled"])
def test_a_masked_or_disabled_gateway_gets_no_call_at_all(tmp_path: Path, state: str) -> None:
    proc, calls = _run(tmp_path, stop="1", skip="1", gw_state=state)
    _ok(proc)
    assert f"systemctl disable --now {_AGENT}" in calls
    assert not [
        c for c in calls if _GATEWAY in c and "is-enabled" not in c and "is-active" not in c
    ]
    assert not [c for c in calls if "mask" in c.replace("masked", "")]
    # only the read-only `state` query of the capture above the step may run
    assert not [c for c in calls if c.startswith("python ") and not c.endswith(" state")]
    assert _state(proc) == "stopped"


def test_a_runtime_enabled_gateway_is_disabled_for_the_runtime_only(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, stop="1", skip="1", gw_state="enabled-runtime")
    _ok(proc)
    assert f"systemctl disable --runtime --now {_GATEWAY}" in calls
    assert _state(proc) == "stopped"


def test_a_failing_disable_warns_with_the_command_and_keeps_the_state_skipped(
    tmp_path: Path,
) -> None:
    proc, calls = _run(tmp_path, stop="1", skip="1", disable_rc=1)
    _ok(proc)  # not fatal
    warns = [c for c in calls if c.startswith("WARN:")]
    assert any("may still be running" in w and _MANUAL in w for w in warns), warns
    assert _state(proc) == "skipped"
    assert not [c for c in calls if "stays installed" in c]
    assert not [c for c in _sys(calls) if c.startswith("systemctl enable")]


def test_the_failed_gateway_disable_also_keeps_the_state_skipped(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, stop="1", skip="1", gw_disable_rc=1)
    _ok(proc)
    assert _state(proc) == "skipped"
    assert any(c.startswith("WARN:") and _MANUAL in c for c in calls)


def test_round_trip_a_later_run_with_the_assistant_on_starts_it_again(tmp_path: Path) -> None:
    first, calls1 = _run(tmp_path, stop="1", skip="1", terminal="0", name="first")
    _ok(first)
    assert _state(first) == "stopped"
    assert f"systemctl enable --now {_AGENT}" not in calls1
    # the later run: assistant on (no skip, no stop signal), the gateway now disabled by the stop
    second, calls2 = _run(tmp_path, name="second", gw_state="disabled")
    _ok(second)
    assert f"systemctl enable --now {_AGENT}" in calls2
    assert not [c for c in _sys(calls2) if c.startswith("systemctl disable --now hal0-agent")]
    assert any("is running" in c for c in calls2 if c.startswith("INFO:"))


def test_the_step_sits_after_the_terminal_apply_and_before_the_enable_block() -> None:
    apply_call = TEXT.index('"${HAL0_BIN}" agent reprovision hermes')
    stop = TEXT.index('"${HAL0_HERMES_STOP:-}" == "1"')
    enable = TEXT.index("systemctl enable --now hal0-agent@hermes.service")
    assert apply_call < stop < enable
    assert TEXT.count("HAL0_HERMES_STOP") >= 1


def test_the_stop_is_unreachable_with_dev_or_no_start() -> None:
    # The whole Hermes step lives in the else branch of the dev / --no-start test.
    gate = TEXT.index('if [[ "${DEV_MODE}" -eq 1 || "${NO_START}" -eq 1 ]]; then')
    branch = TEXT.index("\nelse\n", gate)
    stop = TEXT.index('"${HAL0_HERMES_STOP:-}" == "1"')
    assert gate < branch < stop
    assert "\nfi\n" not in TEXT[gate:branch]


def test_usage_text_says_what_turning_the_assistant_off_does() -> None:
    usage = " ".join(
        TEXT[TEXT.index("--help|-h)") : TEXT.index("exit 0", TEXT.index("--help|-h)"))].split()
    ).replace("\\`", "`")
    sentence = (
        "Turning the assistant off on the setup screens, on a box that already has it, "
        "stops it; it stays installed, and turning it on there again, or "
        "`systemctl enable --now hal0-agent@hermes`, starts it."
    )
    assert sentence in usage
    assert "A skip flag or variable alone" in usage
    assert "does not stop an installed assistant" in usage
