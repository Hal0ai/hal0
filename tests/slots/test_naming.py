"""slot_instance_token id-flip seam (P3-runtime-db inc4 / §11.1).

The ONE seam the M5 id-keying flip changes: ``slot_instance_token`` prefers
``cfg["id"]`` when set (id-keyed box, post-migration), else falls back to the
mutable ``name`` (today's behaviour, unchanged for a name-keyed box). Every
runtime-artefact name (Quadlet unit, systemd service, podman container) is
derived from this single token — see ``hal0.slots.naming`` module docstring.
"""

from __future__ import annotations

from hal0.slots.naming import slot_instance_token


def test_token_is_name_when_no_id_flat_shape() -> None:
    assert slot_instance_token({"name": "primary"}) == "primary"


def test_token_is_name_when_no_id_nested_shape() -> None:
    assert slot_instance_token({"slot": {"name": "primary"}}) == "primary"


def test_token_is_id_when_id_present_flat_shape() -> None:
    assert slot_instance_token({"id": 143, "name": "primary"}) == "143"


def test_token_is_id_when_id_present_nested_shape() -> None:
    assert slot_instance_token({"id": 143, "slot": {"name": "primary"}}) == "143"


def test_token_falls_back_to_name_when_id_is_falsy() -> None:
    # id=0 / id=None / id="" are all "not set" — never a valid slot id
    # (SQLite AUTOINCREMENT starts at 1), so the name path must still win.
    assert slot_instance_token({"id": None, "name": "primary"}) == "primary"
    assert slot_instance_token({"id": 0, "name": "primary"}) == "primary"
    assert slot_instance_token({"id": "", "name": "primary"}) == "primary"


def test_token_id_wins_over_name_when_both_present() -> None:
    assert slot_instance_token({"id": 7, "name": "chat"}) == "7"


def test_token_empty_when_neither_present() -> None:
    assert slot_instance_token({}) == ""
