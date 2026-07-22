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
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
BASELINE = Path(__file__).resolve().parent / "scar_baseline.txt"

# Keep in lockstep with docs: this is the canonical scar-marker definition.
SCAR_RE = re.compile(r"removed in #|DEPRECATED|deprecated|\blegacy\b|backward.compat|compat shim")
CATALOG_TYPES = Path("src/hal0/lifecycle/types.py")
CATALOG_RESOLVER = Path("src/hal0/lifecycle/catalog.py")
CATALOG_STATUS_FIELD_RE = re.compile(r"^(\s*)deprecated(?=\s*:\s*bool\b)", re.IGNORECASE)
CATALOG_STATUS_ACCESS_RE = re.compile(
    r"\b(?:package|runner|model|r)\.deprecated\b|self\._runners\[rid\]\.deprecated\b",
    re.IGNORECASE,
)
SUNSET_RE = re.compile(r"HAL0-SUNSET:\s*v?(\d+)\.(\d+)(?:\.(\d+))?")
PROJECT_VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?(.*)$")
PEP440_PRERELEASE_RE = re.compile(r"^\.?(?:a|alpha|b|beta|rc|pre|preview|dev)", re.IGNORECASE)


@dataclass(frozen=True)
class ProjectVersion:
    raw: str
    release: tuple[int, int, int]
    is_prerelease: bool


def _release_tuple(match: re.Match[str]) -> tuple[int, int, int]:
    try:
        return (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3) or 0),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid release components: {match.group(0)!r}") from exc


def current_version() -> ProjectVersion:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    raw = data["project"]["version"]
    match = PROJECT_VERSION_RE.fullmatch(raw)
    if match is None:
        raise ValueError(f"unsupported project version: {raw!r}")
    release = _release_tuple(match)
    suffix = match.group(4).split("+", 1)[0]
    is_prerelease = suffix.startswith("-") or bool(PEP440_PRERELEASE_RE.match(suffix))
    return ProjectVersion(raw=raw, release=release, is_prerelease=is_prerelease)


def _py_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _is_scar_line(path: Path, line: str) -> bool:
    """Match migration scars without counting typed catalog status expressions."""
    relative_path = path.relative_to(ROOT)
    code, separator, comment = line.partition("#")
    if relative_path == CATALOG_TYPES:
        code = CATALOG_STATUS_FIELD_RE.sub(r"\1catalog_status", code, count=1)
    elif relative_path == CATALOG_RESOLVER:
        code = CATALOG_STATUS_ACCESS_RE.sub("catalog_status", code)
    return SCAR_RE.search(code + separator + comment) is not None


def scar_count() -> int:
    n = 0
    for f in _py_files():
        with suppress(OSError):
            n += sum(
                1
                for line in f.read_text(errors="ignore").splitlines()
                if _is_scar_line(f, line)
            )
    return n


def overdue_markers(cur: ProjectVersion) -> list[tuple[Path, int, tuple[int, int, int]]]:
    out: list[tuple[Path, int, tuple[int, int, int]]] = []
    for f in _py_files():
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            m = SUNSET_RE.search(line)
            if m:
                mv = _release_tuple(m)
                if mv < cur.release or (mv == cur.release and not cur.is_prerelease):
                    out.append((f.relative_to(ROOT), i, mv))
    return out


def _fmt(v: tuple[int, int, int]) -> str:
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
        print(f"❌ OVERDUE HAL0-SUNSET shims (current v{cur.raw}):")
        for f, i, mv in overdue:
            print(f"   {f}:{i}  sunset v{_fmt(mv)} — delete it")

    if BASELINE.exists():
        try:
            base = int(BASELINE.read_text().strip())
        except (OSError, ValueError) as exc:
            raise ValueError(f"invalid scar baseline: {BASELINE}") from exc
    else:
        base = 10**9
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
            print(
                f"   ↓ baseline can be lowered to {cnt} — run: python scripts/check_sunset.py --update-baseline"
            )
    if not overdue:
        print(f"✅ no overdue HAL0-SUNSET shims (current v{cur.raw})")

    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
