#!/usr/bin/env python3
"""Pretty-print a hal0 release-gate JSON report.

Usage:
    scripts/release-test-report.py tests/release-gate-report.json

Exit codes:
    0 — every row is pass/skip/deferred
    1 — any row is fail
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


# Colour codes — auto-disabled on non-tty.
def _colours(stream: object) -> dict[str, str]:
    if not getattr(stream, "isatty", lambda: False)():
        return dict.fromkeys(("red", "yellow", "green", "blue", "bold", "dim", "rst"), "")
    return {
        "red": "\033[0;31m",
        "yellow": "\033[1;33m",
        "green": "\033[0;32m",
        "blue": "\033[0;36m",
        "bold": "\033[1m",
        "dim": "\033[2m",
        "rst": "\033[0m",
    }


_STATUS_COLOUR = {
    "pass": "green",
    "fail": "red",
    "skip": "yellow",
    "deferred": "blue",
}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <report.json>", file=sys.stderr)
        return 2

    path = Path(argv[1])
    if not path.exists():
        print(f"!  report not found: {path}", file=sys.stderr)
        return 2

    report = json.loads(path.read_text())
    c = _colours(sys.stdout)

    generated = datetime.fromtimestamp(report.get("generated", 0)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    host = report.get("host", "?")
    prefix = report.get("prefix", "?")
    summary = report.get("summary", {})

    print(f"\n{c['bold']}hal0 release-gate report{c['rst']}")
    print(f"  generated : {generated}")
    print(f"  host      : {host}")
    print(f"  prefix    : {prefix}")
    print(
        f"  summary   : {c['green']}{summary.get('pass', 0)} pass{c['rst']}  "
        f"{c['red']}{summary.get('fail', 0)} fail{c['rst']}  "
        f"{c['yellow']}{summary.get('skip', 0)} skip{c['rst']}  "
        f"{c['blue']}{summary.get('deferred', 0)} deferred{c['rst']}  "
        f"({summary.get('total', 0)} total)"
    )
    print()

    # Table.
    fmt = "  {name:<14}  {status:<10}  {dur:>9}  {detail}"
    print(c["dim"] + fmt.format(name="row", status="status", dur="ms", detail="detail") + c["rst"])
    print(c["dim"] + "  " + "─" * 78 + c["rst"])
    for row in report.get("rows", []):
        colour = c.get(_STATUS_COLOUR.get(row["status"], "rst"), "")
        print(
            fmt.format(
                name=row["name"],
                status=f"{colour}{row['status']}{c['rst']}",
                dur=row["duration_ms"],
                detail=row.get("detail", ""),
            )
        )
    print()

    return 1 if summary.get("fail", 0) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
