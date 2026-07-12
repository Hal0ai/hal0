#!/usr/bin/env python3
"""Pretty-print a hal0 multi-tier harness report.

Usage:
    scripts/harness-report.py tests/harness/reports/harness.json

Exit codes:
    0 — every row is pass/skip/deferred
    1 — any row is fail
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


def _colours(stream: object) -> dict[str, str]:
    if not getattr(stream, "isatty", lambda: False)():
        return dict.fromkeys(
            ("red", "yellow", "green", "blue", "bold", "dim", "rst"), ""
        )
    return {
        "red":    "\033[0;31m",
        "yellow": "\033[1;33m",
        "green":  "\033[0;32m",
        "blue":   "\033[0;36m",
        "bold":   "\033[1m",
        "dim":    "\033[2m",
        "rst":    "\033[0m",
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
    summary = report.get("summary", {})
    tiers   = report.get("tiers", [])
    rows    = report.get("rows", [])

    print(f"\n{c['bold']}hal0 harness report{c['rst']}")
    print(f"  generated : {generated}")
    print(
        f"  summary   : {c['green']}{summary.get('pass', 0)} pass{c['rst']}  "
        f"{c['red']}{summary.get('fail', 0)} fail{c['rst']}  "
        f"{c['yellow']}{summary.get('skip', 0)} skip{c['rst']}  "
        f"{c['blue']}{summary.get('deferred', 0)} deferred{c['rst']}  "
        f"({summary.get('total', 0)} total)"
    )
    print()

    # Per-tier headers.
    if tiers:
        print(f"  {c['bold']}tiers:{c['rst']}")
        for t in tiers:
            s = t.get("summary", {})
            line = (
                f"    {t['name']:<14}  "
                f"{c['green']}{s.get('pass', 0):>3} pass{c['rst']}  "
                f"{c['red']}{s.get('fail', 0):>3} fail{c['rst']}  "
                f"{c['yellow']}{s.get('skip', 0):>3} skip{c['rst']}  "
                f"{c['blue']}{s.get('deferred', 0):>3} deferred{c['rst']}"
            )
            print(line)
        print()

    # Row table.
    fmt = "  {tier:<10}  {name:<32}  {status:<10}  {dur:>7}  {detail}"
    print(c["dim"] + fmt.format(
        tier="tier", name="row", status="status", dur="ms", detail="detail"
    ) + c["rst"])
    print(c["dim"] + "  " + "─" * 110 + c["rst"])
    for row in rows:
        colour = c.get(_STATUS_COLOUR.get(row["status"], "rst"), "")
        detail = row.get("detail", "")
        if len(detail) > 80:
            detail = detail[:77] + "..."
        print(fmt.format(
            tier=row.get("tier", "?"),
            name=row["name"],
            status=f"{colour}{row['status']}{c['rst']}",
            dur=row["duration_ms"],
            detail=detail,
        ))
    print()

    return 1 if summary.get("fail", 0) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
