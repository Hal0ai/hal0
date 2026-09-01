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
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from hal0.config.schema import LEGACY_SEED_PROFILES, ProfileConfig  # noqa: E402
from hal0.profiles import _runtime_family  # noqa: E402
from hal0.profiles.portable import export_envelope  # noqa: E402

ADDONS_DIR = ROOT / "community" / "addons"
INDEX_PATH = ADDONS_DIR / "index.json"

#: Frozen, NOT a live clock. These envelopes are committed artifacts: a
#: wall-clock stamp would rewrite all eight files on every run and make the
#: checksum-stable output look churned. The checksum covers the profile body
#: only, so this value never affects integrity — it is a provenance note.
EXPORTED_AT = "2026-09-01T00:00:00Z"

INDEX_KIND = "hal0.addon-index"
INDEX_SCHEMA_VERSION = 1

#: The oldest release that can install a row from this index.
#:
#: 1.1.0 shipped portable profiles but not the import-side ``runner``
#: handling, so an envelope naming a runner key that release does not know
#: had no defined behaviour there. The strip-to-Auto import (unknown key →
#: ``runner`` cleared, warned in the dry-run preview) lands in 1.2.0, in the
#: same release as this catalog. Every envelope here currently ships
#: ``runner`` unset, but the floor is a property of the CONTRACT, not of
#: today's rows.
MIN_HAL0_VERSION = "1.2.0"

#: First separator in an ``intent`` string: a middot clause (`` · 32K ctx``)
#: or an opening parenthetical. Everything before it is the headline.
_TITLE_SPLIT = re.compile(r"\s*[·(]")


def _dump(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _title(intent: str) -> str:
    """The intent's headline — the part before the first ``·`` or ``(``."""
    return _TITLE_SPLIT.split(intent, maxsplit=1)[0].strip() or intent.strip()


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


def build_index(envelopes: dict[str, dict]) -> dict:
    """The ``index.json`` catalog — one row per envelope, sorted by name."""
    rows = []
    for name in sorted(envelopes):
        envelope = envelopes[name]
        body = envelope["profile"]
        profile = ProfileConfig.model_validate(LEGACY_SEED_PROFILES[name])
        intent = body.get("intent", "")
        rows.append(
            {
                "name": name,
                "title": _title(intent),
                "description": intent,
                "runtime_family": _runtime_family(name, profile),
                "runner": body.get("runner"),
                "checksum": envelope["checksum"],
                "file": f"{name}.hal0profile.json",
                "min_hal0_version": MIN_HAL0_VERSION,
            }
        )
    return {"kind": INDEX_KIND, "schema_version": INDEX_SCHEMA_VERSION, "addons": rows}


def render() -> dict[Path, str]:
    """The full generated tree: path -> file content."""
    envelopes = build_envelopes()
    files = {
        ADDONS_DIR / f"{name}.hal0profile.json": _dump(envelope)
        for name, envelope in envelopes.items()
    }
    # NOT sort_keys: the index's own key order is part of the published
    # contract (kind, schema_version, addons — and each row reads name-first).
    files[INDEX_PATH] = json.dumps(build_index(envelopes), indent=2, ensure_ascii=False) + "\n"
    return files


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
    print(f"wrote {len(files) - 1} envelopes + index.json to {ADDONS_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
