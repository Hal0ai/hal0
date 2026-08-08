"""Vision is a MODEL property, not a slot lane (vision-slot retirement).

The dedicated ``vision`` slot lane (#515) is retired: vision is served by any
llm slot whose bound model carries it (mmproj sidecar, the registry's
``capabilities`` list / ``defaults.vision`` tri-state, surfaced per-slot via
``LoadedSlot.modalities``). These tests pin the retirement — no seeded vision
slot, no ``vision.vision`` capability child — while the MODEL-side vision
surfacing (``models_for_capability("vision")``, curated tags) stays intact
for model pickers.
"""

from __future__ import annotations

import pytest

from hal0.capabilities import orchestrator as orch
from hal0.capabilities.catalog import models_for_capability
from hal0.registry.curated import CURATED_BY_ID
from hal0.slots.manager import SEEDED_SLOTS

# The curated multimodal MoE primaries that carry the vision tag.
_VISION_MODELS = ("Qwen3.6-35B-A3B-MTP-GGUF", "Qwen3.6-27B-MTP-GGUF")


def test_vision_slot_is_not_seeded() -> None:
    assert "vision" not in SEEDED_SLOTS


def test_vision_child_is_retired() -> None:
    assert ("vision", "vision") not in orch._CHILD_TO_SLOT
    assert "vision" not in orch.LEGAL_SLOTS
    assert ("vision", "vision") not in orch._CHILD_TO_CAPABILITY
    with pytest.raises(Exception, match="unknown capability child"):
        orch.child_to_slot("vision", "vision")


def test_capabilities_toml_drops_stray_vision_selection(tmp_path) -> None:
    # An older install's [selections.vision] table is dropped on load — the
    # retired lane must not resurrect through a stale file.
    from hal0.capabilities.config import load_capabilities_config

    p = tmp_path / "capabilities.toml"
    p.write_text(
        "schema_version = 2\n"
        '[selections.vision.vision]\nmodel = "some-mm-model"\ndevice = "gpu-vulkan"\n'
        '[selections.img.img]\nmodel = ""\ndevice = "gpu-rocm"\n'
    )
    cfg = load_capabilities_config(p)
    assert "vision" not in cfg.selections
    assert "img" in cfg.selections


def test_vision_scaffold_slot_migration(tmp_path) -> None:
    from hal0.config.migrations.vision_slot_retirement import migrate_vision_slot

    # Untouched scaffold (no model bound) → removed.
    (tmp_path / "vision.toml").write_text('name = "vision"\nport = 8093\n[model]\ndefault = ""\n')
    assert migrate_vision_slot(tmp_path) is True
    assert not (tmp_path / "vision.toml").exists()
    # Idempotent.
    assert migrate_vision_slot(tmp_path) is False
    # Operator-owned (model bound) → kept.
    (tmp_path / "vision.toml").write_text('name = "vision"\nport = 8093\n[model]\ndefault = "mm"\n')
    assert migrate_vision_slot(tmp_path) is False
    assert (tmp_path / "vision.toml").exists()


# ── Model-side vision surfacing stays intact ────────────────────────────────


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
