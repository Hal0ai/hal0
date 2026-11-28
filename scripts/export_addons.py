#!/usr/bin/env python3
"""Regenerate the committed addon catalog under ``community/addons/``.

The seed set is deliberately small; further tunes are published here as
portable ``.hal0profile.json`` envelopes (schema 2, the profile-centric shape
of ADR-0029) — the same wire shape the dashboard's profile Import accepts. A
site (hal0.dev) can serve this directory statically and an operator installs
one with a paste or an upload.

Everything in ``community/addons/`` is GENERATED. Edit ``ADDON_PROFILES`` in
``src/hal0/profiles/addons.py``, rerun this script, commit the result;
``tests/community/test_addon_envelopes.py`` fails if the two ever disagree.
A name that is also a code-shipped seed (``agent``, ``coding``, ...) does not
belong here: importing it would collide with the seed.

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

from hal0.profiles.addons import (  # noqa: E402
    ADDON_PROFILES,
    EXPORTED_AT,
    INDEX_KIND,
    INDEX_SCHEMA_VERSION,
    MIN_HAL0_VERSION,
    build_envelopes,
    build_index,
)

__all__ = [
    "ADDON_PROFILES",
    "EXPORTED_AT",
    "INDEX_KIND",
    "INDEX_SCHEMA_VERSION",
    "MIN_HAL0_VERSION",
    "build_envelopes",
    "build_index",
]

ADDONS_DIR = ROOT / "community" / "addons"
INDEX_PATH = ADDONS_DIR / "index.json"


def _dump(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


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


def is_stale(path: Path, body: str) -> bool:
    """True when ``path`` does not already hold the generated ``body``.

    Deliberately BLIND to the envelope's ``hal0_version``, and to nothing
    else. That field is stamped by ``export_envelope`` from
    ``hal0.__version__``, which resolves from INSTALLED package metadata
    (``src/hal0/__init__.py``) — so it differs between a release checkout, a
    tree mid-version-bump, and one where hal0 is not pip-installed at all
    (``0.0.0+source``). A raw-text comparison calls every envelope stale
    in every one of those and points the reader at the wrong problem; the
    stamp records WHICH RELEASE wrote the file, not what the file says.

    Everything the checksum covers — and the checksum itself, the name, kind,
    schema_version, exported_at, and the whole formatting of the file — is
    still compared strictly. Keep this in lockstep with
    ``tests/community/test_addon_envelopes.py::_staleness_view``.
    """
    if not path.exists():
        return True
    on_disk = path.read_text(encoding="utf-8")
    if on_disk == body:
        return False
    try:
        stamped = json.loads(on_disk).get("hal0_version")
        fresh = json.loads(body)
    except (json.JSONDecodeError, AttributeError):
        return True  # unparseable on disk — regenerate it
    if not isinstance(fresh, dict) or "hal0_version" not in fresh or stamped is None:
        return True  # index.json (no stamp): the text difference is real
    # Re-render with the committed stamp: any surviving difference is content
    # or formatting, both of which a regeneration is genuinely needed for.
    return _dump({**fresh, "hal0_version": stamped}) != on_disk


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
    stale: list[Path] = [path for path, body in files.items() if is_stale(path, body)]
    orphans = [p for p in ADDONS_DIR.glob("*.hal0profile.json") if p not in expected]

    if args.check:
        for path in sorted(stale) + sorted(orphans):
            print(f"stale: {path.relative_to(ROOT)}", file=sys.stderr)
        return 1 if (stale or orphans) else 0

    ADDONS_DIR.mkdir(parents=True, exist_ok=True)
    # Write only what `is_stale` flags, for the same reason it ignores the
    # stamp: an unconditional rewrite in a checkout whose ambient
    # `hal0.__version__` differs would restamp all every file and produce a
    # diff of pure provenance churn. Regenerating is then idempotent.
    for path in sorted(stale):
        path.write_text(files[path], encoding="utf-8")
    for path in orphans:
        path.unlink()
        print(f"removed orphan {path.relative_to(ROOT)}")
    if stale:
        for path in sorted(stale):
            print(f"wrote {path.relative_to(ROOT)}")
    print(f"{len(stale)} of {len(files)} files rewritten in {ADDONS_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
