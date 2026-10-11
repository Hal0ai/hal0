#!/usr/bin/env python3
"""Release gate for the runner-image pin table (``manifest.json`` ``runners``).

Fails (exit 1) while any default-tier runner has a null tag or digest, while
the pin table and :data:`hal0.runners.RUNNER_IMAGES` disagree on the set of
runners, while ``standard`` and ``cpu`` name different llama.cpp ``b<N>``
releases, or while a published ``cuda`` pin is not a ``server-cuda-b<M>`` tag
with ``M <= N``. A ``cuda`` pin more than 50 releases behind only warns. The
rules live in :func:`hal0.runners.pins.release_gate_problems`; this script only
prints them. ``scripts/release-check.sh`` runs it before a tag.

Usage:
  python scripts/check_runner_pins.py            # check
  python scripts/check_runner_pins.py --export   # print exports/runner-image-pins.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hal0.runners.pins import (
    export_payload,
    release_gate_problems,
    release_gate_warnings,
    runner_pins,
)


def main(argv: list[str]) -> int:
    if argv == ["--export"]:
        print(json.dumps(export_payload(), indent=2))
        return 0
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    for line in release_gate_warnings():
        print(f"runner pins: warning: {line}", file=sys.stderr)
    problems = release_gate_problems()
    for line in problems:
        print(f"runner pins: {line}", file=sys.stderr)
    if problems:
        return 1
    print(f"runner pins: all {len(runner_pins())} runners release-ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
