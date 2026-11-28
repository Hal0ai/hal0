"""The install-matrix terminal driver (scripts/install-matrix/tty_drive.py).

The installer reads some answers from /dev/tty even when stdin is a pipe, so
the harness runs it under a real pseudo-terminal and types scripted replies.
Everything here drives small bash scripts: no container, no network, no root.
"""

from __future__ import annotations

import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DRIVER = REPO / "scripts" / "install-matrix" / "tty_drive.py"
ANSWERS = REPO / "scripts" / "install-matrix" / "answers"

PROMPT_SCRIPT = 'read -r -p "Continue? [y/N] " a < /dev/tty\necho "got:$a"\n'


def drive(
    tmp_path: Path,
    script: str,
    rules: str,
    *,
    extra: list[str] | None = None,
    command: list[str] | None = None,
    stdin: str | None = None,
    wall: int = 30,
) -> tuple[subprocess.CompletedProcess[str], dict, str]:
    (tmp_path / "script.sh").write_text(script)
    answers = tmp_path / "answers.tsv"
    answers.write_text(rules)
    transcript = tmp_path / "transcript.txt"
    result = tmp_path / "result.json"
    cmd = command or ["bash", "script.sh"]
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            str(DRIVER),
            "--answers",
            str(answers),
            "--transcript",
            str(transcript),
            "--result",
            str(result),
            *(extra or []),
            "--",
            *cmd,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        input=stdin,
        timeout=wall,
        check=False,
    )
    data = json.loads(result.read_text()) if result.exists() else {}
    text = transcript.read_text(errors="replace") if transcript.exists() else ""
    return proc, data, text


def test_answers_prompt_on_dev_tty(tmp_path: Path) -> None:
    proc, res, text = drive(tmp_path, PROMPT_SCRIPT, "Continue\\?\ty\\n\n")
    assert proc.returncode == 0
    assert "got:y" in text
    assert res["questions_answered"] == 1
    assert res["exit_code"] == 0
    assert res["timed_out"] is False
    assert res["idle_timed_out"] is False
    assert res["unanswered_tail"] == ""


def test_stdin_pipe_with_tty_present(tmp_path: Path) -> None:
    proc, res, text = drive(
        tmp_path,
        PROMPT_SCRIPT,
        "Continue\\?\ty\\n\n",
        command=["bash", "-c", "echo hi | bash script.sh"],
    )
    assert proc.returncode == 0
    assert "got:y" in text
    assert res["questions_answered"] == 1


def test_idle_timeout_reports_tail(tmp_path: Path) -> None:
    script = 'read -r -p "Secret handshake? " a < /dev/tty\n'
    proc, res, _ = drive(tmp_path, script, "Continue\\?\ty\\n\n", extra=["--idle-timeout", "2"])
    assert proc.returncode == 124
    assert res["exit_code"] == 124
    assert res["idle_timed_out"] is True
    assert res["timed_out"] is False
    assert "Secret handshake?" in res["unanswered_tail"]
    assert res["questions_answered"] == 0


def test_total_timeout(tmp_path: Path) -> None:
    script = "for i in $(seq 1 30); do printf .; sleep 1; done\n"
    proc, res, _ = drive(tmp_path, script, "", extra=["--timeout", "2"])
    assert proc.returncode == 124
    assert res["timed_out"] is True
    assert res["idle_timed_out"] is False
    assert res["unanswered_tail"] != ""


def test_timeout_kills_whole_process_group(tmp_path: Path) -> None:
    pidfile = tmp_path / "grandchild.pid"
    script = f"sleep 300 &\necho $! > {pidfile}\nwait\n"
    proc, res, _ = drive(tmp_path, script, "", extra=["--timeout", "2"])
    assert proc.returncode == 124
    pid = int(pidfile.read_text())
    # Reaped by init or by us; either way the signal-0 probe must fail.
    # Zombies of an orphan are collected by init; allow a short settle.
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        else:
            try:
                state = Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0]
            except OSError:
                break
            if state == "Z":
                break
            time.sleep(0.1)
    else:
        pytest.fail("grandchild survived the timeout")


def test_rule_fires_once(tmp_path: Path) -> None:
    script = (
        'read -r -p "Continue? [y/N] " a < /dev/tty\nread -r -p "Continue? [y/N] " b < /dev/tty\n'
    )
    proc, res, _ = drive(tmp_path, script, "Continue\\?\ty\\n\n", extra=["--idle-timeout", "2"])
    assert proc.returncode == 124
    assert res["idle_timed_out"] is True
    assert res["questions_answered"] == 1


def test_ansi_stripped_before_match(tmp_path: Path) -> None:
    script = (
        'printf "\\033[1;32mCont\\033[0minue?\\033[0m [y/N] " > /dev/tty\n'
        'read -r a < /dev/tty\necho "got:$a"\n'
    )
    proc, res, text = drive(tmp_path, script, "Continue\\?\ty\\n\n")
    assert proc.returncode == 0
    assert "got:y" in text
    assert res["questions_answered"] == 1


def test_transcript_mode_0600(tmp_path: Path) -> None:
    drive(tmp_path, PROMPT_SCRIPT, "Continue\\?\ty\\n\n")
    mode = stat.S_IMODE((tmp_path / "transcript.txt").stat().st_mode)
    assert mode == 0o600


def test_child_exit_code_passed_through(tmp_path: Path) -> None:
    proc, res, _ = drive(tmp_path, "exit 7\n", "")
    assert proc.returncode == 7
    assert res["exit_code"] == 7


def test_child_killed_by_signal_still_writes_result(tmp_path: Path) -> None:
    proc, res, _ = drive(tmp_path, "kill -9 $$\n", "")
    assert proc.returncode == 128 + 9
    assert res["exit_code"] == 128 + 9


def test_comments_and_blank_lines_ignored(tmp_path: Path) -> None:
    rules = "# a comment\n\nContinue\\?\ty\\n\n"
    proc, res, _ = drive(tmp_path, PROMPT_SCRIPT, rules)
    assert proc.returncode == 0
    assert res["questions_answered"] == 1


def _rules(name: str) -> list[tuple[str, str]]:
    out = []
    for line in (ANSWERS / name).read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            rx, reply = line.split("\t", 1)
            out.append((rx, reply))
    return out


def test_cpu_consent_rule_matches_installer_prompt() -> None:
    rules = _rules("cpu-consent.tsv")
    assert len(rules) == 1
    rx, reply = rules[0]
    assert reply == "y\\n"
    assert re.search(rx, "Continue with a CPU-only install anyway? [y/N] ")


def _installer_prompts() -> list[str]:
    """Every prompt string install.sh can put on the terminal."""
    src = (REPO / "installer" / "install.sh").read_text()
    prompts = re.findall(r'^\s*_tty_read \w+ "([^"]+)"', src, flags=re.M)
    confirm = re.search(r"printf '%s' \"([^\"]+)\" >/dev/tty", src)
    assert confirm, "CPU-only prompt not found in install.sh"
    return [*prompts, confirm.group(1)]


def test_terminal_defaults_cover_every_installer_prompt() -> None:
    prompts = _installer_prompts()
    assert len(prompts) >= 6, prompts
    rules = _rules("terminal-defaults.tsv")
    for prompt in prompts:
        hits = [rx for rx, _ in rules if re.search(rx, prompt)]
        assert len(hits) == 1, (prompt, hits)
    # No rule may be dead: each one answers some installer prompt, except the
    # component checklist drawn by installer/lib/ui.sh.
    ui = (REPO / "installer" / "lib" / "ui.sh").read_text()
    assert "a) defaults  b) bare inference  p) pick  [a]" in ui
    checklist = "a) defaults  b) bare inference  p) pick  [a]: "
    texts = [*prompts, checklist]
    for rx, _ in rules:
        assert any(re.search(rx, t) for t in texts), rx


def test_terminal_defaults_replies() -> None:
    replies = dict(_rules("terminal-defaults.tsv"))
    cpu = [r for rx, r in replies.items() if "CPU-only" in rx]
    assert cpu == ["y\\n"]
    assert [r for rx, r in replies.items() if "CPU-only" not in rx] == ["\\n"] * 6


def test_match_buffer_is_bounded(tmp_path: Path) -> None:
    script = (
        "i=0; while [ $i -lt 40000 ]; do\n"
        '  echo "package install log line $i padding padding padding padding padding padding"\n'
        "  i=$((i+1))\ndone\n"
        'read -r -p "Continue? [y/N] " a < /dev/tty\necho "got:$a"\n'
    )
    started = time.monotonic()
    proc, res, text = drive(tmp_path, script, "Continue\\?\ty\\n\n", wall=60)
    elapsed = time.monotonic() - started
    assert proc.returncode == 0
    assert res["questions_answered"] == 1
    assert "got:y" in text
    assert len(text) > 3_000_000
    assert elapsed < 25


def test_command_runs_with_default_signal_dispositions(tmp_path: Path) -> None:
    proc, res, text = drive(tmp_path, "grep SigIgn /proc/self/status\n", "")
    assert proc.returncode == 0
    mask = int(re.search(r"SigIgn:\s*([0-9a-f]+)", text).group(1), 16)  # type: ignore[union-attr]
    assert not mask & (1 << (signal.SIGPIPE - 1))
    assert not mask & (1 << (signal.SIGXFSZ - 1))


def test_pty_has_a_24x80_window(tmp_path: Path) -> None:
    proc, _, text = drive(tmp_path, "stty size < /dev/tty\n", "")
    assert proc.returncode == 0
    assert "24 80" in text


def test_driver_stdin_is_not_forwarded(tmp_path: Path) -> None:
    script = 'read -r -t 1 a < /dev/tty || true\necho "got:[$a]"\n'
    proc, _, text = drive(tmp_path, script, "", stdin="stray\n")
    assert proc.returncode == 0
    assert "got:[]" in text


def test_existing_output_files_are_recreated_0600(tmp_path: Path) -> None:
    for name in ("transcript.txt", "result.json"):
        old = tmp_path / name
        old.write_text("old")
        old.chmod(0o644)
    proc, _, _ = drive(tmp_path, "exit 0\n", "")
    assert proc.returncode == 0
    for name in ("transcript.txt", "result.json"):
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600


def test_child_that_closes_its_terminal_is_not_reported_as_killed(tmp_path: Path) -> None:
    script = "exec 0<&- 1>&- 2>&-\nsleep 1\nexit 5\n"
    proc, res, _ = drive(tmp_path, script, "")
    assert proc.returncode == 5
    assert res["exit_code"] == 5


def test_result_written_when_driver_is_terminated(tmp_path: Path) -> None:
    (tmp_path / "script.sh").write_text("echo ready\nsleep 60\n")
    (tmp_path / "answers.tsv").write_text("")
    drv = subprocess.Popen(
        [
            sys.executable,
            "-I",
            str(DRIVER),
            "--answers",
            str(tmp_path / "answers.tsv"),
            "--transcript",
            str(tmp_path / "transcript.txt"),
            "--result",
            str(tmp_path / "result.json"),
            "--",
            "bash",
            "script.sh",
        ],
        cwd=tmp_path,
    )
    try:
        for _ in range(100):
            t = tmp_path / "transcript.txt"
            if t.exists() and b"ready" in t.read_bytes():
                break
            time.sleep(0.1)
        else:
            pytest.fail("child never printed")
        drv.send_signal(signal.SIGTERM)
        assert drv.wait(timeout=20) == 128 + signal.SIGTERM
    finally:
        if drv.poll() is None:
            drv.kill()
    res = json.loads((tmp_path / "result.json").read_text())
    assert res["exit_code"] == 128 + signal.SIGTERM


def test_unanswered_tail_is_plain_text(tmp_path: Path) -> None:
    script = 'printf "\\033[1;31mSecret handshake?\\033[0m "\nsleep 30\n'
    _, res, _ = drive(tmp_path, script, "", extra=["--idle-timeout", "2"])
    assert "Secret handshake?" in res["unanswered_tail"]
    assert "\x1b" not in res["unanswered_tail"]
