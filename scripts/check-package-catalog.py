#!/usr/bin/env python3
"""Compare authenticated GitHub container inventory with the authored package catalog."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hal0.lifecycle.types import PackageDefinition


def _inventory_names(document: Any) -> set[str]:
    if not isinstance(document, list):
        raise ValueError("GitHub JSON must be a package list")
    names: set[str] = set()
    for row in document:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ValueError("every GitHub package row must contain a string name")
        package_type = row.get("package_type", "container")
        if package_type == "container":
            names.add(row["name"].lower())
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--github-json", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    args = parser.parse_args()

    try:
        inventory = _inventory_names(json.loads(args.github_json.read_text(encoding="utf-8")))
        authored = tomllib.loads(args.catalog.read_text(encoding="utf-8"))
        packages = tuple(
            PackageDefinition.model_validate(row) for row in authored.get("packages", ())
        )
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        tomllib.TOMLDecodeError,
        ValidationError,
    ) as exc:
        print(f"error: immutable package inventory could not be verified: {exc}", file=sys.stderr)
        return 2

    represented = {
        package.repository.removeprefix("ghcr.io/hal0ai/").lower()
        for package in packages
        if package.repository.lower().startswith("ghcr.io/hal0ai/")
    }
    exclusions: set[str] = set()
    exclusion_errors: list[str] = []
    for row in authored.get("reviewed_exclusions", ()):
        if not isinstance(row, dict) or not row.get("name") or not row.get("reason"):
            exclusion_errors.append("reviewed exclusions require name and reason")
            continue
        exclusions.add(str(row["name"]).lower())

    missing = sorted(inventory - represented - exclusions)
    stale_exclusions = sorted(exclusions - inventory)
    errors = exclusion_errors
    if missing:
        errors.append(
            "visible GitHub packages missing from catalog/reviewed exclusions: "
            + ", ".join(missing)
        )
    if stale_exclusions:
        errors.append(
            "reviewed exclusions not present in GitHub inventory: " + ", ".join(stale_exclusions)
        )
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1

    print(
        f"package catalog covers {len(inventory)} visible containers "
        f"({len(inventory & represented)} represented, {len(inventory & exclusions)} excluded)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
