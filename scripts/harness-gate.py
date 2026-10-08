#!/usr/bin/env python3
"""Decide the exit status of a hal0 harness run (scripts/harness.sh).

Usage:
    scripts/harness-gate.py <harness.json> [--allow-deferred]

A run fails when:

* any row is ``fail``; or
* the slot-lifecycle group is unverified — ``runtime-slot-load`` was
  recorded ``deferred`` (``--dev`` installs cannot start a systemd slot
  unit, see tests/harness/runtime-test.sh) — unless the run opts in with
  ``--allow-deferred`` or ``HAL0_HARNESS_ALLOW_DEFERRED=1`` (#2349).

An opted-in run still names the unverified rows on its OK line, so a run
that never loaded a slot never prints a bare ``harness OK``. The tier that
loads real slots is the release gate, tier gamma (``make release-test``);
making ``--dev`` slot load work is #2377.

Exit codes:
    0 — no fail rows, and the slot lifecycle was verified or explicitly allowed
    1 — otherwise
    2 — usage error
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Rows that together prove a real slot loaded and answered a chat request.
# runtime-chat-roundtrip is recorded `skip` whenever the load did not happen.
SLOT_LIFECYCLE_ROWS = ("runtime-slot-load", "runtime-chat-roundtrip")
ALLOW_ENV = "HAL0_HARNESS_ALLOW_DEFERRED"
USAGE = f"usage: {Path(sys.argv[0]).name} <harness.json> [--allow-deferred]"


def unverified_slot_rows(rows: list[dict]) -> list[dict]:
    """Slot-lifecycle rows left unverified by a deferred slot load.

    Empty unless some row in the group is ``deferred``; a skip caused by an
    outright ``fail`` is already counted by the fail check.
    """
    group = [r for r in rows if r.get("name") in SLOT_LIFECYCLE_ROWS]
    if not any(r.get("status") == "deferred" for r in group):
        return []
    return [r for r in group if r.get("status") in ("deferred", "skip")]


def _colours(stream: object) -> dict[str, str]:
    if not getattr(stream, "isatty", lambda: False)():
        return dict.fromkeys(("red", "green", "yellow", "bold", "rst"), "")
    return {
        "red": "\033[0;31m",
        "green": "\033[0;32m",
        "yellow": "\033[1;33m",
        "bold": "\033[1m",
        "rst": "\033[0m",
    }


def main(argv: list[str]) -> int:
    args = argv[1:]
    allow = os.environ.get(ALLOW_ENV) == "1"
    if "--allow-deferred" in args:
        allow = True
        args = [a for a in args if a != "--allow-deferred"]
    if len(args) != 1 or args[0].startswith("-"):
        print(USAGE, file=sys.stderr)
        return 2

    report = json.loads(Path(args[0]).read_text())
    rows = report.get("rows", [])
    fails = sum(1 for r in rows if r.get("status") == "fail")
    unverified = unverified_slot_rows(rows)
    named = ", ".join(f"{r['name']} ({r['status']})" for r in unverified)

    out, err = _colours(sys.stdout), _colours(sys.stderr)
    failed = False
    if fails:
        print(
            f"\n{err['red']}{err['bold']}harness FAILED{err['rst']} — {fails} row(s) failed.",
            file=sys.stderr,
        )
        failed = True
    if unverified and not allow:
        print(
            f"\n{err['red']}{err['bold']}harness FAILED{err['rst']} — slot lifecycle "
            f"not verified: {named}.\n"
            "  A --dev install cannot start a systemd slot unit, so no slot was "
            "loaded.\n"
            "  The release gate (`make release-test`) is the tier that loads real slots; making "
            "--dev slot load work is #2377.\n"
            f"  To accept this run anyway: {ALLOW_ENV}=1 or --allow-deferred.",
            file=sys.stderr,
        )
        failed = True
    if failed:
        return 1

    if unverified:
        print(
            f"\n{out['yellow']}{out['bold']}harness OK{out['rst']} — slot lifecycle "
            f"not verified (allowed): {named}"
        )
    else:
        print(f"\n{out['green']}{out['bold']}harness OK{out['rst']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
