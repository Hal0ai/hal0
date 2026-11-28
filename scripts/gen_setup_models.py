#!/usr/bin/env python3
"""Generate installer/manifests/setup-models.json from the curated registry.

The installer wizard runs before hal0 is installed, so it cannot import the
registry; it reads this file instead. Run from a checkout with hal0 importable:

    PYTHONPATH=src python scripts/gen_setup_models.py          # write
    PYTHONPATH=src python scripts/gen_setup_models.py --check  # compare

The output is a pure function of the registry: no model is named here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hal0.install.brain_model import BRAIN_MODEL_IDS
from hal0.registry.curated import CURATED_MODELS, CuratedModel

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "installer" / "manifests" / "setup-models.json"
COMMAND = "PYTHONPATH=src python scripts/gen_setup_models.py"


def _row(m: CuratedModel, role: str) -> dict:
    row: dict = {
        "id": m.id,
        "role": role,
        "name": m.display_name,
        "size_gb": m.size_gb,
        "min_gpu_pool_gb": m.vram_gb_min,
    }
    # The registry has no RAM requirement field today; emit one only if it does.
    min_ram = getattr(m, "min_ram_gb", None)
    if min_ram is not None:
        row["min_ram_gb"] = min_ram
    return row


def _largest_first(models: list[CuratedModel]) -> list[CuratedModel]:
    # Stable sort: ties keep registry order, exactly as suggest_models does.
    return sorted(models, key=lambda m: -m.vram_gb_min)


def build() -> dict:
    chat = [
        m
        for m in CURATED_MODELS
        if m.capability == "chat" and not m.bundle_only and "npu" not in m.tags
    ]
    brain = [m for m in chat if m.id in BRAIN_MODEL_IDS]
    rows = [_row(m, "starter") for m in _largest_first(chat)]
    rows += [_row(m, "brain") for m in _largest_first(brain)]
    return {
        "version": 1,
        "generated_from": "src/hal0/registry/curated.py",
        "models": rows,
    }


def render() -> str:
    return json.dumps(build(), indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the file is stale")
    args = ap.parse_args(argv)
    text = render()
    if args.check:
        if OUT.exists() and OUT.read_text() == text:
            return 0
        print(f"{OUT.relative_to(ROOT)} is stale: run `{COMMAND}`")
        return 1
    OUT.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
