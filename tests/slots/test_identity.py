"""SlotIdentityStore (rework §11.1) — opaque id ⇄ name bridge.

Pins the core §11.1 invariants: every slot gets a stable opaque id, a rename
is a pure relabel that never changes the id (so every id-keyed reference —
units, ports, state — survives), and the id is never reused after a delete.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.slots.identity import (
    SlotAlreadyExists,
    SlotIdentityStore,
    SlotNotFound,
)


def _store(tmp_path: Path) -> SlotIdentityStore:
    return SlotIdentityStore(db_path=tmp_path / "hal0.db")


def test_create_assigns_opaque_id_and_roundtrips(tmp_path: Path) -> None:
    store = _store(tmp_path)
    row = store.create(name="primary", slot_type="llm", device="gpu-rocm")
    assert isinstance(row.id, int) and row.id >= 1
    assert row.name == "primary"
    assert row.slot_type == "llm"
    assert row.device == "gpu-rocm"
    assert row.runtime == "container"
    assert store.get(row.id) == row
    assert store.get_by_name("primary") == row


def test_rename_preserves_id(tmp_path: Path) -> None:
    """The load-bearing §11.1 guarantee: rename changes only the label."""
    store = _store(tmp_path)
    row = store.create(name="old", slot_type="llm")
    original_id = row.id

    store.rename(row.id, "new")

    assert store.get_by_name("old") is None
    renamed = store.get_by_name("new")
    assert renamed is not None
    assert renamed.id == original_id  # id is stable across rename
    assert store.get(original_id).name == "new"


def test_rename_updates_timestamp_not_created(tmp_path: Path) -> None:
    store = _store(tmp_path)
    row = store.create(name="a", slot_type="llm")
    store.rename(row.id, "b")
    after = store.get(row.id)
    assert after.created_at == row.created_at


def test_duplicate_name_rejected_on_create(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(name="dup", slot_type="llm")
    with pytest.raises(SlotAlreadyExists):
        store.create(name="dup", slot_type="embedding")


def test_rename_to_taken_name_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(name="a", slot_type="llm")
    b = store.create(name="b", slot_type="llm")
    with pytest.raises(SlotAlreadyExists):
        store.rename(b.id, "a")


def test_get_missing_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(SlotNotFound):
        store.get(999)
    assert store.get_by_name("nope") is None


def test_id_not_reused_after_delete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = store.create(name="a", slot_type="llm")
    store.delete(a.id)
    b = store.create(name="b", slot_type="llm")
    assert b.id > a.id
    with pytest.raises(SlotNotFound):
        store.get(a.id)


def test_resolve_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    row = store.create(name="a", slot_type="llm")
    assert store.resolve_id("a") == row.id
    with pytest.raises(SlotNotFound):
        store.resolve_id("missing")


def test_list_all_and_by_type(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(name="llm1", slot_type="llm")
    store.create(name="llm2", slot_type="llm")
    store.create(name="emb", slot_type="embedding")

    assert [r.name for r in store.list_all()] == ["llm1", "llm2", "emb"]
    assert [r.name for r in store.list_by_type("llm")] == ["llm1", "llm2"]


# ── GH #1383: the vestigial ``enabled`` column surface is gone ──────────────
# model-presence (not a per-slot ``enabled`` flag) is the activation signal
# since #1369; the SQLite ``slot.enabled`` column may stay on disk (additive
# schema — no destructive migration), but no code reads or writes it.


def test_slot_row_has_no_enabled_field(tmp_path: Path) -> None:
    store = _store(tmp_path)
    row = store.create(name="a", slot_type="llm")
    assert not hasattr(row, "enabled")


def test_create_rejects_enabled_kwarg(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(TypeError):
        store.create(name="a", slot_type="llm", enabled=False)  # type: ignore[call-arg]


def test_list_by_type_rejects_enabled_only_kwarg(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(name="a", slot_type="llm")
    with pytest.raises(TypeError):
        store.list_by_type("llm", enabled_only=False)  # type: ignore[call-arg]


def test_set_enabled_is_gone(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert not hasattr(store, "set_enabled")


def test_seed_ids(tmp_path: Path) -> None:
    store = _store(tmp_path)
    seed = store.create(name="s", slot_type="llm", is_seed=True)
    store.create(name="plain", slot_type="llm")
    assert store.list_seed_ids() == [seed.id]


def test_coresident_group_set(tmp_path: Path) -> None:
    store = _store(tmp_path)
    row = store.create(name="anchor", slot_type="llm", device="npu")
    assert row.coresident_group is None
    store.set_coresident_group(row.id, "npu-flm-trio")
    assert store.get(row.id).coresident_group == "npu-flm-trio"


def test_links(tmp_path: Path) -> None:
    store = _store(tmp_path)
    anchor = store.create(name="anchor", slot_type="llm", coresident_group="npu-flm-trio")
    stt = store.create(name="stt", slot_type="transcription", coresident_group="npu-flm-trio")
    store.link(anchor.id, stt.id, "served_by")
    store.link(anchor.id, stt.id, "served_by")  # idempotent
    assert store.children_of(anchor.id, "served_by") == [stt.id]
    assert store.parents_of(stt.id, "served_by") == [anchor.id]
    store.unlink(anchor.id, stt.id, "served_by")
    assert store.children_of(anchor.id, "served_by") == []


def test_link_cascades_when_parent_deleted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    anchor = store.create(name="anchor", slot_type="llm")
    stt = store.create(name="stt", slot_type="transcription")
    store.link(anchor.id, stt.id, "served_by")
    store.delete(anchor.id)
    assert store.parents_of(stt.id, "served_by") == []
