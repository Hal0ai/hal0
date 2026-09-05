"""Schema-driven settings field metadata — completeness ratchet (#2108).

``walk_settings_schema`` is what makes every operator-editable
``Hal0Config`` key automatically a labelled, described settings row
instead of requiring a hand-authored FormRow per field (the trap that
left ``[brain_chat].tool_model`` with no dashboard path — #2108). This
mirrors the exposure-classification ratchet in
``tests/security/test_exposure.py``: a new schema field with no
``Field(description=...)`` must fail loudly here, not ship as a blank or
missing row.
"""

from __future__ import annotations

import pydantic
import pytest

from hal0.api._settings_apply import REGISTRY
from hal0.api._settings_fields import (
    SettingsFieldSchemaError,
    SettingsFieldSpec,
    walk_settings_schema,
)
from hal0.config.schema import Hal0Config


def test_every_leaf_field_has_a_description() -> None:
    """The walk itself raises on a missing description — a clean call
    over the real ``Hal0Config`` is the ratchet. If this test starts
    failing, a new field was added to the schema without a
    ``Field(description=...)``; add one before merging."""
    rows = walk_settings_schema()
    assert rows, "walk_settings_schema() returned no fields — schema import broken?"
    for row in rows:
        assert row.description, row.path
        assert row.description.strip(), row.path


def test_every_leaf_field_is_classified_in_the_apply_registry() -> None:
    """A schema key the apply-plan registry doesn't know about would
    render with no reload-effect badge — the settings-apply-plan
    equivalent of an unclassified route falling through to
    deny-by-default. New config fields must gain a registry entry in
    the same PR (``src/hal0/api/_settings_apply.py``)."""
    rows = walk_settings_schema()
    missing = [row.path for row in rows if row.path not in REGISTRY]
    assert missing == [], f"settings keys missing from the apply-plan registry: {missing}"


def test_walk_recurses_into_nested_models_not_emit_them() -> None:
    """A field whose type is itself a nested BaseModel (``memory.graph``,
    ``memory.embedding``) must be recursed into, not emitted as a row —
    there's nothing an operator can "set" a whole nested table to."""
    rows = walk_settings_schema()
    paths = {row.path for row in rows}
    assert "memory.graph" not in paths
    assert "memory.embedding" not in paths
    assert "memory.graph.enabled" in paths
    assert "memory.embedding.rerank_model" in paths


def test_tool_model_is_present_and_labelled() -> None:
    """Regression pin for #2108: tool_model must appear as a row with a
    real label and description, not silently skipped."""
    rows = {row.path: row for row in walk_settings_schema()}
    assert "brain_chat.tool_model" in rows
    row = rows["brain_chat.tool_model"]
    assert row.label == "Tool model"
    assert "tool" in row.description.lower()
    assert row.type == "string"


@pytest.mark.parametrize(
    "path, expected_type, expected_enum",
    [
        ("telemetry.enabled", "boolean", None),
        ("telemetry.channel", "enum", ["stable", "preview", "nightly"]),
        ("slots.max_slots", "number", None),
        ("slots.default_images", "map", None),
        ("models.roots", "string[]", None),
        ("brain_chat.model", "string", None),
        ("activity.max_rows", "number", None),  # int | None must not fall through to "string"
        ("security.require_auth", "boolean", None),  # bool | None
    ],
)
def test_field_type_classification(
    path: str, expected_type: str, expected_enum: list[str] | None
) -> None:
    rows = {row.path: row for row in walk_settings_schema()}
    row = rows[path]
    assert row.type == expected_type, path
    assert row.enum == expected_enum, path


def test_numeric_constraints_are_extracted() -> None:
    rows = {row.path: row for row in walk_settings_schema()}
    assert rows["slots.max_slots"].constraints == {"ge": 0}
    assert rows["dispatcher.direct_read_timeout_s"].constraints == {"ge": 30.0, "le": 600.0}
    assert rows["brain_chat.max_rounds"].constraints == {"ge": 1, "le": 100}


def test_default_factory_fields_resolve_to_a_concrete_default() -> None:
    """``models.roots`` and ``slots.default_images`` use ``default_factory`` —
    the spec's ``default`` must be the resolved value, not the factory
    function itself (a raw callable would serialize as garbage in the API
    response)."""
    rows = {row.path: row for row in walk_settings_schema()}
    assert rows["slots.default_images"].default == {}
    assert isinstance(rows["models.roots"].default, list)
    assert not callable(rows["models.roots"].default)


def test_group_is_the_top_level_hal0_config_section() -> None:
    rows = {row.path: row for row in walk_settings_schema()}
    assert rows["brain_chat.tool_model"].group == "brain_chat"
    assert rows["memory.embedding.rerank_model"].group == "memory.embedding"
    assert rows["memory.graph.enabled"].group == "memory.graph"


def test_missing_description_raises_schema_error() -> None:
    """The ratchet mechanism itself: a field with no description must
    raise, not silently emit a blank row."""

    class _Undocumented(pydantic.BaseModel):
        knob: int = 1

    with pytest.raises(SettingsFieldSchemaError, match="knob"):
        walk_settings_schema(_Undocumented)


def test_walk_settings_schema_covers_every_hal0_config_leaf() -> None:
    """Cross-check against a fresh manual walk of ``Hal0Config.model_fields``
    so a future refactor of the walker itself can't silently drop a
    section (e.g. by forgetting to recurse into a newly-added nested
    model)."""

    def _count_leaves(model_cls: type[pydantic.BaseModel]) -> int:
        total = 0
        for field in model_cls.model_fields.values():
            ann = field.annotation
            unwrapped = ann
            import types
            import typing

            origin = typing.get_origin(ann)
            if origin is typing.Union or origin is types.UnionType:
                args = [a for a in typing.get_args(ann) if a is not type(None)]
                if len(args) == 1:
                    unwrapped = args[0]
            if isinstance(unwrapped, type) and issubclass(unwrapped, pydantic.BaseModel):
                total += _count_leaves(unwrapped)
            else:
                total += 1
        return total

    assert len(walk_settings_schema()) == _count_leaves(Hal0Config)


def test_spec_is_a_frozen_dataclass_instance() -> None:
    row = walk_settings_schema()[0]
    assert isinstance(row, SettingsFieldSpec)
    with pytest.raises(AttributeError):
        row.path = "mutated"  # type: ignore[misc]
