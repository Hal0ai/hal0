"""Slot autoload + eviction-priority field semantics (spec 2026-08-02)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.config.schema import SlotConfig
from hal0.slots.activation import autoload_enabled
from hal0.slots.reaper import eviction_priority


def _cfg(**kw) -> SlotConfig:
    base: dict = {"name": "s1", "port": 8090}
    base.update(kw)
    return SlotConfig(**base)


class TestSlotConfigAutoload:
    def test_default_false_without_model(self) -> None:
        assert _cfg().autoload is False

    def test_derives_true_from_bound_model(self) -> None:
        # Migration shim: legacy TOML (no autoload key) with a bound model
        # keeps its implicit boot start.
        assert _cfg(model={"default": "qwen3"}).autoload is True

    def test_explicit_false_wins_over_bound_model(self) -> None:
        assert _cfg(model={"default": "qwen3"}, autoload=False).autoload is False

    def test_explicit_true_without_model(self) -> None:
        assert _cfg(autoload=True).autoload is True


class TestSlotConfigPriority:
    def test_default_50(self) -> None:
        assert _cfg().priority == 50

    @pytest.mark.parametrize("value", [0, 100])
    def test_bounds_accepted(self, value: int) -> None:
        assert _cfg(priority=value).priority == value

    @pytest.mark.parametrize("value", [-1, 101])
    def test_out_of_range_rejected(self, value: int) -> None:
        with pytest.raises(ValidationError):
            _cfg(priority=value)


class TestAutoloadEnabledRawDict:
    def test_none_and_empty(self) -> None:
        assert autoload_enabled(None) is False
        assert autoload_enabled({}) is False

    def test_legacy_bound_model_derives_true(self) -> None:
        assert autoload_enabled({"model": {"default": "qwen3"}}) is True

    def test_explicit_false_wins(self) -> None:
        assert autoload_enabled({"autoload": False, "model": {"default": "qwen3"}}) is False

    def test_explicit_true_without_model(self) -> None:
        assert autoload_enabled({"autoload": True}) is True

    def test_model_without_default_is_false(self) -> None:
        assert autoload_enabled({"model": {}}) is False


class TestEvictionPriorityRawDict:
    def test_default_on_missing(self) -> None:
        assert eviction_priority(None) == 50
        assert eviction_priority({}) == 50

    def test_reads_value(self) -> None:
        assert eviction_priority({"priority": 10}) == 10

    @pytest.mark.parametrize("bad", [True, "10", 3.5, None])
    def test_non_int_falls_back(self, bad) -> None:
        assert eviction_priority({"priority": bad}) == 50

    def test_clamps(self) -> None:
        assert eviction_priority({"priority": -5}) == 0
        assert eviction_priority({"priority": 999}) == 100
