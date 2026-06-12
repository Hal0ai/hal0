"""Vision capability surfacing (#515).

hal0 gains a first-class ``vision`` capability. Rather than introducing a
brand-new model download, the vision slot REUSES the curated multimodal
MoE primaries that PR #500 already reconciled into ``curated.py``:
``Qwen3.6-35B-A3B-MTP-GGUF`` and ``Qwen3.6-27B-MTP-GGUF`` both carry the
``vision`` tag and ship a stock mmproj sidecar, so a vision slot
can load them straight from the bundled catalogue.

These tests pin three things:
  - the ``vision`` capability/child is a recognized slot + child, and
  - ``models_for_capability("vision")`` surfaces a multimodal model even
    though those entries are otherwise ``bundle_only``.
"""

from __future__ import annotations

from hal0.capabilities import orchestrator as orch
from hal0.capabilities.catalog import models_for_capability
from hal0.registry.curated import CURATED_BY_ID
from hal0.slots.manager import SEEDED_SLOTS

# The curated multimodal MoE primaries the vision slot reuses.
_VISION_MODELS = ("Qwen3.6-35B-A3B-MTP-GGUF", "Qwen3.6-27B-MTP-GGUF")


def test_vision_slot_is_seeded() -> None:
    assert "vision" in SEEDED_SLOTS


def test_vision_child_is_recognized() -> None:
    # The (slot, child) tuple resolves to an underlying slot name.
    assert ("vision", "vision") in orch._CHILD_TO_SLOT
    assert orch.child_to_slot("vision", "vision") == "vision"
    # And the capability slot is legal for HTTP validation.
    assert "vision" in orch.LEGAL_SLOTS
    assert orch.legal_children("vision") == ["vision"]


def test_vision_child_maps_to_vision_capability() -> None:
    assert orch._CHILD_TO_CAPABILITY[("vision", "vision")] == "vision"


def test_curated_vision_models_carry_vision_tag() -> None:
    for model_id in _VISION_MODELS:
        entry = CURATED_BY_ID[model_id]
        assert "vision" in entry.tags, f"{model_id} lost its vision tag"


def test_vision_dropdown_surfaces_a_multimodal_model() -> None:
    rows = models_for_capability("vision", registry=None)
    ids = {row["id"] for row in rows}
    # At least one of the curated multimodal MoE primaries is surfaced.
    assert ids & set(_VISION_MODELS), f"no multimodal model surfaced for vision; got {sorted(ids)}"


def test_vision_model_offers_a_real_backend() -> None:
    rows = models_for_capability("vision", registry=None)
    vision_rows = [r for r in rows if r["id"] in _VISION_MODELS]
    assert vision_rows, "no curated vision model surfaced"
    backends = {b["id"] for row in vision_rows for b in row["backends"]}
    # llama.cpp-compatible GGUF fans out to a real host backend.
    assert backends & {"gpu-vulkan", "gpu-rocm", "cpu"}
