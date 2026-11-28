#!/usr/bin/env python3
"""Generate installer/manifests/setup-models.json from the curated registry.

The installer wizard runs before hal0 is installed, so it cannot import the
registry; it reads this file instead. Run from a checkout with hal0 importable:

    PYTHONPATH=src python scripts/gen_setup_models.py          # write
    PYTHONPATH=src python scripts/gen_setup_models.py --check  # compare

The output is a pure function of the installer's own lists; no model is named
here, and rows keep each source's own order (nothing is sorted):

  starter  hal0.install.agent_model.AGENT_MODEL_IDS. This is a ladder: the first
           row whose min_gpu_pool_gb fits the box is what the installer's `auto`
           choice picks. Label, download size and memory need come from each id's
           curated row.
  brain    hal0.install.brain_model.BRAIN_MODEL_IDS (the ids HAL0_BRAIN_MODEL
           accepts), in that tuple's order. Same fields as starter.
  image    hal0.comfyui.catalog.CATALOG, the API-renderable models in catalog
           order. NOT a ladder: the installer's recommended image model depends on
           the box (recommended_model_id, src/hal0/comfyui/catalog.py), the wizard
           does not preview it, and rows carry no min_gpu_pool_gb. The wizard
           offers "the recommended set" as its own first choice and then lists
           these rows as a pick-list.

Rows use the keys the wizard's loader reads: role, model, label, download_gb and,
for starter and brain, min_gpu_pool_gb. A key is absent, never null, when no
source states it.
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
    return {
        "role": role,
        "model": m.id,
        "label": m.display_name,
        "download_gb": m.size_gb,
        "min_gpu_pool_gb": m.vram_gb_min,
    }


def _image_rows() -> list[dict]:
    # Catalogue order, no thresholds: this is a pick-list, not a ladder (see the
    # module docstring). download_gb is the figure the installer quotes
    # (weights_gib, GiB to one decimal).
    return [
        {"role": "image", "model": m.id, "label": m.label, "download_gb": weights_gib(m)}
        for m in CATALOG.values()
        if m.api_renderable
    ]


def build() -> dict:
    rows = [_row(i, "starter") for i in AGENT_MODEL_IDS]
    rows += [_row(i, "brain") for i in BRAIN_MODEL_IDS]
    rows += _image_rows()
    return {
        "version": 1,
        "generated_from": [
            "src/hal0/install/agent_model.py",
            "src/hal0/install/brain_model.py",
            "src/hal0/registry/curated.py",
            "src/hal0/comfyui/catalog.py",
        ],
        "model_ladder": rows,
    }


def render() -> str:
    return json.dumps(build(), indent=2, allow_nan=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the file is stale")
    args = ap.parse_args(argv)
    text = render()
    if args.check:
        if OUT.exists() and OUT.read_text(encoding="utf-8") == text:
            return 0
        print(f"{OUT.relative_to(ROOT)} is stale: run `{COMMAND}`")
        return 1
    OUT.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
