"""Command line entry for the setup wizard.

Order of work: read the arguments and input files, decide between the headless
path and the screens, run one of them, write the answers document. Nothing but
error messages goes to standard error and nothing to standard output except
``--help`` and ``--version``. A typed secret goes only to ``--secret-out``.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import signal
import sys
import termios
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import NoReturn

from . import flow
from .catalog import Catalog, CatalogError, load_catalog
from .document import (
    Document,
    DocumentError,
    Override,
    effective,
    get,
    load_document,
    overrides,
    write_document,
    write_private_text,
)
from .facts import Facts, FactsError, load_facts
from .flow_model import Outcome, Row, Screen, State, View
from .render import render
from .term import Terminal, TerminalUnavailable, can_open

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_QUIT = 10
EXIT_STOPPED = 11
EXIT_INTERRUPTED = 130

VERSION_TEXT = "hal0 setup wizard 1"
_CPU_CONSENT_LINE = "set HAL0_ALLOW_CPU_ONLY=1 to continue without a GPU"
_NO_TOKEN_PLACE = "no place to keep the token; add it later"
_SAVE_COPY = "_save_copy_to"
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class _Usage(Exception):
    """Bad arguments or an unusable input; the message is one line."""


class _Interrupted(Exception):
    """A signal, Ctrl-C, end of input or a lost terminal ended the run."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _Usage(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="hal0-setup-wizard")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    parser.add_argument("--catalog", type=Path, help="the setup catalog (required)")
    parser.add_argument("--out", type=Path, help="where to write the answers (required)")
    parser.add_argument("--models", type=Path, help="the generated model rows")
    parser.add_argument("--facts", type=Path, help="facts about this box")
    parser.add_argument("--saved", type=Path, help="answers saved by an earlier install")
    parser.add_argument("--answers", type=Path, help="an answers document given by the operator")
    parser.add_argument("--secret-out", type=Path, help="where to keep a typed token")
    parser.add_argument("--tty", default="/dev/tty", help="the terminal device to use")
    parser.add_argument("--hal0-version", default="unknown", help="the version being installed")
    parser.add_argument("--headless", action="store_true", help="never ask anything")
    parser.add_argument("--reconfigure", action="store_true", help="ask again on an upgrade")
    parser.add_argument("flags", nargs="*", help="installer flags, after --")
    return parser


@dataclass(frozen=True)
class _Inputs:
    catalog: Catalog
    facts: Facts
    saved: Document | None
    given: Document | None
    over: tuple[Override, ...]
    hal0_version: str


def _load[T](path: Path | None, reader: Callable[[Path], T]) -> T | None:
    """Read one input file. Anything unexpected still ends as one line, never a traceback."""
    if path is None:
        return None
    try:
        return reader(path)
    except (CatalogError, FactsError, DocumentError):
        raise
    except Exception as exc:
        raise _Usage(f"cannot read {path}: {type(exc).__name__}") from None


def _read_inputs(args: argparse.Namespace, environ: Mapping[str, str]) -> _Inputs:
    try:
        catalog = _load(args.catalog, lambda p: load_catalog(p, args.models))
        facts = load_facts(None) if args.facts is None else _load(args.facts, load_facts)
        saved = _load(args.saved, load_document)
        given = _load(args.answers, load_document)
        over = overrides(args.flags, environ)
    except (CatalogError, FactsError, DocumentError) as exc:
        raise _Usage(str(exc)) from None
    assert catalog is not None and facts is not None
    return _Inputs(catalog, facts, saved, given, over, args.hal0_version)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _document(inp: _Inputs, chosen: Mapping[str, object]) -> Document:
    return effective(
        inp.catalog,
        chosen=chosen,
        saved=inp.saved,
        given=inp.given,
        over=inp.over,
        hal0_version=inp.hal0_version,
        now=_now(),
        install_state=inp.facts.install_state,
    )


# -------------------------------------------------------------- headless


def _gpu_reason(inp: _Inputs) -> str:
    """The wording the GPU screen would show, taken from the screen itself."""
    shown = flow.view(flow.start(inp.catalog, inp.facts, saved=inp.saved, given=inp.given))
    return shown.intro[0] if shown.intro else "The graphics card on this box cannot be used."


def _headless_stop(inp: _Inputs) -> str | None:
    """The message to stop with, or None when a headless run may go on."""
    outcome = inp.facts.gpu_outcome
    if outcome == "stop":
        return _gpu_reason(inp)
    consent = any(o.key == "hardware.allow_cpu_only" and o.value is True for o in inp.over)
    if outcome == "cpu_only_needs_consent" and not consent:
        return f"{_gpu_reason(inp)}\n{_CPU_CONSENT_LINE}"
    return None


def _gone_saved_folder(inp: _Inputs, doc: Document) -> str | None:
    """The saved model folder, when it is the one in use and neither it nor its parent exists."""
    question = inp.catalog.questions.get("model_store")
    if question is None or inp.saved is None:
        return None
    key = question.answer_key
    path = get(doc, key)
    named_elsewhere = get(inp.given or {}, key) is not None or any(o.key == key for o in inp.over)
    if not isinstance(path, str) or named_elsewhere or get(inp.saved, key) != path:
        return None
    if os.path.exists(path) or os.path.exists(os.path.dirname(path)):
        return None
    return path


def _run_headless(inp: _Inputs, out: Path) -> int:
    stop = _headless_stop(inp)
    if stop is not None:
        print(f"hal0 setup: {stop}", file=sys.stderr)
        return EXIT_STOPPED
    doc = _document(inp, {})
    gone = _gone_saved_folder(inp, doc)
    if gone is not None:
        raise _Usage(f"the model folder {gone} from the saved answers no longer exists")
    write_document(doc, out)
    return EXIT_OK


# --------------------------------------------------------------- screens


@contextlib.contextmanager
def _raising_signals() -> Iterator[None]:
    """Turn SIGINT, SIGTERM and SIGHUP into an exception, then put the old handlers back."""

    def raise_interrupt(signum: int, frame: FrameType | None) -> None:
        raise _Interrupted

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


def _earlier_note(inp: _Inputs) -> str | None:
    """The intro line for a fresh install that was given saved answers."""
    if inp.saved is None or inp.facts.install_state != "fresh":
        return None
    saved_at = get(inp.saved, "meta.saved_at")
    day = saved_at[:10] if isinstance(saved_at, str) and len(saved_at) >= 10 else ""
    when = day if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else "an unknown date"
    return f"Filled in from answers from an earlier install on {when}."


class _Session:
    """One run of the screens on an open terminal."""

    def __init__(self, inp: _Inputs, term: Terminal, *, plain: bool) -> None:
        self.inp = inp
        self.term = term
        self.plain = plain
        self.notice: str | None = None
        self.copy_to: object = None

    def _view(self, state: State, first: Screen, note: str | None) -> View:
        shown = flow.view(state)
        if note is not None and state.screen is first:
            shown = replace(shown, intro=(*shown.intro, note))
        if self.notice is not None and shown.error is None:
            shown = replace(shown, error=self.notice)
        return shown

    def _keys(self, state: State, shown: View) -> tuple[State, list[str]]:
        if not self.plain:
            key = self.term.read_key()
            if key in ("INTERRUPT", "EOF"):
                raise _Interrupted
            return state, [key]
        secret = state.editing is not None and state.field == "token"
        try:
            line = self.term.read_line("> ", echo=not secret)
        except termios.error:
            raise _Interrupted from None
        if line is None:
            raise _Interrupted
        cursor, keys = plain_keys(line, shown)
        return (state if cursor is None else replace(state, cursor=cursor)), keys

    def _save_copy(self, state: State) -> None:
        path = flow.chosen(state).get(_SAVE_COPY)
        if path is None or path == self.copy_to:
            return
        self.copy_to = path
        try:
            write_document(_document(self.inp, flow.chosen(state)), Path(str(path)))
            self.notice = None
        except DocumentError as exc:
            self.notice = str(exc)

    def run(self) -> State:
        inp = self.inp
        state = flow.start(inp.catalog, inp.facts, saved=inp.saved, given=inp.given, over=inp.over)
        first, note = state.screen, _earlier_note(inp)
        while flow.outcome(state) is Outcome.RUNNING:
            shown = self._view(state, first, note)
            width = self.term.width()
            self.term.draw(render(shown, width=width, plain=self.plain, color=not self.plain))
            state, keys = self._keys(state, shown)
            for key in keys:
                state = flow.press(state, key)
            self._save_copy(state)
        return state


def _run_screens(inp: _Inputs, tty: str, environ: Mapping[str, str]) -> State | None:
    """Run the screens; None when the terminal could not be used after all."""
    plain = _wants_plain(environ)
    try:
        with _raising_signals(), Terminal(tty, raw=not plain) as term:
            return _Session(inp, term, plain=plain).run()
    except TerminalUnavailable:
        return None
    except (OSError, termios.error):
        raise _Interrupted from None


# ---------------------------------------------------------------- output


def _keep_token(inp: _Inputs, doc: Document, token: str, secret_out: Path | None) -> None:
    question = inp.catalog.questions.get("hf_token")
    name = get(doc, question.answer_key) if question is not None else None
    if secret_out is None or not isinstance(name, str) or not _ENV_NAME.match(name):
        print(f"hal0 setup: {_NO_TOKEN_PLACE}", file=sys.stderr)
        return
    try:
        write_private_text(f"{name}={token}\n", secret_out)
    except DocumentError as exc:
        print(f"hal0 setup: {exc}; add the token later", file=sys.stderr)


def _finish(inp: _Inputs, state: State, out: Path, secret_out: Path | None) -> int:
    status = flow.outcome(state)
    if status is Outcome.QUIT:
        return EXIT_QUIT
    if status is Outcome.STOPPED:
        return EXIT_STOPPED
    doc = _document(inp, flow.chosen(state))
    write_document(doc, out)
    token = flow.secret(state)
    if token is not None:
        _keep_token(inp, doc, token, secret_out)
    return EXIT_OK


def _run(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    if args.catalog is None or args.out is None:
        raise _Usage("--catalog and --out are required")
    inp = _read_inputs(args, environ)
    reuse = (
        inp.facts.install_state in ("upgrade", "unfinished")
        and inp.saved is not None
        and not args.reconfigure
    )
    quiet = args.headless or environ.get("HAL0_NONINTERACTIVE") == "1" or reuse
    if not quiet and can_open(args.tty):
        state = _run_screens(inp, args.tty, environ)
        if state is not None:
            return _finish(inp, state, args.out, args.secret_out)
    return _run_headless(inp, args.out)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Run the wizard and return its exit code."""
    env = os.environ if environ is None else environ
    parser = _build_parser()
    try:
        args = parser.parse_args(None if argv is None else list(argv))
        if args.version:
            print(VERSION_TEXT)
            return EXIT_OK
        return _run(args, env)
    except _Usage as exc:
        print(f"hal0 setup: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except DocumentError as exc:
        print(f"hal0 setup: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except (_Interrupted, KeyboardInterrupt):
        return EXIT_INTERRUPTED
    except SystemExit as exc:
        # argparse exits for --help (code 0); every usage error is raised as _Usage.
        return EXIT_OK if exc.code in (0, None) else EXIT_USAGE
