"""install.sh stops and disables an installed assistant that confirmed screens turned off.

The Hermes step is cut out of install.sh by text anchors and run under real
bash, on a PATH that holds only stubs (`systemctl`, `hal0`, the venv's python)
and the few tools bash needs: every call is recorded in one log and nothing
touches the real system or /etc. The stubs keep unit state in files, so a
disable is visible to a later `is-enabled` / `is-active`. The record lives
under the test's own folder through HAL0_FACTS_AGENTS_DIR (honoured by
facts.sh only when not root).
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
FACTS = ROOT / "installer" / "lib" / "facts.sh"

_FUNCS_START = "unit_enable_state() {\n"
_FUNCS_END = "gateway_install_note() {"
_START = '    _gateway_state="$(unit_enable_state hermes-gateway.service)"\n'
_END = "    # ── Optional: pi coding agent"
_VENV = "/var/lib/hal0/venvs/hermes/bin/hermes"
_AGENT = "hal0-agent@hermes.service"
_GATEWAY = "hermes-gateway.service"
_MANUAL = f"systemctl disable --now {_AGENT} {_GATEWAY}"
_RECORD = "hermes-stopped-by-setup"


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


# Unit state lives in $STATE_DIR/<key>.state (the is-enabled word) and
# <key>.active (exists = running). Keys: agent, gw (canonical), hashed.
_STUB_SYSTEMCTL = """#!/bin/bash
printf 'systemctl %s\\n' "$*" >>"$STUB_LOG"
mode="$1"; shift
now=0; runtime=0; units=()
for a in "$@"; do
    case "$a" in
        --now) now=1 ;;
        --runtime) runtime=1 ;;
        --quiet) ;;
        -*) ;;
        *) units+=("$a") ;;
    esac
done
u="${units[0]:-}"
case "$u" in
    hal0-agent@hermes.service) key=agent ;;
    hermes-gateway.service) key=gw ;;
    hermes-gateway-abcd1234.service) key=hashed ;;
    *) key=other ;;
esac
S="$STATE_DIR"
state() { cat "$S/$key.state" 2>/dev/null; }
case "$mode" in
    is-enabled) state; exit 0 ;;
    is-active) [[ -f "$S/$key.active" ]]; exit $? ;;
    disable)
        rcvar="STUB_DISABLE_RC_$key"
        rc="${!rcvar:-0}"
        [[ "$rc" -ne 0 ]] && exit "$rc"
        case "$(state)" in masked*|"") ;; *) echo disabled >"$S/$key.state" ;; esac
        [[ $now -eq 1 ]] && rm -f "$S/$key.active"
        exit 0 ;;
    enable)
        case "$(state)" in masked*) exit 1 ;; esac
        if [[ $runtime -eq 1 ]]; then echo enabled-runtime >"$S/$key.state"; else echo enabled >"$S/$key.state"; fi
        [[ $now -eq 1 ]] && touch "$S/$key.active"
        exit 0 ;;
esac
exit 0
"""
_STUB_HAL0 = """#!/bin/bash
printf 'hal0 %s\\n' "$*" >>"$STUB_LOG"
exit 0
"""
# hal0's venv python: `migrate` moves a hashed unit onto the canonical one and
# keeps its state; `state` prints the gateway the operator has.
_STUB_PYTHON = """#!/bin/bash
printf 'python %s\\n' "$*" >>"$STUB_LOG"
S="$STATE_DIR"
st() { cat "$S/$1.state" 2>/dev/null; }
act() { if [[ -f "$S/$1.active" ]]; then echo 1; else echo 0; fi; }
case "$*" in
    "-I -m hal0.agents.hermes_gateway_regen migrate")
        [[ "${STUB_MIGRATE_RC:-0}" -ne 0 ]] && exit "$STUB_MIGRATE_RC"
        [[ -f "$S/hashed.state" && -z "${STUB_MIGRATE_NOOP:-}" ]] || exit 0
        h="$(st hashed)"; ha="$(act hashed)"
        case "$(st gw)" in
            masked*|enabled*) ;;
            *)
                if [[ "$h" == enabled* ]]; then echo enabled >"$S/gw.state"; touch "$S/gw.active"
                elif [[ "$ha" == 1 ]]; then touch "$S/gw.active"; fi ;;
        esac
        rm -f "$S/hashed.state" "$S/hashed.active"
        exit 0 ;;
    "-I -m hal0.agents.hermes_gateway_regen state")
        g="$(st gw)"; ga="$(act gw)"
        if [[ -f "$S/hashed.state" ]] && [[ "$g" == "" || ( "$g" != enabled* && "$g" != masked* && "$ga" == 0 ) ]]; then
            echo "$(st hashed) $(act hashed)"
        elif [[ -n "$g" ]]; then echo "$g $ga"
        else echo "not-found 0"; fi
        exit 0 ;;
esac
exit 0
"""

# Stands in for the installer's `nofollow` function (the real helper is
# exercised once, below): records the call, then writes under the test folder.
_NOFOLLOW = r"""
nofollow() {
    printf 'nofollow %s\n' "$*" >>"$STUB_LOG"
    [[ "${STUB_NOFOLLOW_RC:-0}" -eq 0 ]] || return "$STUB_NOFOLLOW_RC"
    local verb="$1" mode="" last="" prev="" a
    shift
    for a in "$@"; do
        [[ "$prev" == "--mode" ]] && mode="$a"
        prev="$a"
        last="$a"
    done
    case "$verb" in
        dir) mkdir -p "$last" ;;
        write) cat >"$last"; chmod "$mode" "$last" ;;
    esac
}
"""


def _exe(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


class Box:
    """One run of the Hermes step and what it left behind."""

    def __init__(self, work: Path, proc: subprocess.CompletedProcess[str], calls: list[str]):
        self.work = work
        self.proc = proc
        self.calls = calls

    def state(self) -> str:
        (line,) = [ln for ln in self.proc.stdout.splitlines() if ln.startswith("STATE=")]
        return line[len("STATE=") :]

    def ok(self) -> None:
        assert self.proc.returncode == 0 and "REGION_DONE" in self.proc.stdout, (
            self.proc.stdout + self.proc.stderr
        )

    def sys(self) -> list[str]:
        return [c for c in self.calls if c.startswith("systemctl ")]

    def writes(self) -> list[str]:
        """systemctl calls that change anything (not is-enabled / is-active / daemon-reload)."""
        return [
            c
            for c in self.sys()
            if c.startswith(("systemctl disable", "systemctl enable", "systemctl stop"))
        ]

    def record_path(self) -> Path:
        return self.work / "agents" / _RECORD

    def record(self) -> str | None:
        p = self.record_path()
        return p.read_text(encoding="utf-8") if p.is_file() else None

    def unit(self, key: str) -> str | None:
        p = self.work / "state" / f"{key}.state"
        return p.read_text(encoding="utf-8").strip() if p.exists() else None

    def running(self, key: str) -> bool:
        return (self.work / "state" / f"{key}.active").exists()

    def infos(self) -> list[str]:
        return [c for c in self.calls if c.startswith("INFO:")]

    def warns(self) -> list[str]:
        return [c for c in self.calls if c.startswith("WARN:")]

    def enables_gateway(self) -> bool:
        return any(c.startswith(f"systemctl enable --now {_GATEWAY}") for c in self.calls)


def _run(
    tmp_path: Path,
    *,
    stop: str | None = None,
    skip: str | None = None,
    terminal: str | None = None,
    venv: bool = True,
    unit: bool = True,
    gw_state: str | None = "enabled",
    gw_active: bool | None = None,
    hashed: str | None = None,
    hashed_active: bool = False,
    agent_state: str | None = None,
    agent_active: bool | None = None,
    record: str | None = None,
    record_link: bool = False,
    env_extra: dict[str, str] | None = None,
    name: str = "run",
) -> Box:
    """Run the Hermes step; return the process, the recorded calls and the files left."""
    work = tmp_path / name
    bindir = work / "bin"
    state = work / "state"
    state.mkdir(parents=True, exist_ok=True)
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
    if gw_state is not None:
        (state / "gw.state").write_text(gw_state + "\n", encoding="utf-8")
    gw_running = gw_active if gw_active is not None else gw_state in ("enabled", "enabled-runtime")
    if gw_running:
        (state / "gw.active").touch()
    if hashed is not None:
        (state / "hashed.state").write_text(hashed + "\n", encoding="utf-8")
        if hashed_active:
            (state / "hashed.active").touch()
    if agent_state is not None:
        (state / "agent.state").write_text(agent_state + "\n", encoding="utf-8")
    agent_running = agent_active if agent_active is not None else agent_state == "enabled"
    if agent_running:
        (state / "agent.active").touch()
    agents = work / "agents"
    agents.mkdir(exist_ok=True)
    if record_link:
        (work / "elsewhere").write_text("agent\n", encoding="utf-8")
        (agents / _RECORD).symlink_to(work / "elsewhere")
    elif record is not None:
        (agents / _RECORD).write_text(record, encoding="utf-8")
    log = work / "calls.log"
    region = _region()
    assert _VENV in region
    script = (
        "set -euo pipefail\n"
        'info() { printf \'INFO: %s\\n\' "$*" >>"$STUB_LOG"; }\n'
        'warn() { printf \'WARN: %s\\n\' "$*" >>"$STUB_LOG"; }\n'
        "wait_active() { return 0; }\n"
        "preflight_git() { return 0; }\n"
        + _NOFOLLOW
        + f'source "{FACTS}"\n'
        + _functions()
        + "declare -A COMPONENT_STATES=([hermes]=skipped)\n"
        "_hermes_install_args=()\n"
        f'AGENT_UNIT_DST="{unit_dir}/hal0-agent@.service"\n'
        f'UNIT_DIR="{unit_dir}"\n'
        f'VENV_DIR="{venv_dir}"\n'
        f'HAL0_BIN="{hal0}"\n' + region.replace(_VENV, str(fake_hermes)) + "\necho REGION_DONE\n"
        'echo "STATE=${COMPONENT_STATES[hermes]}"\n'
    )
    for tool in ("rm", "mkdir", "chmod", "cat", "touch"):
        found = next(p for p in ("/usr/bin/" + tool, "/bin/" + tool) if os.path.exists(p))
        link = bindir / tool
        if not link.exists():
            link.symlink_to(found)
    env = {
        "PATH": str(bindir),
        "STUB_LOG": str(log),
        "STATE_DIR": str(state),
        "HAL0_FACTS_AGENTS_DIR": str(agents),
    }
    if stop is not None:
        env["HAL0_HERMES_STOP"] = stop
    if skip is not None:
        env["HAL0_SKIP_HERMES"] = skip
    if terminal is not None:
        env["HAL0_HERMES_TERMINAL"] = terminal
    env.update(env_extra or {})
    log.write_text("", encoding="utf-8")
    proc = _run_group(
        ["/bin/bash", "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    return Box(work, proc, log.read_text(encoding="utf-8").splitlines())


# ── the stop ──────────────────────────────────────────────────────────────


def test_stop_signal_on_an_installed_assistant_disables_both_units_and_enables_nothing(
    tmp_path: Path,
) -> None:
    box = _run(tmp_path, stop="1", skip="1", terminal="0", agent_state="enabled")
    box.ok()
    assert [c for c in box.sys() if c.startswith("systemctl disable")] == [
        f"systemctl disable --now {_AGENT}",
        f"systemctl disable --now {_GATEWAY}",
    ]
    assert not [c for c in box.sys() if c.startswith("systemctl enable")]
    assert box.state() == "stopped"
    assert (box.unit("agent"), box.unit("gw")) == ("disabled", "disabled")
    assert not box.running("agent") and not box.running("gw")
    info = [c for c in box.infos() if "stays installed" in c]
    assert len(info) == 1 and "was stopped and disabled" in info[0]
    assert "systemctl enable --now hal0-agent@hermes" in info[0]
    assert not box.warns()
    # the terminal-tool "off" is applied first, as before, and only once
    assert box.calls.count("hal0 agent reprovision hermes") == 1
    assert box.calls.index("hal0 agent reprovision hermes") < box.calls.index(
        f"systemctl disable --now {_AGENT}"
    )


def test_the_record_is_written_with_the_right_lines_owner_and_mode(tmp_path: Path) -> None:
    box = _run(tmp_path, stop="1", skip="1", agent_state="enabled")
    box.ok()
    assert box.record() == "agent\ngateway\n"
    assert (box.record_path().stat().st_mode & 0o777) == 0o644
    rec = str(box.record_path())
    assert (
        f"nofollow dir --create --owner root:root --mode 0755 --expect root {box.work}/agents"
        in (box.calls)
    )
    assert f"nofollow write --owner root:root --mode 0644 --require-trusted-dir {rec}" in box.calls
    # the path is the facts library's one definition, not a literal in install.sh
    assert "hal0_hermes_stop_record_path" in TEXT


def test_the_record_names_only_what_the_stop_disabled(tmp_path: Path) -> None:
    # a gateway the person had disabled is not named: the lift must leave it off
    box = _run(tmp_path, stop="1", skip="1", agent_state="enabled", gw_state="disabled")
    box.ok()
    assert box.record() == "agent\n"


def test_stop_signal_without_the_assistant_on_the_box_is_a_plain_skip(tmp_path: Path) -> None:
    for i, kwargs in enumerate(({"venv": False}, {"unit": False}, {"venv": False, "unit": False})):
        box = _run(tmp_path, stop="1", skip="1", name=f"n{i}", **kwargs)
        box.ok()
        assert not box.writes(), kwargs
        assert box.state() == "skipped", kwargs
        assert box.record() is None, kwargs
        assert not [c for c in box.calls if "stays installed" in c], kwargs


def test_a_skip_without_the_stop_signal_leaves_an_installed_assistant_running(
    tmp_path: Path,
) -> None:
    box = _run(tmp_path, skip="1")
    box.ok()
    assert f"systemctl enable --now {_AGENT}" in box.calls  # today's behaviour
    assert not [c for c in box.sys() if c.startswith("systemctl disable")]
    assert box.state() == "skipped"
    assert box.record() is None


@pytest.mark.parametrize("value", ["0", "true", "yes", "", "11", " 1", "2"])
def test_only_the_exact_value_one_is_a_stop_signal(tmp_path: Path, value: str) -> None:
    box = _run(tmp_path, stop=value, skip="1")
    box.ok()
    assert f"systemctl enable --now {_AGENT}" in box.calls
    assert not [c for c in box.sys() if c.startswith("systemctl disable")]
    assert box.state() == "skipped"
    assert box.record() is None


@pytest.mark.parametrize("state", ["masked", "masked-runtime", "disabled"])
def test_a_masked_or_disabled_gateway_gets_no_call_at_all(tmp_path: Path, state: str) -> None:
    box = _run(tmp_path, stop="1", skip="1", gw_state=state, gw_active=False)
    box.ok()
    assert f"systemctl disable --now {_AGENT}" in box.calls
    assert not [
        c for c in box.calls if _GATEWAY in c and "is-enabled" not in c and "is-active" not in c
    ]
    assert not [c for c in box.calls if "mask" in c.replace("masked", "")]
    # only the read-only `state` query and `migrate` (which keeps a mask) may run
    assert not [
        c for c in box.calls if c.startswith("python ") and not c.endswith((" state", " migrate"))
    ]
    assert box.unit("gw") == state
    assert box.state() == "stopped"
    assert box.record() == "agent\n"


def test_a_runtime_enabled_gateway_is_disabled_for_the_runtime_only(tmp_path: Path) -> None:
    box = _run(tmp_path, stop="1", skip="1", gw_state="enabled-runtime")
    box.ok()
    assert f"systemctl disable --runtime --now {_GATEWAY}" in box.calls
    assert box.state() == "stopped"
    assert box.record() == "agent\ngateway\n"


# ── the gateway that is really running, whichever name it has ────────────────


def test_a_hashed_gateway_alone_is_moved_onto_the_canonical_name_and_stopped(
    tmp_path: Path,
) -> None:
    box = _run(
        tmp_path,
        stop="1",
        skip="1",
        gw_state=None,
        hashed="enabled",
        hashed_active=True,
        agent_state="enabled",
    )
    box.ok()
    migrate = "python -I -m hal0.agents.hermes_gateway_regen migrate"
    assert migrate in box.calls
    assert box.calls.index(migrate) < box.calls.index(f"systemctl disable --now {_GATEWAY}")
    assert box.unit("hashed") is None and not box.running("hashed")
    assert box.unit("gw") == "disabled" and not box.running("gw")
    assert box.state() == "stopped"
    assert box.record() == "agent\ngateway\n"
    assert not [c for c in box.sys() if c.startswith("systemctl enable")]
    assert not box.warns()


def test_a_hashed_gateway_beside_a_disabled_canonical_one_is_stopped_too(tmp_path: Path) -> None:
    box = _run(
        tmp_path,
        stop="1",
        skip="1",
        gw_state="disabled",
        gw_active=False,
        hashed="enabled",
        hashed_active=True,
        agent_state="enabled",
    )
    box.ok()
    assert box.unit("hashed") is None and not box.running("hashed")
    assert box.unit("gw") == "disabled" and not box.running("gw")
    assert box.state() == "stopped"
    assert box.record() == "agent\ngateway\n"


def test_a_gateway_started_by_hand_is_stopped_and_not_named_in_the_record(
    tmp_path: Path,
) -> None:
    box = _run(tmp_path, stop="1", skip="1", gw_state="disabled", gw_active=True)
    box.ok()
    assert f"systemctl disable --now {_GATEWAY}" in box.calls
    assert not box.running("gw") and box.unit("gw") == "disabled"
    assert box.state() == "stopped"
    assert box.record() == "agent\n"  # the person had it disabled: a lift leaves it so


def test_the_state_is_stopped_only_when_no_gateway_unit_of_either_name_is_left_running(
    tmp_path: Path,
) -> None:
    # a mover that leaves the hashed unit running (it did nothing)
    box = _run(
        tmp_path,
        stop="1",
        skip="1",
        gw_state="disabled",
        gw_active=False,
        hashed="enabled",
        hashed_active=True,
        env_extra={"STUB_MIGRATE_NOOP": "1"},
    )
    box.ok()
    assert box.state() == "stop-failed"
    assert any(_MANUAL in w and "may still be running" in w for w in box.warns())
    assert box.record() is None


def test_a_failing_migrate_is_a_failed_stop(tmp_path: Path) -> None:
    box = _run(
        tmp_path,
        stop="1",
        skip="1",
        gw_state=None,
        hashed="enabled",
        hashed_active=True,
        env_extra={"STUB_MIGRATE_RC": "1"},
    )
    box.ok()
    assert box.state() == "stop-failed"
    assert box.record() is None
    assert not [c for c in box.sys() if c.startswith("systemctl enable")]


def test_a_failing_disable_warns_with_the_command_and_marks_the_stop_failed(
    tmp_path: Path,
) -> None:
    box = _run(tmp_path, stop="1", skip="1", env_extra={"STUB_DISABLE_RC_agent": "1"})
    box.ok()  # not fatal
    assert any("may still be running" in w and _MANUAL in w for w in box.warns()), box.warns()
    assert box.state() == "stop-failed"
    assert not [c for c in box.calls if "stays installed" in c]
    assert not [c for c in box.sys() if c.startswith("systemctl enable")]
    assert box.record() is None


def test_the_failed_gateway_disable_also_marks_the_stop_failed(tmp_path: Path) -> None:
    box = _run(tmp_path, stop="1", skip="1", env_extra={"STUB_DISABLE_RC_gw": "1"})
    box.ok()
    assert box.state() == "stop-failed"
    assert any(_MANUAL in w for w in box.warns())


def test_a_record_that_cannot_be_written_warns_and_the_stop_still_counts(tmp_path: Path) -> None:
    box = _run(tmp_path, stop="1", skip="1", env_extra={"STUB_NOFOLLOW_RC": "1"})
    box.ok()
    assert box.state() == "stopped"
    assert any("could not write" in w and "may start the assistant again" in w for w in box.warns())


# ── the stop lasts; only a person lifts it ───────────────────────────────────


def test_record_and_agent_still_disabled_then_a_run_without_screens_starts_nothing(
    tmp_path: Path,
) -> None:
    first = _run(tmp_path, stop="1", skip="1", agent_state="enabled", name="first")
    first.ok()
    rec = first.record()
    assert rec == "agent\ngateway\n"
    # an upgrade, a headless run, --answers or saved answers: no stop signal; the
    # assistant is "on" by default (no skip), or skipped by a flag
    for i, skip in enumerate((None, "1")):
        box = _run(
            tmp_path,
            record=rec,
            gw_state="disabled",
            gw_active=False,
            agent_state="disabled",
            skip=skip,
            name=f"later{i}",
        )
        box.ok()
        assert not box.writes(), box.calls
        assert not [c for c in box.calls if c.startswith("hal0 agent install")]
        assert not [c for c in box.calls if c.startswith("python ") and c.endswith(" install")]
        assert box.state() == "stopped"
        assert box.record() == rec  # kept, not reduced
        assert (box.unit("agent"), box.unit("gw")) == ("disabled", "disabled")
        assert not box.running("agent") and not box.running("gw")
        held = [c for c in box.infos() if "stopped from the setup screens and stays stopped" in c]
        assert len(held) == 1 and "systemctl enable --now hal0-agent@hermes" in held[0]
        assert not box.warns()


def test_record_and_agent_enabled_by_hand_then_a_run_without_screens_stops_nothing_and_removes_the_record(
    tmp_path: Path,
) -> None:
    box = _run(
        tmp_path,
        record="agent\ngateway\n",
        gw_state="disabled",
        gw_active=False,
        agent_state="enabled",
    )
    box.ok()
    assert not [c for c in box.sys() if c.startswith("systemctl disable")]
    assert box.unit("agent") == "enabled" and box.running("agent")  # left running
    # the gateway this installer disabled is enabled again, and started
    assert box.enables_gateway()
    assert box.unit("gw") == "enabled" and box.running("gw")
    assert box.record() is None and not box.record_path().exists()
    assert any("stop from the setup screens is lifted" in c for c in box.infos())


def test_agent_enabled_by_hand_and_a_gateway_not_in_the_record_is_left_as_it_is(
    tmp_path: Path,
) -> None:
    box = _run(
        tmp_path, record="agent\n", gw_state="disabled", gw_active=False, agent_state="enabled"
    )
    box.ok()
    assert not box.enables_gateway()
    assert box.unit("gw") == "disabled" and not box.running("gw")
    assert box.record() is None


def test_a_confirmed_run_with_the_assistant_on_lifts_the_stop_and_restores_the_gateway(
    tmp_path: Path,
) -> None:
    first = _run(tmp_path, stop="1", skip="1", agent_state="enabled", name="first")
    first.ok()
    assert first.record() == "agent\ngateway\n"
    second = _run(
        tmp_path,
        stop="0",
        record=first.record(),
        gw_state="disabled",
        gw_active=False,
        agent_state="disabled",
        name="second",
    )
    second.ok()
    assert second.enables_gateway()
    assert second.unit("gw") == "enabled" and second.running("gw")
    assert second.record() is None
    assert not [c for c in second.sys() if c.startswith("systemctl disable")]


def test_a_gateway_the_person_disabled_before_the_stop_stays_disabled_after_the_lift(
    tmp_path: Path,
) -> None:
    first = _run(
        tmp_path,
        stop="1",
        skip="1",
        agent_state="enabled",
        gw_state="disabled",
        gw_active=False,
        name="first",
    )
    first.ok()
    second = _run(
        tmp_path,
        stop="0",
        record=first.record(),
        gw_state="disabled",
        gw_active=False,
        name="second",
    )
    second.ok()
    assert not second.enables_gateway()
    assert second.unit("gw") == "disabled"
    assert second.record() is None


@pytest.mark.parametrize("how", ["screens", "by_hand"])
def test_a_gateway_the_person_masked_since_the_stop_is_never_unmasked(
    tmp_path: Path, how: str
) -> None:
    kwargs = {"stop": "0"} if how == "screens" else {"agent_state": "enabled"}
    box = _run(tmp_path, record="agent\ngateway\n", gw_state="masked", gw_active=False, **kwargs)
    box.ok()
    assert box.unit("gw") == "masked"
    assert not box.enables_gateway()
    assert not [c for c in box.sys() if "unmask" in c]
    assert box.record() is None


def test_a_lift_signal_without_a_record_is_a_normal_run(tmp_path: Path) -> None:
    box = _run(tmp_path, stop="0", gw_state="disabled", gw_active=False)
    box.ok()
    assert not box.enables_gateway()
    assert box.record() is None
    assert not [c for c in box.calls if "stays stopped" in c]


def test_a_stop_signal_with_a_record_stays_stopped_and_keeps_the_record(tmp_path: Path) -> None:
    # the screens come again with the assistant still off
    box = _run(
        tmp_path,
        stop="1",
        skip="1",
        record="agent\ngateway\n",
        gw_state="disabled",
        gw_active=False,
        agent_state="disabled",
    )
    box.ok()
    assert not [c for c in box.sys() if c.startswith("systemctl enable")]
    assert not box.warns()
    assert box.state() == "stopped"
    assert box.record() == "agent\ngateway\n"  # not reduced
    again = [c for c in box.infos() if "already stopped and stays stopped" in c]
    assert len(again) == 1
    assert not [c for c in box.infos() if "was stopped and disabled" in c]


def test_a_stop_signal_with_the_agent_enabled_by_hand_stops_it_again(tmp_path: Path) -> None:
    box = _run(
        tmp_path,
        stop="1",
        skip="1",
        record="agent\ngateway\n",
        gw_state="disabled",
        gw_active=False,
        agent_state="enabled",
    )
    box.ok()
    assert box.unit("agent") == "disabled" and not box.running("agent")
    assert box.state() == "stopped"
    assert box.record() == "agent\ngateway\n"
    assert [c for c in box.infos() if "was stopped and disabled" in c]


@pytest.mark.parametrize("text", ["", "bogus\n", "agent\nagent \n", "agent\nroot\n", "agent\n\n"])
def test_a_damaged_record_lifts_nothing_by_itself_and_warns(tmp_path: Path, text: str) -> None:
    box = _run(tmp_path, record=text, gw_state="disabled", gw_active=False, agent_state="disabled")
    box.ok()
    assert not box.writes(), box.calls
    assert box.state() == "stopped" and box.record() == text
    assert any("not a record this installer wrote" in w for w in box.warns())


def test_a_record_without_a_final_newline_still_reads(tmp_path: Path) -> None:
    box = _run(
        tmp_path, record="agent", gw_state="disabled", gw_active=False, agent_state="disabled"
    )
    box.ok()
    assert not box.writes() and box.record() == "agent" and not box.warns()


def test_a_link_at_the_record_path_is_damaged_and_never_followed(tmp_path: Path) -> None:
    box = _run(tmp_path, record_link=True, gw_state="disabled", gw_active=False)
    box.ok()
    assert not box.writes()
    assert any("not a record this installer wrote" in w for w in box.warns())
    assert (box.work / "elsewhere").read_text() == "agent\n"


@pytest.mark.parametrize("how", ["screens", "by_hand"])
def test_a_damaged_record_is_lifted_by_a_person_but_enables_no_gateway(
    tmp_path: Path, how: str
) -> None:
    kwargs = {"stop": "0"} if how == "screens" else {"agent_state": "enabled"}
    box = _run(tmp_path, record="bogus\n", gw_state="disabled", gw_active=False, **kwargs)
    box.ok()
    assert not box.enables_gateway()
    assert box.record() is None


def test_a_stop_signal_leaves_a_damaged_record_as_it_is(tmp_path: Path) -> None:
    box = _run(tmp_path, stop="1", skip="1", record="bogus\n", agent_state="enabled")
    box.ok()
    assert box.record() == "bogus\n"
    assert box.state() == "stopped"
    assert any("not a record this installer wrote" in w for w in box.warns())


# ── structure ─────────────────────────────────────────────────────────────


def test_the_step_sits_after_the_terminal_apply_and_before_the_enable_block() -> None:
    apply_call = TEXT.index('"${HAL0_BIN}" agent reprovision hermes')
    stop = TEXT.index('"${HAL0_HERMES_STOP:-}" == "1"')
    enable = TEXT.index("systemctl enable --now hal0-agent@hermes.service")
    assert apply_call < stop < enable
    assert TEXT.count("HAL0_HERMES_STOP") >= 1


def test_the_stop_is_unreachable_with_dev_or_no_start() -> None:
    # The whole Hermes step lives in the else branch of the dev / --no-start test:
    # the stop, the record read, the hold and the lift.
    gate = TEXT.index('if [[ "${DEV_MODE}" -eq 1 || "${NO_START}" -eq 1 ]]; then')
    branch = TEXT.index("\nelse\n", gate)
    for needle in (
        '"${HAL0_HERMES_STOP:-}" == "1"',
        "hal0_hermes_stop_record_path",
        'rm -f "${_hermes_record}"',
    ):
        assert gate < branch < TEXT.index(needle), needle
    assert "\nfi\n" not in TEXT[gate:branch]


def test_nothing_outside_install_sh_reads_the_record_or_stops_the_agent_unit() -> None:
    hits = []
    for base in ("src", "installer"):
        for path in (ROOT / base).rglob("*"):
            if path.suffix not in {".py", ".sh", ""} or not path.is_file():
                continue
            if path.name in {"install.sh", "facts.sh", "uninstall.sh"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "hermes-stopped-by-setup" in text:
                hits.append(str(path.relative_to(ROOT)))
    assert hits == []


def test_usage_text_says_what_turning_the_assistant_off_does() -> None:
    usage = " ".join(
        TEXT[TEXT.index("--help|-h)") : TEXT.index("exit 0", TEXT.index("--help|-h)"))].split()
    ).replace("\\`", "`")
    sentence = (
        "Turning the assistant off on the setup screens, on a box that already has it, "
        "stops it; it stays installed and stays stopped, also across upgrades, until it "
        "is turned on there again or started by hand (`systemctl enable --now "
        "hal0-agent@hermes`, and `hermes-gateway` for the messaging bridge)."
    )
    assert sentence in usage
    assert "A skip flag or variable alone" in usage
    assert "does not stop an installed assistant" in usage


def test_the_real_safe_writer_makes_the_record_0644(tmp_path: Path) -> None:
    helper = ROOT / "src" / "hal0" / "security" / "nofollow.py"
    target = tmp_path / "agents" / _RECORD
    target.parent.mkdir()
    proc = subprocess.run(
        ["python3", "-I", str(helper), "write", "--mode", "0644", str(target)],
        input="agent\ngateway\n",
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert target.read_text() == "agent\ngateway\n"
    assert (target.stat().st_mode & 0o777) == 0o644
