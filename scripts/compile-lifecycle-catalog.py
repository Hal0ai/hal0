#!/usr/bin/env python3
"""Validate authored lifecycle TOML and compile canonical bundled JSON."""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from hal0.lifecycle.catalog import CatalogError, LifecycleCatalog

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "hal0" / "lifecycle" / "data"
DEFAULT_OUTPUT = DATA / "catalog.json"
DOCUMENT_NAMES = ("packages", "runners", "models", "profiles", "bootstrap")


def load_authored() -> LifecycleCatalog:
    documents = {
        name: tomllib.loads((DATA / f"{name}.toml").read_text(encoding="utf-8"))
        for name in DOCUMENT_NAMES
    }
    catalog = LifecycleCatalog.from_documents(documents)
    report = catalog.validate()
    if report.errors:
        raise CatalogError("catalog validation failed:\n" + "\n".join(report.errors))
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--check", action="store_true", help="fail if compiled output is absent/stale"
    )
    action.add_argument("--write", action="store_true", help="write compiled output")
    action.add_argument("--validate", action="store_true", help="validate authored input only")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    try:
        catalog = load_authored()
    except (CatalogError, OSError, tomllib.TOMLDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    expected = catalog.canonical_json()
    output = args.output
    if args.validate:
        print("lifecycle catalog valid")
        return 0
    if args.write:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(expected, encoding="utf-8")
        print(f"wrote {output}")
        return 0
    if not output.exists():
        print(f"error: compiled lifecycle catalog is absent: {output}", file=sys.stderr)
        return 1
    if output.read_text(encoding="utf-8") != expected:
        print(f"error: compiled lifecycle catalog is stale: {output}", file=sys.stderr)
        return 1
    print(f"compiled lifecycle catalog is current: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
