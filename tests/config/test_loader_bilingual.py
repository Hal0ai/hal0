"""Bilingual (name-or-id) slot-TOML load/save round-trips (P3-runtime-db).

Pins the loader half of the bilingual seam: an id-keyed file loads with its
embedded display name (never the digit stem), a slot carrying an ``id`` saves
to ``<id>.toml`` with both keys embedded, and a name-keyed slot is byte-for-
byte the pre-id shape (no stray ``id`` key).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.config import paths
from hal0.config.loader import (
    ConfigNotFound,
    list_slot_layout,
    load_slot_config,
    load_slot_config_by_id,
    save_slot_config,
)
from hal0.config.schema import SlotConfig


def test_save_name_keyed_slot_has_no_id_key(tmp_hal0_home: str) -> None:
    """A slot with id=None round-trips to the pre-id byte shape (no id key)."""
    cfg = SlotConfig(name="chat", port=8081, device="gpu-vulkan")
    save_slot_config(cfg)
    target = paths.slots_config_dir() / "chat.toml"
    with open(target, "rb") as f:
        data = tomllib.load(f)
    assert data["slot"]["name"] == "chat"
    assert "id" not in data["slot"]


def test_save_id_keyed_slot_writes_id_toml_with_both_keys(tmp_hal0_home: str) -> None:
    cfg = SlotConfig(id=143, name="brain", port=8090, device="cpu")
    save_slot_config(cfg)
    # Derived stem is the id, NOT the name.
    id_path = paths.slots_config_dir() / "143.toml"
    assert id_path.exists()
    assert not (paths.slots_config_dir() / "brain.toml").exists()
    with open(id_path, "rb") as f:
        data = tomllib.load(f)
    assert data["slot"]["id"] == 143
    assert data["slot"]["name"] == "brain"


def test_load_by_id_recovers_embedded_name(tmp_hal0_home: str) -> None:
    cfg = SlotConfig(id=143, name="brain", port=8090, device="cpu")
    cfg.model.default = "some-model"
    save_slot_config(cfg)

    loaded = load_slot_config_by_id(143)
    assert loaded.id == 143
    assert loaded.name == "brain"  # NEVER the digit "143"
    assert loaded.port == 8090
    assert loaded.model.default == "some-model"


def test_load_by_id_roundtrips_via_public_name_reader(tmp_hal0_home: str) -> None:
    """The id-keyed file also loads through the name-agnostic path reader."""
    cfg = SlotConfig(id=7, name="flm", port=8081, device="npu")
    save_slot_config(cfg)
    id_path = paths.slots_config_dir() / "7.toml"
    loaded = load_slot_config("7", path=id_path)
    assert loaded.name == "flm"


def test_load_by_id_missing_raises(tmp_hal0_home: str) -> None:
    with pytest.raises(ConfigNotFound):
        load_slot_config_by_id(999)


def test_list_slot_layout_classifies_mixed(tmp_hal0_home: str) -> None:
    save_slot_config(SlotConfig(name="chat", port=8081))
    save_slot_config(SlotConfig(id=143, name="brain", port=8090))
    assert list_slot_layout() == {"chat": "name", "143": "id"}


def test_save_explicit_path_override_untouched(tmp_hal0_home: str, tmp_path: Path) -> None:
    """An explicit path still wins over the id/name stem derivation."""
    cfg = SlotConfig(id=5, name="x", port=8081)
    dest = tmp_path / "custom.toml"
    save_slot_config(cfg, path=dest)
    assert dest.exists()
