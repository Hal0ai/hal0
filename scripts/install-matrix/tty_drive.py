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
import fcntl
import json
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import time

TIMEOUT_EXIT = 124
KILL_GRACE = 5.0
TAIL_LINES = 40
TAIL_BYTES = 256 * 1024
WINDOW_BYTES = 16 * 1024
EOF_GRACE = 3.0
ROWS, COLS = 24, 80

_ANSI = re.compile(
    rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL / ST
    rb"|\x1b\[[0-?]*[ -/]*[@-~]"  # CSI
    rb"|\x1b[@-Z\\-_]"  # other two-byte escapes
)


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
    """Return the wait status once the child is gone, else None.

    Callers keep the first non-None status and never call this again.
    """
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
    """Last TAIL_LINES lines of the transcript, ANSI removed; reads the end only."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - TAIL_BYTES))
            data = fh.read()
    except OSError:
        return ""
    text = strip_ansi(data).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(text.splitlines()[-TAIL_LINES:])


def _create_private(path: str, mode: str) -> object:
    """Create a fresh 0600 file; an existing file (or symlink) is replaced."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(fd, mode, buffering=0 if "b" in mode else -1)


def _write_result(path: str, result: dict) -> None:
    with _create_private(path, "w") as fh:  # type: ignore[union-attr]
        json.dump(result, fh)
        fh.write("\n")


def _child_exec(command: list[str]) -> None:
    """Runs in the forked child: undo what Python changed, then exec."""
    # Python ignores these; exec would inherit that and the command would
    # behave differently from one started in a real terminal.
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
    try:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    except OSError:
        pass
    try:
        os.execvp(command[0], command)
    except OSError as exc:
        os.write(2, f"tty_drive: cannot run {command[0]}: {exc}\n".encode())
        os._exit(127)


def run(args: argparse.Namespace) -> int:
    rules = load_rules(args.answers)
    fired = [False] * len(rules)
    result: dict = {
        "exit_code": 1,
        "questions_answered": 0,
        "timed_out": False,
        "idle_timed_out": False,
        "unanswered_tail": "",
    }
    stop_signal = 0

    def _on_signal(signum: int, _frame: object) -> None:
        nonlocal stop_signal
        stop_signal = signum

    old = {s: signal.signal(s, _on_signal) for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
    transcript = _create_private(args.transcript, "wb")
    master = -1
    pid = -1
    status: int | None = None
    try:
        pid, master = pty.fork()
        if pid == 0:  # child: the pty is already its controlling terminal
            _child_exec(args.command)
        start = last_output = time.monotonic()
        pending = b""
        child_gone_at: float | None = None
        eof = False
        while not stop_signal:
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
                    eof = True  # every holder of the pty closed it
                    break
                last_output = time.monotonic()
                transcript.write(chunk)  # type: ignore[attr-defined]
                if all(fired):
                    continue
                # Only a trailing window can matter to a prompt; keeping it
                # bounded keeps the per-chunk cost constant.
                pending = (pending + chunk)[-WINDOW_BYTES:]
                text = strip_ansi(pending)
                for i, (rx, reply) in enumerate(rules):
                    if not fired[i] and rx.search(text):
                        fired[i] = True
                        try:
                            os.write(master, reply)
                        except OSError:
                            pass  # child already gone; EOF follows
                        result["questions_answered"] += 1
                        pending = b""
                        break
            elif status is None:
                status = _reap(pid, 0)
                if status is not None:
                    # Child gone but a descendant may still hold the pty:
                    # give it a moment to flush, then stop waiting.
                    child_gone_at = time.monotonic()
            elif time.monotonic() - (child_gone_at or 0) > 1.0:
                break
        if eof and status is None:
            # The command closed its terminal; let it finish rather than
            # reporting our own SIGTERM as its exit.
            status = _reap(pid, EOF_GRACE)
    finally:
        # Whatever happened, no process of the command's group outlives us.
        if pid > 0:
            if status is None:
                status = _reap(pid, 0)
            if status is None:
                _killpg(pid, signal.SIGTERM)
                status = _reap(pid, KILL_GRACE)
            _killpg(pid, signal.SIGKILL)
            if status is None:
                status = _reap(pid, KILL_GRACE)
        if master >= 0:
            try:
                os.close(master)
            except OSError:
                pass
        transcript.close()  # type: ignore[attr-defined]
        for sig, handler in old.items():
            signal.signal(sig, handler)
        if result["timed_out"] or result["idle_timed_out"]:
            result["exit_code"] = TIMEOUT_EXIT
            result["unanswered_tail"] = _tail(args.transcript)
        elif stop_signal:
            result["exit_code"] = 128 + stop_signal
            result["unanswered_tail"] = _tail(args.transcript)
        elif status is not None:
            result["exit_code"] = _decode_status(status)
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
