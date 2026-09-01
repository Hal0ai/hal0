#!/usr/bin/env python3
"""Regenerate the committed addon catalog under ``community/addons/``.

The 10-profile seed core is deliberately small; the eight tunes pruned out of
it live on as ``LEGACY_SEED_PROFILES`` and are published here as portable
``.hal0profile.json`` envelopes — the same wire shape the dashboard's profile
Import accepts. A site (hal0.dev) can serve this directory statically and an
operator installs one with a paste or an upload.

Everything in ``community/addons/`` is GENERATED. Edit
:data:`hal0.config.schema.LEGACY_SEED_PROFILES` (via
``src/hal0/config/_seeds_data.py``), rerun this script, commit the result;
``tests/community/test_addon_envelopes.py`` fails if the two ever disagree.

Usage:
  python scripts/export_addons.py            # rewrite community/addons/
  python scripts/export_addons.py --check     # exit 1 if anything would change
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from hal0.config.schema import LEGACY_SEED_PROFILES, ProfileConfig  # noqa: E402
from hal0.profiles.portable import export_envelope  # noqa: E402

ADDONS_DIR = ROOT / "community" / "addons"

#: Frozen, NOT a live clock. These envelopes are committed artifacts: a
#: wall-clock stamp would rewrite all eight files on every run and make the
#: checksum-stable output look churned. The checksum covers the profile body
#: only, so this value never affects integrity — it is a provenance note.
EXPORTED_AT = "2026-09-01T00:00:00Z"


def _dump(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def build_envelopes() -> dict[str, dict]:
    """Envelope-per-legacy-seed, keyed by profile name."""
    return {
        name: export_envelope(
            name,
            ProfileConfig.model_validate(entry),
            exported_at=EXPORTED_AT,
        )
        for name, entry in LEGACY_SEED_PROFILES.items()
    }


def render() -> dict[Path, str]:
    """The full generated tree: path -> file content."""
    return {
        ADDONS_DIR / f"{name}.hal0profile.json": _dump(envelope)
        for name, envelope in build_envelopes().items()
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if the committed catalog is stale",
    )
    args = parser.parse_args(argv)

    files = render()
    expected = set(files)
    stale: list[Path] = [
        path for path, body in files.items() if not path.exists() or path.read_text() != body
    ]
    orphans = [p for p in ADDONS_DIR.glob("*.hal0profile.json") if p not in expected]

    if args.check:
        for path in sorted(stale) + sorted(orphans):
            print(f"stale: {path.relative_to(ROOT)}", file=sys.stderr)
        return 1 if (stale or orphans) else 0

    ADDONS_DIR.mkdir(parents=True, exist_ok=True)
    for path, body in sorted(files.items()):
        path.write_text(body, encoding="utf-8")
    for path in orphans:
        path.unlink()
        print(f"removed orphan {path.relative_to(ROOT)}")
    print(f"wrote {len(files)} envelopes to {ADDONS_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
