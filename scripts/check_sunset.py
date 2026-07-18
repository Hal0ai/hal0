#!/usr/bin/env python3
"""hal0 sunset-shim CI guardrail (rework Phase 0).

Two checks, both baselined to pass green the day they're introduced:

1. HAL0-SUNSET markers — annotate any *intentional* temporary shim with a
   comment ``# HAL0-SUNSET: v<major>.<minor>[.<patch>]`` naming the version by
   which it must be gone. CI fails once the current project version reaches that
   sunset (an overdue shim that should already have been deleted).

2. Scar-marker ratchet — the count of legacy/deprecated/shim markers in ``src/``
   may only go DOWN. It's frozen in ``scripts/scar_baseline.txt``; CI fails if the
   live count exceeds the baseline. Each de-scar PR lowers the baseline; a newly
   introduced shim must instead carry a HAL0-SUNSET marker (which check #1 tracks).

Usage:
  python scripts/check_sunset.py                    # check (CI + `make check-sunset`)
  python scripts/check_sunset.py --update-baseline  # rewrite baseline to current count
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
BASELINE = Path(__file__).resolve().parent / "scar_baseline.txt"

# Keep in lockstep with docs: this is the canonical scar-marker definition.
SCAR_RE = re.compile(r"removed in #|DEPRECATED|deprecated|\blegacy\b|backward.compat|compat shim")
SUNSET_RE = re.compile(r"HAL0-SUNSET:\s*v?(\d+)\.(\d+)(?:\.(\d+))?")


def current_version() -> tuple[int, int, int]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    parts = re.findall(r"\d+", data["project"]["version"])[:3]
    parts += ["0"] * (3 - len(parts))
    return tuple(int(x) for x in parts)  # type: ignore[return-value]


def _py_files():
    return sorted(SRC.rglob("*.py"))


def scar_count() -> int:
    n = 0
    for f in _py_files():
        try:
            n += sum(1 for line in f.read_text(errors="ignore").splitlines() if SCAR_RE.search(line))
        except OSError:
            pass
    return n


def overdue_markers(cur: tuple[int, int, int]):
    out = []
    for f in _py_files():
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            m = SUNSET_RE.search(line)
            if m:
                mv = tuple(int(m.group(k) or 0) for k in (1, 2, 3))
                if mv <= cur:
                    out.append((f.relative_to(ROOT), i, mv))
    return out


def _fmt(v) -> str:
    return ".".join(map(str, v))


def main(argv: list[str]) -> int:
    cur = current_version()
    if "--update-baseline" in argv:
        c = scar_count()
        BASELINE.write_text(f"{c}\n")
        print(f"scar_baseline.txt <- {c}")
        return 0

    fail = False

    overdue = overdue_markers(cur)
    if overdue:
        fail = True
        print(f"❌ OVERDUE HAL0-SUNSET shims (current v{_fmt(cur)}):")
        for f, i, mv in overdue:
            print(f"   {f}:{i}  sunset v{_fmt(mv)} — delete it")

    base = int(BASELINE.read_text().strip()) if BASELINE.exists() else 10**9
    cnt = scar_count()
    if cnt > base:
        fail = True
        print(
            f"❌ SCAR RATCHET: {cnt} markers > baseline {base} (+{cnt - base}). "
            "Delete shims; a justified new shim must carry a HAL0-SUNSET marker, not raise the count."
        )
    else:
        print(f"✅ scar markers: {cnt} <= baseline {base}")
        if cnt < base:
            print(f"   ↓ baseline can be lowered to {cnt} — run: python scripts/check_sunset.py --update-baseline")
    if not overdue:
        print(f"✅ no overdue HAL0-SUNSET shims (current v{_fmt(cur)})")

    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
