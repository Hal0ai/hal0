"""Command line entry for the setup wizard."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_QUIT = 10
EXIT_STOPPED = 11
EXIT_INTERRUPTED = 130

VERSION_TEXT = "hal0 setup wizard 1"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hal0-setup-wizard", exit_on_error=False)
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the wizard version and exit",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Run the wizard and return its exit code."""
    parser = _build_parser()
    try:
        args = parser.parse_args(None if argv is None else list(argv))
    except argparse.ArgumentError as exc:
        print(f"{parser.prog}: error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except SystemExit as exc:
        # argparse exits for --help (code 0) and for usage errors (code 2).
        return EXIT_OK if exc.code in (0, None) else EXIT_USAGE
    if args.version:
        print(VERSION_TEXT)
        return EXIT_OK
    return EXIT_OK
