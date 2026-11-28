"""Command line entry for the setup wizard.

Order of work: read the arguments and input files, decide between the headless
path and the screens, run one of them, write the answers document. Nothing but
error messages goes to standard error and nothing to standard output except
``--help`` and ``--version``. A typed secret goes only to ``--secret-out``.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from . import flow
from .catalog import CatalogError, load_catalog
from .document import (
    Document,
    DocumentError,
    effective,
    get,
    load_document,
    overrides,
    write_document,
    write_private_text,
)
from .facts import FactsError, load_facts
from .flow_basic import GPU_REASON_OTHER
from .flow_model import Outcome, State
from .session import SAVE_COPY, Inputs, Interrupted, run_screens
from .term import can_open

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_QUIT = 10
EXIT_STOPPED = 11
EXIT_INTERRUPTED = 130

VERSION_TEXT = "hal0 setup wizard 1"
_CPU_CONSENT_LINE = "set HAL0_ALLOW_CPU_ONLY=1 to continue without a GPU"
_NO_TOKEN_PLACE = "no place to keep the token; add it later"
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class _Usage(Exception):
    """Bad arguments or an unusable input; the message is one line."""


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


def _read_inputs(args: argparse.Namespace, environ: Mapping[str, str]) -> Inputs:
    try:
        catalog = _load(args.catalog, lambda p: load_catalog(p, args.models))
        facts = load_facts(None) if args.facts is None else _load(args.facts, load_facts)
        saved = _load(args.saved, load_document)
        given = _load(args.answers, load_document)
        over = overrides(args.flags, environ)
    except (CatalogError, FactsError, DocumentError) as exc:
        raise _Usage(str(exc)) from None
    assert catalog is not None and facts is not None
    return Inputs(catalog, facts, saved, given, over, args.hal0_version, args.facts is not None)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _document(inp: Inputs, chosen: Mapping[str, object]) -> Document:
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


def _gpu_reason(inp: Inputs) -> str:
    """The wording the GPU screen would show, taken from the screen itself."""
    shown = flow.view(flow.start(inp.catalog, inp.facts, saved=inp.saved, given=inp.given))
    return shown.intro[0] if shown.intro else GPU_REASON_OTHER


def _headless_stop(inp: Inputs, doc: Document) -> str | None:
    """The message to stop with, or None when a headless run may go on.

    Consent to run without a GPU counts from the effective document: a flag or
    variable, a given answers file or the saved answers of an earlier install.
    """
    outcome = inp.facts.gpu_outcome
    if outcome == "stop":
        return _gpu_reason(inp)
    question = inp.catalog.questions.get("cpu_only")
    consent = question is not None and get(doc, question.answer_key) is True
    if outcome == "cpu_only_needs_consent" and not consent:
        return f"{_gpu_reason(inp)}\n{_CPU_CONSENT_LINE}"
    return None


def _gone_saved_folder(inp: Inputs, doc: Document) -> str | None:
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


def _run_headless(inp: Inputs, out: Path) -> int:
    doc = _document(inp, {})
    stop = _headless_stop(inp, doc)
    if stop is not None:
        print(f"hal0 setup: {stop}", file=sys.stderr)
        return EXIT_STOPPED
    gone = _gone_saved_folder(inp, doc)
    if gone is not None:
        raise _Usage(f"the model folder {gone} from the saved answers no longer exists")
    write_document(doc, out)
    return EXIT_OK


# ---------------------------------------------------------------- output


def _keep_token(inp: Inputs, doc: Document, token: str, secret_out: Path | None) -> None:
    question = inp.catalog.questions.get("hf_token")
    name = get(doc, question.answer_key) if question is not None else None
    if secret_out is None or not isinstance(name, str) or not _ENV_NAME.match(name):
        print(f"hal0 setup: {_NO_TOKEN_PLACE}", file=sys.stderr)
        return
    try:
        write_private_text(f"{name}={token}\n", secret_out)
    except DocumentError as exc:
        print(f"hal0 setup: {exc}; add the token later", file=sys.stderr)


def _refresh_copy(state: State, doc: Document) -> None:
    """Write the saved copy again so it never holds answers the person later changed."""
    path = flow.chosen(state).get(SAVE_COPY)
    if path is None:
        return
    try:
        write_document(doc, Path(str(path)))
    except DocumentError as exc:
        print(f"hal0 setup: {exc}", file=sys.stderr)


def _finish(inp: Inputs, state: State, out: Path, secret_out: Path | None) -> int:
    status = flow.outcome(state)
    if status is Outcome.QUIT:
        return EXIT_QUIT
    if status is Outcome.STOPPED:
        return EXIT_STOPPED
    doc = _document(inp, flow.chosen(state))
    write_document(doc, out)
    _refresh_copy(state, doc)
    token = flow.secret(state)
    if token:
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
        state = run_screens(inp, args.tty, environ, lambda chosen: _document(inp, chosen))
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
    except (Interrupted, KeyboardInterrupt):
        return EXIT_INTERRUPTED
    except SystemExit as exc:
        # argparse exits for --help (code 0); every usage error is raised as _Usage.
        return EXIT_OK if exc.code in (0, None) else EXIT_USAGE
