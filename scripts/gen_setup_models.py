#!/usr/bin/env python3
"""Generate installer/manifests/setup-models.json from the curated registry.

The installer wizard runs before hal0 is installed, so it cannot import the
registry; it reads this file instead. Run from a checkout with hal0 importable:

    PYTHONPATH=src python scripts/gen_setup_models.py          # write
    PYTHONPATH=src python scripts/gen_setup_models.py --check  # compare

The output is a pure function of the installer's own lists; no model is named here:

  starter  hal0.install.agent_model.AGENT_MODEL_IDS, in that order (the ladder
           agent_model_for_hardware walks); name, size and memory need come from
           each id's curated row.
  brain    hal0.install.brain_model.BRAIN_MODEL_IDS (the ids HAL0_BRAIN_MODEL
           accepts), largest first.
  image    hal0.comfyui.catalog.CATALOG, the API-renderable models in catalog
           order: the hardware-independent catalogue behind
           hal0.install.image_models.image_offers.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hal0.comfyui.catalog import CATALOG, weights_gib
from hal0.install.agent_model import AGENT_MODEL_IDS
from hal0.install.brain_model import BRAIN_MODEL_IDS
from hal0.registry.curated import CuratedModel, get_curated

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "installer" / "manifests" / "setup-models.json"
COMMAND = "PYTHONPATH=src python scripts/gen_setup_models.py"


def _curated(model_id: str) -> CuratedModel:
    model = get_curated(model_id)
    if model is None:
        raise SystemExit(f"{model_id!r} is named by the installer but is not curated")
    return model


def _row(model_id: str, role: str) -> dict:
    m = _curated(model_id)
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


def _largest_first(ids: tuple[str, ...]) -> list[str]:
    # Stable sort: ties keep the installer's own order.
    return sorted(ids, key=lambda i: -_curated(i).vram_gb_min)


def build() -> dict:
    rows = [_row(i, "starter") for i in AGENT_MODEL_IDS]
    rows += [_row(i, "brain") for i in _largest_first(BRAIN_MODEL_IDS)]
    # The installer states no memory need per image model and its "recommended"
    # pick depends on the box, so neither is emitted. size_gb is the figure the
    # installer quotes (weights_gib, GiB to one decimal).
    rows += [
        {"id": m.id, "role": "image", "name": m.label, "size_gb": weights_gib(m)}
        for m in CATALOG.values()
        if m.api_renderable
    ]
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
