"""The retired "type" tags fold into typed fields and strip from registry rows.

Pins hal0.registry.tag_retirement: ``mtp`` folds to ``defaults.mtp`` (absent-
only), ``vision`` folds into the capabilities list, all six behaviour tags
strip, descriptive tags survive, and the sweep is idempotent.
"""

from __future__ import annotations

from hal0.registry.model import Model, ModelDefaults
from hal0.registry.store import ModelRegistry
from hal0.registry.tag_retirement import retire_model_type_tags


def _add(registry: ModelRegistry, model_id: str, **kw) -> None:
    registry.add(Model(id=model_id, name=model_id, path=f"/m/{model_id}.gguf", **kw))


def test_mtp_tag_folds_to_defaults(tmp_hal0_home: str) -> None:
    registry = ModelRegistry()
    _add(registry, "m1", tags=["curated", "mtp", "chat"])

    changed = retire_model_type_tags(registry)

    assert changed == ["m1"]
    row = registry.get("m1")
    assert row.defaults is not None and row.defaults.mtp is True
    assert row.tags == ["curated", "chat"]


def test_operator_mtp_false_survives_the_fold(tmp_hal0_home: str) -> None:
    registry = ModelRegistry()
    _add(registry, "m1", tags=["mtp"], defaults=ModelDefaults(mtp=False))

    retire_model_type_tags(registry)

    row = registry.get("m1")
    assert row.defaults.mtp is False
    assert row.tags == []


def test_vision_tag_folds_into_capabilities(tmp_hal0_home: str) -> None:
    registry = ModelRegistry()
    _add(registry, "m1", tags=["vision", "frontier"], capabilities=["chat"])

    retire_model_type_tags(registry)

    row = registry.get("m1")
    assert "vision" in row.capabilities
    assert row.tags == ["frontier"]


def test_all_six_behaviour_tags_strip(tmp_hal0_home: str) -> None:
    registry = ModelRegistry()
    _add(
        registry,
        "m1",
        tags=["mtp", "moe", "tool-calling", "reasoning", "coder", "vision", "user-added"],
    )

    retire_model_type_tags(registry)

    assert registry.get("m1").tags == ["user-added"]


def test_descriptive_only_rows_untouched_and_idempotent(tmp_hal0_home: str) -> None:
    registry = ModelRegistry()
    _add(registry, "clean", tags=["curated", "chat", "long-context"])
    _add(registry, "dirty", tags=["reasoning"])

    first = retire_model_type_tags(registry)
    assert first == ["dirty"]
    # Second pass finds nothing left to do.
    assert retire_model_type_tags(registry) == []
    assert registry.get("clean").tags == ["curated", "chat", "long-context"]
