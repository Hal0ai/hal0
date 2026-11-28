"""install.sh applies the terminal-tool answer on a box that already has Hermes.

`hal0 agent install hermes` runs only when the Hermes venv is missing, so on a
re-run (`--reconfigure`) the answer in HAL0_HERMES_TERMINAL would be read by
nothing. The apply block runs `hal0 agent reprovision hermes` there. These
tests cut that block out of install.sh and run it under real bash with a stub
HAL0_BIN that records its arguments and the answer it saw.
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

_START = "    _hermes_was_provisioned=0\n"
_END = "    # Enable the unit + gateway for both fresh"
_VENV = "/var/lib/hal0/venvs/hermes/bin/hermes"


def _kill_group(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)


def _run_group(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    with subprocess.Popen(argv, start_new_session=True, **kwargs) as proc:  # type: ignore[call-overload]
        try:
            out, err = proc.communicate(timeout=20)
        except BaseException:
            _kill_group(proc.pid)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.communicate(timeout=5)
            raise
        _kill_group(proc.pid)
        return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def _region() -> str:
    head = TEXT.index(_START)
    return TEXT[head : TEXT.index(_END, head)]


def _run(
    tmp_path: Path,
    *,
    venv: bool = True,
    unit: bool = True,
    answer: str | None = None,
    skip: str | None = None,
    rc: int = 0,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the region; return the process and the stub's recorded lines."""
    fake_venv = tmp_path / "hermes"
    if venv:
        fake_venv.write_text("#!/bin/sh\n", encoding="utf-8")
        fake_venv.chmod(0o755)
    unit_file = tmp_path / "agent.service"
    if unit:
        unit_file.write_text("", encoding="utf-8")
    log = tmp_path / "calls.log"
    hal0 = tmp_path / "hal0"
    hal0.write_text(
        "#!/bin/bash\n"
        f'printf "%s|TERMINAL=%s\\n" "$*" "${{HAL0_HERMES_TERMINAL-<unset>}}" >>"{log}"\n'
        f"exit {rc}\n",
        encoding="utf-8",
    )
    hal0.chmod(0o755)
    region = _region()
    assert _VENV in region
    script = (
        "set -euo pipefail\n"
        "info() { printf 'INFO: %s\\n' \"$*\"; }\n"
        "warn() { printf 'WARN: %s\\n' \"$*\"; }\n"
        "preflight_git() { return 0; }\n"
        "_hermes_install_args=()\n"
        f'AGENT_UNIT_DST="{unit_file}"\n'
        f'HAL0_BIN="{hal0}"\n' + region.replace(_VENV, str(fake_venv)) + "\necho REGION_DONE\n"
    )
    env = {"PATH": "/usr/bin:/bin"}
    if answer is not None:
        env["HAL0_HERMES_TERMINAL"] = answer
    if skip is not None:
        env["HAL0_SKIP_HERMES"] = skip
    proc = _run_group(
        ["bash", "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return proc, calls


@pytest.mark.parametrize("answer", ["0", "1"])
def test_an_answer_on_a_box_with_hermes_runs_the_apply_command(tmp_path: Path, answer: str) -> None:
    proc, calls = _run(tmp_path, answer=answer)
    assert proc.returncode == 0 and "REGION_DONE" in proc.stdout, proc.stdout + proc.stderr
    assert calls == [f"agent reprovision hermes|TERMINAL={answer}"]
    assert "WARN" not in proc.stdout


def test_no_answer_runs_nothing(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, answer=None)
    assert proc.returncode == 0 and "REGION_DONE" in proc.stdout, proc.stdout + proc.stderr
    assert calls == []


@pytest.mark.parametrize("answer", ["", "maybe", "  "])
def test_an_empty_or_unreadable_answer_runs_nothing(tmp_path: Path, answer: str) -> None:
    proc, calls = _run(tmp_path, answer=answer)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == []


def test_skip_with_an_on_answer_runs_nothing(tmp_path: Path) -> None:
    # Turning the tool ON is not applied when the assistant is skipped.
    proc, calls = _run(tmp_path, answer="1", skip="1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == []


def test_skip_with_an_off_answer_still_applies_it(tmp_path: Path) -> None:
    # A setup-screens run with the assistant off exports both variables; an
    # agent installed earlier must not keep a root-equivalent tool the person
    # turned off.
    proc, calls = _run(tmp_path, answer="0", skip="1")
    assert proc.returncode == 0 and "REGION_DONE" in proc.stdout, proc.stdout + proc.stderr
    assert calls == ["agent reprovision hermes|TERMINAL=0"]
    assert "WARN" not in proc.stdout


@pytest.mark.parametrize("answer", ["0", "false", "no", "off", "n", " OFF ", "No"])
def test_every_off_word_is_honoured_under_skip(tmp_path: Path, answer: str) -> None:
    proc, calls = _run(tmp_path, answer=answer, skip="1")
    assert proc.returncode == 0 and "REGION_DONE" in proc.stdout, proc.stdout + proc.stderr
    assert calls == [f"agent reprovision hermes|TERMINAL={answer}"]


@pytest.mark.parametrize("answer", ["1", "true", "yes", "on", "y", "maybe", ""])
def test_no_other_word_is_applied_under_skip(tmp_path: Path, answer: str) -> None:
    proc, calls = _run(tmp_path, answer=answer, skip="1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == []


@pytest.mark.parametrize("answer", ["0", "1"])
def test_skip_unset_applies_both_answers_as_before(tmp_path: Path, answer: str) -> None:
    _proc, calls = _run(tmp_path, answer=answer)
    assert calls == [f"agent reprovision hermes|TERMINAL={answer}"]


def test_skip_with_off_on_a_box_without_hermes_runs_nothing(tmp_path: Path) -> None:
    for kwargs in ({"venv": False}, {"unit": False}):
        sub = tmp_path / ("v" if "venv" in kwargs else "u")
        sub.mkdir()
        proc, calls = _run(sub, answer="0", skip="1", **kwargs)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert calls == []


def test_a_box_without_the_agent_unit_runs_nothing(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, answer="0", unit=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == []


def test_a_box_without_the_venv_takes_the_install_path_not_the_apply(tmp_path: Path) -> None:
    # The install command reads the answer itself; the apply block must not run
    # a second provisioning over the one that just happened.
    proc, calls = _run(tmp_path, venv=False, answer="1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == ["agent install hermes|TERMINAL=1"]


def test_a_failing_apply_command_warns_and_the_run_continues(tmp_path: Path) -> None:
    proc, calls = _run(tmp_path, answer="0", rc=1)
    assert proc.returncode == 0 and "REGION_DONE" in proc.stdout, proc.stdout + proc.stderr
    assert calls == ["agent reprovision hermes|TERMINAL=0"]
    warns = [ln for ln in proc.stdout.splitlines() if ln.startswith("WARN:")]
    text = " ".join(warns)
    assert "may not match what was chosen" in text
    assert "hal0 agent install hermes --no-terminal-tool" in text
