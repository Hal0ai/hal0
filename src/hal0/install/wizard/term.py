"""The only module that touches a terminal.

It opens a terminal device by path (normally ``/dev/tty``, so the wizard works
when standard input is a pipe), reads keys in raw mode, redraws a block of
lines in place and offers a plain line reader. The terminal is put back the way
it was on every way out, and a terminal that has gone away never raises out of
a write. Signal handling is the caller's job.
"""

from __future__ import annotations

import contextlib
import os
import select
import termios
import tty
from collections.abc import Sequence
from types import TracebackType
from typing import Any, Self

_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"
_CLEAR_ROW = "\x1b[2K"
_ESCAPE_WAIT = 0.05  # seconds to wait for the rest of an escape sequence
_FALLBACK_WIDTH = 80
_FALLBACK_HEIGHT = 24

_ARROWS = {ord("A"): "UP", ord("B"): "DOWN"}


class TerminalUnavailable(RuntimeError):
    """The terminal device cannot be opened, or is not a terminal."""


def can_open(path: str = "/dev/tty") -> bool:
    try:
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return False
    try:
        return os.isatty(fd)
    finally:
        os.close(fd)


def _utf8_length(lead: int) -> int:
    if 0xC2 <= lead <= 0xDF:
        return 2
    if 0xE0 <= lead <= 0xEF:
        return 3
    if 0xF0 <= lead <= 0xF4:
        return 4
    return 0


def _escape(data: bytes, i: int) -> tuple[str | None, int] | None:
    """Decode the escape sequence at ``data[i]``: (key or None, next index).

    Returns None when the sequence is incomplete.
    """
    if i + 1 >= len(data):
        return None
    kind = data[i + 1]
    if kind < 0x20:  # ESC again or another control byte: only this ESC is consumed
        return None, i + 1
    if kind == ord("["):
        j = i + 2
        while j < len(data) and 0x20 <= data[j] <= 0x3F:  # parameters and intermediates
            j += 1
        if j >= len(data):
            return None
        if 0x40 <= data[j] <= 0x7E:
            plain = j == i + 2  # only a bare final byte is an arrow
            return (_ARROWS.get(data[j]) if plain else None), j + 1
        return None, j  # not a valid final byte: drop what was read
    if kind == ord("O"):
        if i + 2 >= len(data):
            return None
        if data[i + 2] < 0x20:  # a new sequence starts: drop only ESC O
            return None, i + 2
        return _ARROWS.get(data[i + 2]), i + 3
    return None, i + 2  # Alt plus a key: swallowed


def decode_keys(data: bytes) -> tuple[list[str], bytes]:
    """Decode key names from raw bytes; the second item is the undecoded tail."""
    keys: list[str] = []
    i = 0
    while i < len(data):
        byte = data[i]
        if byte == 0x1B:
            result = _escape(data, i)
            if result is None:
                return keys, data[i:]
            key, i = result
            if key is not None:
                keys.append(key)
        elif byte in (0x0D, 0x0A):
            keys.append("ENTER")
            i += 1
        elif byte in (0x7F, 0x08):
            keys.append("BACKSPACE")
            i += 1
        elif byte == 0x03:
            keys.append("INTERRUPT")
            i += 1
        elif byte == 0x04:
            keys.append("EOF")
            i += 1
        elif byte == 0x20:
            keys.append("SPACE")
            i += 1
        elif byte < 0x20:
            i += 1
        elif byte < 0x80:
            keys.append(chr(byte))
            i += 1
        else:
            need = _utf8_length(byte)
            if need == 0:
                i += 1
            elif i + need > len(data):
                tail = data[i:]
                if all(0x80 <= b <= 0xBF for b in tail[1:]):
                    return keys, tail
                i += 1
            else:
                try:
                    char = data[i : i + need].decode("utf-8")
                except UnicodeDecodeError:
                    i += 1
                    continue
                if char.isprintable():
                    keys.append(char)
                i += need
    return keys, b""


class Terminal:
    """A terminal device opened by path. ``raw=False`` is the plain form."""

    def __init__(self, path: str = "/dev/tty", *, raw: bool) -> None:
        self._path = path
        self._raw = raw
        self._fd = -1
        self._saved: list[Any] | None = None
        self._rows = 0
        self._keys: list[str] = []
        self._tail = b""
        self._line_rest = b""
        self._cursor_hidden = False

    def __enter__(self) -> Self:
        try:
            self._fd = os.open(self._path, os.O_RDWR | os.O_NOCTTY)
        except OSError as exc:
            raise TerminalUnavailable(f"cannot open {self._path}: {exc}") from exc
        try:
            if not os.isatty(self._fd):
                raise TerminalUnavailable(f"{self._path} is not a terminal")
            if self._raw:
                try:
                    self._saved = termios.tcgetattr(self._fd)
                    tty.setraw(self._fd)
                except (termios.error, OSError) as exc:
                    raise TerminalUnavailable(f"cannot configure {self._path}: {exc}") from exc
                self.write(_HIDE_CURSOR)
                self._cursor_hidden = True
        except BaseException:
            self._close()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._close()

    def _close(self) -> None:
        fd, self._fd = self._fd, -1
        if fd < 0:
            return
        try:
            if self._saved is not None:
                with contextlib.suppress(termios.error, OSError):
                    termios.tcsetattr(fd, termios.TCSADRAIN, self._saved)
        finally:
            try:
                if self._cursor_hidden:
                    self._write_fd(fd, _SHOW_CURSOR)
            finally:
                self._saved = None
                self._cursor_hidden = False
                with contextlib.suppress(OSError):
                    os.close(fd)

    @staticmethod
    def _write_fd(fd: int, text: str) -> None:
        data = text.encode("utf-8", errors="replace")
        try:
            while data:
                written = os.write(fd, data)
                data = data[written:]
        except OSError:
            pass

    def write(self, text: str) -> None:
        self._write_fd(self._fd, text)

    def width(self) -> int:
        try:
            return os.get_terminal_size(self._fd).columns or _FALLBACK_WIDTH
        except OSError:
            return _FALLBACK_WIDTH

    def _height(self) -> int:
        try:
            return os.get_terminal_size(self._fd).lines or _FALLBACK_HEIGHT
        except OSError:
            return _FALLBACK_HEIGHT

    def draw(self, lines: Sequence[str]) -> None:
        if not self._raw:
            self.write("".join(f"{line}\n" for line in lines) + "\n")
            return
        height = self._height()
        if len(lines) >= height or self._rows >= height:
            # Too tall to climb back over: start from a clean screen instead.
            self._rows = len(lines)
            self.write("\x1b[H\x1b[2J" + "".join(f"{line}\r\n" for line in lines))
            return
        out = f"\x1b[{self._rows}A" if self._rows else ""
        out += "".join(f"{_CLEAR_ROW}{line}\r\n" for line in lines)
        extra = self._rows - len(lines)
        if extra > 0:
            out += f"{_CLEAR_ROW}\r\n" * extra + f"\x1b[{extra}A"
        self._rows = len(lines)
        self.write(out)

    def _read_bytes(self) -> bytes:
        try:
            return os.read(self._fd, 1024)
        except OSError:
            return b""

    def read_key(self) -> str:
        while True:
            if self._keys:
                return self._keys.pop(0)
            if self._tail[:1] == b"\x1b":
                try:
                    ready, _, _ = select.select([self._fd], [], [], _ESCAPE_WAIT)
                except (OSError, ValueError):
                    return "EOF"
                if not ready:
                    self._tail = b""  # a lone escape, or a sequence that never finished
                    continue
            data = self._read_bytes()
            if not data:
                return "EOF"
            self._keys, self._tail = decode_keys(self._tail + data)

    def read_line(self, prompt: str, *, echo: bool = True) -> str | None:
        """Read one line (plain form only). None at end of input."""
        if self._raw:
            raise RuntimeError("read_line is for the plain form")
        before: list[Any] | None = None
        try:
            if not echo:
                before = termios.tcgetattr(self._fd)
                attrs = [*before]
                attrs[3] &= ~termios.ECHO
                termios.tcsetattr(self._fd, termios.TCSADRAIN, attrs)
            self.write(prompt)
            line = self._take_line()
            if not echo and line is not None:
                self.write("\n")
            return line
        finally:
            if before is not None:
                with contextlib.suppress(termios.error, OSError):
                    termios.tcsetattr(self._fd, termios.TCSADRAIN, before)

    def _take_line(self) -> str | None:
        buf = self._line_rest
        while b"\n" not in buf:
            data = self._read_bytes()
            if not data:
                self._line_rest = b""
                return buf.decode("utf-8", errors="replace") if buf else None
            buf += data
        line, _, self._line_rest = buf.partition(b"\n")
        return line.decode("utf-8", errors="replace").rstrip("\r")
