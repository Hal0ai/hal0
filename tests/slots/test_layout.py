"""Unit tests for :mod:`hal0.slots.layout` — the bilingual key primitives.

Pure functions; no HAL0_HOME / fixtures needed beyond ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

from hal0.slots.layout import (
    classify_layout,
    is_id_stem,
    read_slot_display_name,
    resolve_slot_stem,
    slot_state_path,
    slot_stems_by_name,
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


# ── name→stem resolution (#1510) ─────────────────────────────────────────────


def test_read_slot_display_name_flat_and_nested(tmp_path: Path) -> None:
    (tmp_path / "1.toml").write_text('name = "agent"\nport = 8087\n', encoding="utf-8")
    (tmp_path / "2.toml").write_text('[slot]\nname = "code"\n', encoding="utf-8")
    assert read_slot_display_name(tmp_path / "1.toml") == "agent"
    assert read_slot_display_name(tmp_path / "2.toml") == "code"


def test_read_slot_display_name_tolerates_junk(tmp_path: Path) -> None:
    (tmp_path / "bad.toml").write_text("this is not = = toml\n", encoding="utf-8")
    (tmp_path / "nameless.toml").write_text("port = 1\n", encoding="utf-8")
    # A digit name is refused: a slot name is never all-digit, and honouring one
    # would let an id stem masquerade as a display name.
    (tmp_path / "digit.toml").write_text('name = "143"\n', encoding="utf-8")
    assert read_slot_display_name(tmp_path / "bad.toml") is None
    assert read_slot_display_name(tmp_path / "nameless.toml") is None
    assert read_slot_display_name(tmp_path / "digit.toml") is None
    assert read_slot_display_name(tmp_path / "absent.toml") is None


def test_slot_stems_by_name_id_keyed_box(tmp_path: Path) -> None:
    (tmp_path / "1.toml").write_text('name = "agent"\n', encoding="utf-8")
    (tmp_path / "13.toml").write_text('name = "rerank"\n', encoding="utf-8")
    (tmp_path / ".1.toml.tmp").write_text('name = "half"\n', encoding="utf-8")
    assert slot_stems_by_name(tmp_path) == {"agent": "1", "rerank": "13"}


def test_resolve_slot_stem_prefers_a_literal_stem(tmp_path: Path) -> None:
    # A name-keyed box, and any caller that already holds a stem, resolve
    # without reading a single TOML.
    (tmp_path / "brain.toml").write_text('name = "brain"\n', encoding="utf-8")
    (tmp_path / "1.toml").write_text('name = "agent"\n', encoding="utf-8")
    assert resolve_slot_stem(tmp_path, "brain") == "brain"
    assert resolve_slot_stem(tmp_path, "1") == "1"


def test_resolve_slot_stem_finds_an_id_keyed_slot_by_display_name(tmp_path: Path) -> None:
    (tmp_path / "1.toml").write_text('name = "agent"\n', encoding="utf-8")
    assert resolve_slot_stem(tmp_path, "agent") == "1"


def test_resolve_slot_stem_absent_is_none(tmp_path: Path) -> None:
    (tmp_path / "1.toml").write_text('name = "agent"\n', encoding="utf-8")
    assert resolve_slot_stem(tmp_path, "quick") is None
    assert resolve_slot_stem(tmp_path, "") is None
    assert resolve_slot_stem(tmp_path / "nope", "agent") is None
