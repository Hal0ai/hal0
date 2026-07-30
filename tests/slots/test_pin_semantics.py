"""Unit tests for the §21.10 operator-pin resolution (`reaper.is_pinned`).

The pin overlay combines two sources:
  - the default-pinned anchor set (``agent``/``utility``/``npu``), and
  - the per-slot ``SlotConfig.pinned`` field in the raw TOML dict.

Tri-state contract (#1367): an *explicit* ``pinned`` key in the config
always wins — ``pinned = false`` un-pins a default anchor, ``pinned =
true`` pins any slot. Only when the key is absent (or the config is
unreadable) does the default anchor set apply.
"""

from __future__ import annotations

from hal0.slots.reaper import is_pinned


def test_explicit_pinned_false_overrides_default_anchor() -> None:
    """``pinned = false`` in the TOML un-pins a default anchor like utility."""
    assert is_pinned("utility", {"pinned": False}) is False


def test_explicit_pinned_true_pins_a_non_anchor_slot() -> None:
    assert is_pinned("chat", {"pinned": True}) is True


def test_absent_key_falls_back_to_default_anchor_set() -> None:
    """No ``pinned`` key at all → the anchor set decides (raw-TOML presence)."""
    assert is_pinned("utility", {}) is True
    assert is_pinned("chat", {}) is False


def test_none_config_falls_back_to_default_anchor_set() -> None:
    """Missing/unreadable config (None) keeps the fail-open anchor default."""
    assert is_pinned("agent", None) is True
    assert is_pinned("chat", None) is False


def test_explicit_pinned_none_is_treated_as_absent() -> None:
    """A ``pinned = None`` value (defensive) behaves like an absent key."""
    assert is_pinned("utility", {"pinned": None}) is True
    assert is_pinned("chat", {"pinned": None}) is False
