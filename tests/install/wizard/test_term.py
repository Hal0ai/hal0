from __future__ import annotations

import os
import pathlib
import signal
import termios
import threading
import time
from collections.abc import Iterator
from types import FrameType

import pytest

from hal0.install.wizard.session import Interrupted
from hal0.install.wizard.term import (
    Terminal,
    TerminalUnavailable,
    can_open,
    decode_keys,
    in_foreground,
)

from .conftest import read_all, set_winsize


@pytest.fixture(autouse=True)
def _deadline() -> Iterator[None]:
    """No terminal test may hang the suite."""

    def expired(signum: int, frame: FrameType | None) -> None:
        raise TimeoutError("terminal test exceeded its deadline")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(15)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _attrs(path: str) -> list[object]:
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
    try:
        return termios.tcgetattr(fd)
    finally:
        os.close(fd)


def _write_later(master: int, data: bytes, delay: float = 0.3) -> threading.Thread:
    def go() -> None:
        time.sleep(delay)
        os.write(master, data)

    thread = threading.Thread(target=go)
    thread.start()
    return thread


@pytest.mark.parametrize(
    "data, keys, tail",
    [
        (b"\x1b[A", ["UP"], b""),
        (b"\x1bOB", ["DOWN"], b""),
        (b"\r", ["ENTER"], b""),
        (b"\n", ["ENTER"], b""),
        (b" ", ["SPACE"], b""),
        (b"\x7f", ["BACKSPACE"], b""),
        (b"\x08", ["BACKSPACE"], b""),
        (b"\x03", ["INTERRUPT"], b""),
        (b"\x04", ["EOF"], b""),
        (b"\x01", [], b""),
        (b"q", ["q"], b""),
        (b"\x1b[A\x1b[A\x1b[B", ["UP", "UP", "DOWN"], b""),
        (b"\x1b[", [], b"\x1b["),
        (b"\x1b", [], b"\x1b"),
        (b"\x1b[1;5C", [], b""),
        (b"\x1b\x1b[A", ["UP"], b""),
        (b"\x1b\x1bOB", ["DOWN"], b""),
        (b"\x1bO\x1b[A", ["UP"], b""),
        (b"\x1b[C\x1b[D\x1bOP", [], b""),
        (b"/data/models", list("/data/models"), b""),
        ("é".encode(), ["é"], b""),
        ("é".encode()[:1], [], "é".encode()[:1]),
        (b"\xff", [], b""),
        (b"a\xffb", ["a", "b"], b""),
    ],
)
def test_decode_keys(data: bytes, keys: list[str], tail: bytes) -> None:
    assert decode_keys(data) == (keys, tail)


def test_raw_mode_is_restored_on_exit_and_on_error(pty_pair: tuple[int, str]) -> None:
    _, slave_path = pty_pair
    before = _attrs(slave_path)
    with pytest.raises(RuntimeError), Terminal(slave_path, raw=True):
        assert _attrs(slave_path) != before
        raise RuntimeError("boom")
    assert _attrs(slave_path) == before


def test_raw_mode_is_restored_on_keyboard_interrupt(pty_pair: tuple[int, str]) -> None:
    master, slave_path = pty_pair
    before = _attrs(slave_path)
    with pytest.raises(KeyboardInterrupt), Terminal(slave_path, raw=True):
        raise KeyboardInterrupt
    assert _attrs(slave_path) == before
    assert b"\x1b[?25h" in read_all(master)


def test_cursor_is_hidden_then_shown_in_raw_mode(pty_pair: tuple[int, str]) -> None:
    master, slave_path = pty_pair
    with Terminal(slave_path, raw=True):
        pass
    out = read_all(master)
    assert out.index(b"\x1b[?25l") < out.index(b"\x1b[?25h")


def test_read_key_joins_a_sequence_split_across_two_reads(pty_pair: tuple[int, str]) -> None:
    master, slave_path = pty_pair
    with Terminal(slave_path, raw=True) as t:
        os.write(master, b"\x1b")
        os.write(master, b"[B")
        assert t.read_key() == "DOWN"


def test_read_key_waits_for_the_second_half_of_a_sequence(pty_pair: tuple[int, str]) -> None:
    master, slave_path = pty_pair
    with Terminal(slave_path, raw=True) as t:
        os.write(master, b"\x1b")
        writer = _write_later(master, b"[B", delay=0.02)
        try:
            assert t.read_key() == "DOWN"
        finally:
            writer.join()


def test_read_key_returns_every_key_of_a_burst(pty_pair: tuple[int, str]) -> None:
    master, slave_path = pty_pair
    with Terminal(slave_path, raw=True) as t:
        os.write(master, b"\x1b[A\x1b[Bx")
        assert [t.read_key() for _ in range(3)] == ["UP", "DOWN", "x"]


def test_a_lone_escape_is_ignored_after_the_wait(pty_pair: tuple[int, str]) -> None:
    master, slave_path = pty_pair
    results: list[str] = []
    with Terminal(slave_path, raw=True) as t:
        reader = threading.Thread(target=lambda: results.append(t.read_key()))
        os.write(master, b"\x1b")
        reader.start()
        try:
            time.sleep(0.3)  # long past the escape wait: the ESC has been dropped
            assert results == []
            os.write(master, b"z")
        finally:
            reader.join(timeout=5)
        assert results == ["z"]


def test_ctrl_c_is_a_key_not_a_signal(pty_pair: tuple[int, str]) -> None:
    master, slave_path = pty_pair
    with Terminal(slave_path, raw=True) as t:
        os.write(master, b"\x03")
        assert t.read_key() == "INTERRUPT"


def test_end_of_input_reads_as_eof(pty_pair: tuple[int, str]) -> None:
    master, slave_path = pty_pair
    with Terminal(slave_path, raw=True) as t:
        os.close(master)
        assert t.read_key() == "EOF"
        assert t.read_key() == "EOF"


def test_draw_moves_up_by_what_it_drew_last(pty_pair: tuple[int, str]) -> None:
    master, slave_path = pty_pair
    with Terminal(slave_path, raw=True) as t:
        t.draw(["a", "b", "c"])
        t.draw(["a", "b"])
    out = read_all(master)
    assert b"\x1b[3A" in out
    assert out.count(b"\x1b[2K") >= 5
    assert b"a\r\n" in out


def test_draw_after_a_shorter_draw_climbs_the_shorter_height(pty_pair: tuple[int, str]) -> None:
    master, slave_path = pty_pair
    with Terminal(slave_path, raw=True) as t:
        t.draw(["a", "b", "c"])
        t.draw(["a"])
        t.draw(["a"])
    assert b"\x1b[1A" in read_all(master)


def test_width_follows_a_resize(pty_pair: tuple[int, str]) -> None:
    master, slave_path = pty_pair
    with Terminal(slave_path, raw=True) as t:
        set_winsize(master, rows=24, cols=37)
        assert t.width() == 37


def test_width_falls_back_to_eighty(pty_pair: tuple[int, str]) -> None:
    _, slave_path = pty_pair
    with Terminal(slave_path, raw=True) as t:
        assert t.width() == 80


def test_no_terminal_is_reported(tmp_path: pathlib.Path) -> None:
    absent = str(tmp_path / "absent")
    assert can_open(absent) is False
    with pytest.raises(TerminalUnavailable):
        Terminal(absent, raw=True).__enter__()


@pytest.mark.parametrize("raw", [True, False])
def test_a_file_is_not_a_terminal(tmp_path: pathlib.Path, raw: bool) -> None:
    plain = tmp_path / "file"
    plain.write_bytes(b"IMPORTANT DATA\n")
    assert can_open(str(plain)) is False
    with pytest.raises(TerminalUnavailable):
        Terminal(str(plain), raw=raw).__enter__()
    assert plain.read_bytes() == b"IMPORTANT DATA\n"


def test_can_open_a_terminal(pty_pair: tuple[int, str]) -> None:
    _, slave_path = pty_pair
    assert can_open(slave_path) is True


def test_a_lost_terminal_never_raises_out_of_a_write(pty_pair: tuple[int, str]) -> None:
    master, slave_path = pty_pair
    with Terminal(slave_path, raw=True) as t:
        os.close(master)
        t.write("hello")
        t.draw(["a", "b"])
    t.write("after exit")


def test_plain_form_leaves_the_mode_alone_and_draws_no_escape(pty_pair: tuple[int, str]) -> None:
    master, slave_path = pty_pair
    before = _attrs(slave_path)
    with Terminal(slave_path, raw=False) as t:
        assert _attrs(slave_path) == before
        t.draw(["one", "two"])
        t.draw(["three"])
    assert _attrs(slave_path) == before
    out = read_all(master)
    assert b"\x1b" not in out
    assert out.replace(b"\r\n", b"\n") == b"one\ntwo\n\nthree\n\n"


def test_plain_read_line_returns_a_line(pty_pair: tuple[int, str]) -> None:
    master, slave_path = pty_pair
    with Terminal(slave_path, raw=False) as t:
        os.write(master, b"hello world\n")
        assert t.read_line("> ") == "hello world"


def test_plain_read_line_returns_none_at_end_of_input(pty_pair: tuple[int, str]) -> None:
    master, slave_path = pty_pair
    with Terminal(slave_path, raw=False) as t:
        os.close(master)
        assert t.read_line("> ") is None


def test_read_line_without_echo_hides_what_is_typed_and_restores_echo(
    pty_pair: tuple[int, str],
) -> None:
    master, slave_path = pty_pair
    before = _attrs(slave_path)
    with Terminal(slave_path, raw=False) as t:
        writer = _write_later(master, b"s3cret\n")
        try:
            assert t.read_line("token: ", echo=False) == "s3cret"
        finally:
            writer.join()
        assert _attrs(slave_path) == before
    out = read_all(master)
    assert b"token: " in out
    assert b"s3cret" not in out


def test_echo_is_restored_when_the_read_raises(
    pty_pair: tuple[int, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, slave_path = pty_pair
    before = _attrs(slave_path)

    def boom(self: Terminal) -> str | None:
        raise RuntimeError("boom")

    monkeypatch.setattr(Terminal, "_take_line", boom)
    with Terminal(slave_path, raw=False) as t:
        with pytest.raises(RuntimeError):
            t.read_line("token: ", echo=False)
        assert _attrs(slave_path) == before


def test_read_line_is_for_the_plain_form(pty_pair: tuple[int, str]) -> None:
    _, slave_path = pty_pair
    with Terminal(slave_path, raw=True) as t, pytest.raises(RuntimeError, match="plain form"):
        t.read_line("> ")


def test_a_block_as_tall_as_the_window_clears_instead_of_climbing(
    pty_pair: tuple[int, str],
) -> None:
    master, slave_path = pty_pair
    set_winsize(master, rows=5, cols=40)
    with Terminal(slave_path, raw=True) as t:
        t.draw([f"line {n}" for n in range(8)])
        t.draw([f"line {n}" for n in range(8)])
    out = read_all(master)
    assert b"\x1b[2J" in out
    assert b"\x1b[8A" not in out


def _group(value: int):  # type: ignore[no-untyped-def]
    def tcgetpgrp(fd: int) -> int:
        return value

    return tcgetpgrp


def _not_controlling(fd: int) -> int:
    raise OSError(25, "Inappropriate ioctl for device")


def test_a_terminal_in_the_foreground_is_usable() -> None:
    assert in_foreground(0, tcgetpgrp=_group(41), getpgrp=lambda: 41) is True


def test_a_terminal_whose_foreground_is_another_job_is_not_usable() -> None:
    assert in_foreground(0, tcgetpgrp=_group(42), getpgrp=lambda: 41) is False


def test_a_terminal_this_process_does_not_control_is_usable() -> None:
    assert in_foreground(0, tcgetpgrp=_not_controlling, getpgrp=lambda: 41) is True


def test_a_background_job_neither_opens_nor_touches_the_terminal(
    pty_pair: tuple[int, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    master, slave_path = pty_pair
    before = _attrs(slave_path)
    monkeypatch.setattr(os, "tcgetpgrp", _group(os.getpgrp() + 1))
    assert can_open(slave_path) is False
    with pytest.raises(TerminalUnavailable):
        Terminal(slave_path, raw=True).__enter__()
    assert _attrs(slave_path) == before
    assert read_all(master) == b""


class _Boom(Exception):
    pass


def test_a_signal_while_closing_still_restores_the_terminal(
    pty_pair: tuple[int, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, slave_path = pty_pair
    before = _attrs(slave_path)
    real = termios.tcsetattr
    armed: list[bool] = []

    def signal_first(fd: int, when: int, attrs: list[object]) -> None:
        if armed:
            armed.clear()
            # Aimed at this thread, so it stays pending while the signals are held.
            signal.pthread_kill(threading.get_ident(), signal.SIGTERM)
        real(fd, when, attrs)

    def raise_boom(signum: int, frame: FrameType | None) -> None:
        raise _Boom

    previous = signal.signal(signal.SIGTERM, raise_boom)
    caught: list[_Boom] = []
    try:
        monkeypatch.setattr(termios, "tcsetattr", signal_first)
        try:
            with Terminal(slave_path, raw=True):
                armed.append(True)
            # Unblocking a pending signal runs its handler before pthread_sigmask returns,
            # so _Boom has already left the block above; nothing here waits for it.
        except _Boom as exc:
            caught.append(exc)
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert caught and not armed
    assert _attrs(slave_path) == before


@pytest.mark.parametrize("missing", [True, False])
def test_closing_carries_on_without_a_signal_mask(
    pty_pair: tuple[int, str], monkeypatch: pytest.MonkeyPatch, missing: bool
) -> None:
    _, slave_path = pty_pair
    before = _attrs(slave_path)

    def refuse(how: int, mask: object) -> set[signal.Signals]:
        raise OSError("no signal mask here")

    if missing:
        monkeypatch.delattr(signal, "pthread_sigmask")
    else:
        monkeypatch.setattr(signal, "pthread_sigmask", refuse)
    with Terminal(slave_path, raw=True):
        assert _attrs(slave_path) != before
    assert _attrs(slave_path) == before


def test_a_failing_mask_call_still_restores_the_terminal_and_the_mask(
    pty_pair: tuple[int, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, slave_path = pty_pair
    before = _attrs(slave_path)
    mask_before = signal.pthread_sigmask(signal.SIG_BLOCK, [])
    real = signal.pthread_sigmask

    def blocking_fails(how: int, mask: object) -> set[signal.Signals]:
        result = real(how, mask)  # type: ignore[arg-type]
        if how == signal.SIG_BLOCK and mask:
            # The block took effect, then something failed before the old mask was
            # stored: the case that used to leave the signals blocked for good.
            raise Interrupted
        return result

    monkeypatch.setattr(signal, "pthread_sigmask", blocking_fails)
    with pytest.raises(Interrupted), Terminal(slave_path, raw=True):
        assert _attrs(slave_path) != before
    monkeypatch.undo()
    assert _attrs(slave_path) == before
    assert signal.pthread_sigmask(signal.SIG_BLOCK, []) == mask_before
