"""The screens on an open terminal: the draw, read and press loop.

It owns the signal handling for a run, the plain (numbered) form's mapping from
typed lines to keys, and the saved copy of the answers. It never writes the
final document and does not know the command line.
"""

from __future__ import annotations

import contextlib
import re
import signal
import termios
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import FrameType

from . import flow
from .catalog import Catalog
from .document import Document, DocumentError, Override, get, write_document
from .facts import Facts
from .flow_model import Outcome, Row, Screen, State, View
from .render import render
from .term import Terminal, TerminalUnavailable

SAVE_COPY = "_save_copy_to"
_SAVE_FIELD = "save"
_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)

MakeDocument = Callable[[Mapping[str, object]], Document]


class Interrupted(Exception):
    """A signal, Ctrl-C, end of input or a lost terminal ended the run."""


@dataclass(frozen=True)
class Inputs:
    catalog: Catalog
    facts: Facts
    saved: Document | None
    given: Document | None
    over: tuple[Override, ...]
    hal0_version: str
    facts_known: bool = True


@contextlib.contextmanager
def _raising_signals() -> Iterator[None]:
    """Turn SIGINT, SIGTERM and SIGHUP into an exception, then put the old handlers back."""

    def raise_interrupt(signum: int, frame: FrameType | None) -> None:
        raise Interrupted

    previous: dict[signal.Signals, object] = {}
    try:
        for sig in _SIGNALS:
            previous[sig] = signal.signal(sig, raise_interrupt)
    except ValueError:  # not the main thread: no handlers to install
        pass
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)  # type: ignore[arg-type]


def _wants_plain(environ: Mapping[str, str]) -> bool:
    return (
        environ.get("HAL0_PLAIN") == "1"
        or bool(environ.get("NO_COLOR"))
        or environ.get("TERM", "") in ("", "dumb")
    )


def _numbered(rows: Sequence[Row], number: str) -> int | None:
    """The index of the Nth row that is not an information row."""
    chosen = [i for i, row in enumerate(rows) if row.kind != "info"]
    n = int(number)
    return chosen[n - 1] if 1 <= n <= len(chosen) else None


def plain_keys(line: str, view: View) -> tuple[int | None, list[str]]:
    """Turn one typed line into (a row to move the cursor to, keys to press)."""
    if view.editing is not None:
        return None, [*line, "ENTER"]
    text = line.strip()
    if not text:
        return None, ["ENTER"]
    if text in ("b", "q", "?", "s"):
        return None, [text]
    asking = text.startswith("?")
    digits = text[1:] if asking else text
    if not (digits.isascii() and digits.isdigit()):
        return None, []
    index = _numbered(view.rows, digits)
    if index is None:
        return None, []
    if asking:
        return index, ["?"]
    return index, ["ENTER" if view.rows[index].kind == "choice" else "SPACE"]


_UNCHECKED = "This box could not be checked, so some choices may not suit it."


def _intro_notes(inp: Inputs) -> tuple[str, ...]:
    """Lines added to the first screen's intro."""
    earlier = _earlier_note(inp)
    return (*(() if earlier is None else (earlier,)), *(() if inp.facts_known else (_UNCHECKED,)))


def _earlier_note(inp: Inputs) -> str | None:
    """The intro line for a fresh install that was given saved answers."""
    if inp.saved is None or inp.facts.install_state != "fresh":
        return None
    saved_at = get(inp.saved, "meta.saved_at")
    day = saved_at[:10] if isinstance(saved_at, str) and len(saved_at) >= 10 else ""
    when = day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else "an unknown date"
    return f"Filled in from answers from an earlier install on {when}."


class Session:
    """One run of the screens on an open terminal."""

    def __init__(self, inp: Inputs, term: Terminal, document: MakeDocument, *, plain: bool) -> None:
        self.inp = inp
        self.document = document
        self.term = term
        self.plain = plain
        self.notice: str | None = None

    def _view(self, state: State, first: Screen, notes: tuple[str, ...]) -> View:
        shown = flow.view(state)
        if state.screen is first:
            shown = replace(shown, intro=(*shown.intro, *notes))
        if self.notice is not None and shown.error is None:
            shown = replace(shown, error=self.notice)
        return shown

    def _keys(self, state: State, shown: View) -> tuple[State, list[str]]:
        if not self.plain:
            key = self.term.read_key()
            if key in ("INTERRUPT", "EOF"):
                raise Interrupted
            return state, [key]
        secret = state.editing is not None and state.field == "token"
        try:
            line = self.term.read_line("> ", echo=not secret)
        except termios.error:
            raise Interrupted from None
        if line is None:
            raise Interrupted
        cursor, keys = plain_keys(line, shown)
        return (state if cursor is None else replace(state, cursor=cursor)), keys

    def _save_copy(self, state: State) -> None:
        path = flow.chosen(state).get(SAVE_COPY)
        if path is None:
            return
        try:
            write_document(self.document(flow.chosen(state)), Path(str(path)))
            self.notice = None
        except DocumentError as exc:
            self.notice = str(exc)

    def _press(self, state: State, key: str) -> State:
        """Press a key; write the copy each time the save field is confirmed."""
        saving = state.editing is not None and state.field == _SAVE_FIELD
        after = flow.press(state, key)
        # Confirming a good path is the only thing that replaces the answers while
        # the field is open; a cancelled or refused entry leaves them as they were.
        if saving and after.editing is None and after.answers is not state.answers:
            self._save_copy(after)
        return after

    def run(self) -> State:
        inp = self.inp
        state = flow.start(inp.catalog, inp.facts, saved=inp.saved, given=inp.given, over=inp.over)
        first, notes = state.screen, _intro_notes(inp)
        while flow.outcome(state) is Outcome.RUNNING:
            shown = self._view(state, first, notes)
            width = self.term.width()
            self.term.draw(render(shown, width=width, plain=self.plain, color=not self.plain))
            state, keys = self._keys(state, shown)
            self.notice = None  # a failed save is shown once, until the next key
            for key in keys:
                state = self._press(state, key)
        return state


def run_screens(
    inp: Inputs, tty: str, environ: Mapping[str, str], document: MakeDocument
) -> State | None:
    """Run the screens; None when the terminal could not be used after all."""
    plain = _wants_plain(environ)
    try:
        with _raising_signals(), Terminal(tty, raw=not plain) as term:
            return Session(inp, term, document, plain=plain).run()
    except TerminalUnavailable:
        return None
    except (OSError, termios.error):
        raise Interrupted from None
