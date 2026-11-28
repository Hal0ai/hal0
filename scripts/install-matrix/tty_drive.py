#!/usr/bin/env python3
"""Run a command under a pseudo-terminal and type scripted answers into it.

The hal0 installer asks some questions on /dev/tty even when its stdin is a
pipe (`curl ... | bash`). To test that honestly the command gets the pty as
its CONTROLLING terminal, so opening /dev/tty in the child or any descendant
reaches this driver. The driver's own stdin is never forwarded.

Usage:
  python3 -I tty_drive.py --answers FILE --transcript FILE --result FILE \
      [--timeout 5400] [--idle-timeout 300] -- <command...>

Answers file: one rule per line, `<regular expression><TAB><reply>`. `\\n` in
the reply is Enter (and `\\t` a tab). Lines starting `#` and blank lines are
ignored. Rules are tried in order against the output received since the last
reply, with ANSI escapes removed; each rule fires at most once.

Result JSON: {exit_code, questions_answered, timed_out, idle_timed_out,
unanswered_tail}. unanswered_tail is the last 40 transcript lines when a
timeout fired, else "". The transcript is the raw terminal output and may
hold a secret, so it is created mode 0600.

Exit status: the command's own (128+N if killed by signal N), or 124 on
either timeout. Standard library only; Python 3.10+.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import pty
import re
import select
import signal
import sys
import time

TIMEOUT_EXIT = 124
KILL_GRACE = 5.0
TAIL_LINES = 40

_ANSI = re.compile(
    rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL / ST
    rb"|\x1b\[[0-?]*[ -/]*[@-~]"  # CSI
    rb"|\x1b[@-Z\\-_]"  # other two-byte escapes
)


class _Terminated(Exception):
    """The driver itself was asked to stop (SIGTERM/SIGHUP)."""


def load_rules(path: str) -> list[tuple[re.Pattern[str], bytes]]:
    rules = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\r\n")
            if not line.strip() or line.startswith("#"):
                continue
            if "\t" not in line:
                raise SystemExit(f"tty_drive: rule without a TAB: {line!r}")
            rx, reply = line.split("\t", 1)
            reply = reply.replace("\\n", "\n").replace("\\t", "\t")
            rules.append((re.compile(rx), reply.encode()))
    return rules


def strip_ansi(data: bytes) -> str:
    return _ANSI.sub(b"", data).decode("utf-8", errors="replace")


def _killpg(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _reap(pid: int, block_for: float) -> int | None:
    """Return the wait status once the child is gone, else None."""
    deadline = time.monotonic() + block_for
    while True:
        try:
            done, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return 0
        if done:
            return status
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


def _decode_status(status: int) -> int:
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return os.WEXITSTATUS(status)


def _tail(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(text.splitlines()[-TAIL_LINES:])


def _write_result(path: str, result: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(result, fh)
        fh.write("\n")


def run(args: argparse.Namespace) -> int:
    rules = load_rules(args.answers)
    fired = [False] * len(rules)
    result = {
        "exit_code": 1,
        "questions_answered": 0,
        "timed_out": False,
        "idle_timed_out": False,
        "unanswered_tail": "",
    }
    # Mode is set at creation, never chmod'ed after writing.
    tfd = os.open(args.transcript, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    transcript = os.fdopen(tfd, "wb", buffering=0)

    def _on_term(signum: int, _frame: object) -> None:
        raise _Terminated

    old = {s: signal.signal(s, _on_term) for s in (signal.SIGTERM, signal.SIGHUP)}

    pid, master = pty.fork()
    if pid == 0:  # child: pty is already the controlling terminal
        try:
            os.execvp(args.command[0], args.command)
        except OSError as exc:
            os.write(2, f"tty_drive: cannot run {args.command[0]}: {exc}\n".encode())
            os._exit(127)

    status: int | None = None
    try:
        start = last_output = time.monotonic()
        pending = b""
        child_gone_at: float | None = None
        while True:
            now = time.monotonic()
            if now - start >= args.timeout:
                result["timed_out"] = True
                break
            if now - last_output >= args.idle_timeout:
                result["idle_timed_out"] = True
                break
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    chunk = b""
                if not chunk:
                    break  # every holder of the pty closed it
                last_output = time.monotonic()
                transcript.write(chunk)
                pending += chunk
                text = strip_ansi(pending)
                for i, (rx, reply) in enumerate(rules):
                    if not fired[i] and rx.search(text):
                        fired[i] = True
                        os.write(master, reply)
                        result["questions_answered"] += 1
                        pending = b""
                        break
            else:
                status = _reap(pid, 0)
                if status is not None:
                    # Child gone but a descendant may still hold the pty:
                    # give it a moment to flush, then stop waiting.
                    child_gone_at = child_gone_at or time.monotonic()
                    if time.monotonic() - child_gone_at > 1.0:
                        break
    except _Terminated:
        result["timed_out"] = True
    finally:
        # Whatever happened, no process of the command's group outlives us.
        if status is None:
            status = _reap(pid, 0)
        if status is None:
            _killpg(pid, signal.SIGTERM)
            status = _reap(pid, KILL_GRACE)
        _killpg(pid, signal.SIGKILL)
        if status is None:
            status = _reap(pid, KILL_GRACE)
        try:
            os.close(master)
        except OSError:
            pass
        transcript.close()
        for s, h in old.items():
            signal.signal(s, h)

    timed_out = result["timed_out"] or result["idle_timed_out"]
    if timed_out:
        result["exit_code"] = TIMEOUT_EXIT
        result["unanswered_tail"] = _tail(args.transcript)
    else:
        result["exit_code"] = _decode_status(status) if status is not None else 1
    _write_result(args.result, result)
    return result["exit_code"]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--answers", required=True)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--timeout", type=float, default=5400)
    ap.add_argument("--idle-timeout", type=float, default=300)
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        ap.error("no command given after --")
    return run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
