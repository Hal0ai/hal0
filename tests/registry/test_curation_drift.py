"""Curation single-source invariant (#500).

These tests codify the contract that ``curated.py`` is the canonical
definition of every model any other surface references:

- every id in ``recommend._PRIMARY_TIERS`` must be a curated id, and
- every ``model_name`` in the omni bundle manifests must be a curated id.

The second test is expected to FAIL until #500 reconciles the manifests
against ``CURATED_MODELS`` (today the manifests reference legacy catalog
ids that are not defined in ``curated.py``). It is
the executable definition of "done" for the unify work and the seed of
the CI drift check.
"""

from __future__ import annotations

import json
from pathlib import Path

from hal0.hardware.recommend import _PRIMARY_TIERS
from hal0.registry.curated import CURATED_BY_ID

_MANIFEST_DIR = Path(__file__).resolve().parents[2] / "installer" / "manifests" / "omni"


def _manifest_model_refs() -> dict[str, list[str]]:
    """Map every model_name referenced in the omni manifests to where it appears."""
    refs: dict[str, list[str]] = {}

    def note(name: str | None, where: str) -> None:
        if name:
            refs.setdefault(name, []).append(where)

    for manifest in sorted(_MANIFEST_DIR.glob("*.json")):
        data = json.loads(manifest.read_text())
        hal0 = data.get("hal0", {})
        for key in ("primary", "coder"):
            entry = hal0.get(key)
            if isinstance(entry, dict):
                note(entry.get("model_name"), f"{manifest.name}:{key}")
        for entry in hal0.get("aux", []) or []:
            note(entry.get("model_name"), f"{manifest.name}:aux/{entry.get('slot')}")
        for member in (data.get("omni", {}) or {}).get("members", []) or []:
            note(member.get("model_name"), f"{manifest.name}:omni")
    return refs


def test_primary_tiers_ids_are_curated() -> None:
    missing = [cid for cid, *_ in _PRIMARY_TIERS if cid not in CURATED_BY_ID]
    assert not missing, f"_PRIMARY_TIERS ids not defined in CURATED_MODELS: {missing}"


def test_manifest_model_names_are_curated() -> None:
    refs = _manifest_model_refs()
    missing = {name: locs for name, locs in refs.items() if name not in CURATED_BY_ID}
    detail = "\n".join(f"  {name}  ({', '.join(locs)})" for name, locs in sorted(missing.items()))
    assert not missing, (
        "omni manifest model_names not defined in CURATED_MODELS (drift — #500):\n" + detail
    )
