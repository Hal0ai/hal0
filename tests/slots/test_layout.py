"""Unit tests for :mod:`hal0.slots.layout` — the bilingual key primitives.

Pure functions; no HAL0_HOME / fixtures needed beyond ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

from hal0.slots.layout import (
    classify_layout,
    is_id_stem,
    slot_state_path,
    slot_toml_path,
)


def test_is_id_stem_digits_are_ids() -> None:
    assert is_id_stem("143")
    assert is_id_stem("1")
    assert is_id_stem("0")


def test_is_id_stem_names_are_not_ids() -> None:
    assert not is_id_stem("brain")
    assert not is_id_stem("flm-stt")
    assert not is_id_stem("chat2")
    assert not is_id_stem("")
    # unicode digits must NOT be mistaken for an id (str.isdigit is True for them)
    assert not is_id_stem("١٢٣")


def test_slot_toml_path_name_and_id(tmp_path: Path) -> None:
    assert slot_toml_path(tmp_path, "brain") == tmp_path / "brain.toml"
    assert slot_toml_path(tmp_path, 143) == tmp_path / "143.toml"


def test_slot_state_path_name_and_id(tmp_path: Path) -> None:
    assert slot_state_path(tmp_path, "brain") == tmp_path / "brain" / "state.json"
    assert slot_state_path(tmp_path, 143) == tmp_path / "143" / "state.json"


def test_classify_layout_mixed_tree(tmp_path: Path) -> None:
    (tmp_path / "brain.toml").write_text('name = "brain"\n', encoding="utf-8")
    (tmp_path / "143.toml").write_text('name = "flm"\n', encoding="utf-8")
    (tmp_path / "flm-stt.toml").write_text('name = "flm-stt"\n', encoding="utf-8")
    # dotfile temp must be ignored
    (tmp_path / ".chat.toml.tmp").write_text("x\n", encoding="utf-8")
    assert classify_layout(tmp_path) == {
        "brain": "name",
        "143": "id",
        "flm-stt": "name",
    }


def test_classify_layout_absent_dir(tmp_path: Path) -> None:
    assert classify_layout(tmp_path / "nope") == {}
