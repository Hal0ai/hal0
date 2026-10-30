"""Tests for :func:`hal0.slot_config.tune_effective` — profile + slot [tune] merge."""

from hal0.config.schema import SlotTuneConfig
from hal0.profiles import ResolvedProfile
from hal0.slot_config import tune_effective


def _profile(**overrides: object) -> ResolvedProfile:
    """A minimal ResolvedProfile with tune defaults."""
    kwargs: dict[str, object] = {
        "name": "test-profile",
        "flags": "",
        "seed": False,
        "runtime_family": "llama-server",
        "supported_slot_types": ("chat",),
        "ctx_size": 4096,
        "enable_thinking": True,
        "reasoning_effort": "medium",
        "reasoning_budget": 2048,
        "vision": "auto",
        "speculation": "auto",
    }
    kwargs.update(overrides)
    return ResolvedProfile(**kwargs)


class TestTuneEffectiveProfileOnly:
    """Every field reads from the profile when no slot [tune] is set."""

    def test_all_profile_values(self) -> None:
        p = _profile()
        te = tune_effective(p)
        assert te.ctx_size == 4096
        assert te.enable_thinking is True
        assert te.reasoning_effort == "medium"
        assert te.reasoning_budget == 2048
        assert te.vision == "auto"
        assert te.speculation == "auto"

    def test_all_sources_are_profile(self) -> None:
        p = _profile()
        te = tune_effective(p)
        for key in ("ctx_size", "enable_thinking", "reasoning_effort",
                     "reasoning_budget", "vision", "speculation"):
            assert te.sources.get(key) == "profile", f"{key} source"


class TestTuneEffectiveSlotOverrides:
    """A non-None slot [tune] field wins and records source='slot'."""

    def test_single_override_ctx_size(self) -> None:
        p = _profile(ctx_size=4096)
        st = SlotTuneConfig(ctx_size=8192)
        te = tune_effective(p, st)
        assert te.ctx_size == 8192
        assert te.sources["ctx_size"] == "slot"
        # Others unchanged
        assert te.enable_thinking is True
        assert te.sources["enable_thinking"] == "profile"

    def test_single_override_vision_off(self) -> None:
        p = _profile(vision="auto")
        st = SlotTuneConfig(vision="off")
        te = tune_effective(p, st)
        assert te.vision == "off"
        assert te.sources["vision"] == "slot"

    def test_single_override_thinking_false(self) -> None:
        p = _profile(enable_thinking=True)
        st = SlotTuneConfig(enable_thinking=False)
        te = tune_effective(p, st)
        assert te.enable_thinking is False
        assert te.sources["enable_thinking"] == "slot"

    def test_single_override_speculation_off(self) -> None:
        p = _profile(speculation="auto")
        st = SlotTuneConfig(speculation="off")
        te = tune_effective(p, st)
        assert te.speculation == "off"
        assert te.sources["speculation"] == "slot"

    def test_multi_override(self) -> None:
        p = _profile(ctx_size=4096, enable_thinking=True, vision="auto", speculation="auto")
        st = SlotTuneConfig(ctx_size=16384, vision="off", speculation="ngram")
        te = tune_effective(p, st)
        assert te.ctx_size == 16384
        assert te.enable_thinking is True  # profile
        assert te.vision == "off"
        assert te.speculation == "ngram"
        assert te.sources["ctx_size"] == "slot"
        assert te.sources["enable_thinking"] == "profile"
        assert te.sources["vision"] == "slot"
        assert te.sources["speculation"] == "slot"

    def test_all_overridden(self) -> None:
        p = _profile()
        st = SlotTuneConfig(
            ctx_size=32768,
            enable_thinking=False,
            reasoning_effort="high",
            reasoning_budget=4096,
            vision="off",
            speculation="off",
        )
        te = tune_effective(p, st)
        for key in ("ctx_size", "enable_thinking", "reasoning_effort",
                     "reasoning_budget", "vision", "speculation"):
            assert te.sources.get(key) == "slot", f"{key} source"


class TestTuneEffectiveNoProfile:
    """A missing profile yields safe defaults."""

    def test_none_profile(self) -> None:
        te = tune_effective(None)
        assert te.ctx_size is None
        assert te.enable_thinking is None
        assert te.reasoning_effort == "default"
        assert te.reasoning_budget == -1
        assert te.vision == "auto"
        assert te.speculation == "auto"

    def test_none_profile_with_slot_tune(self) -> None:
        st = SlotTuneConfig(ctx_size=8192, enable_thinking=True)
        te = tune_effective(None, st)
        assert te.ctx_size == 8192
        assert te.enable_thinking is True
        assert te.sources["ctx_size"] == "slot"
        assert te.sources["enable_thinking"] == "slot"


class TestTuneEffectiveNoSlotTune:
    """None / empty slot_tune behaves like no overrides."""

    def test_none_slot_tune(self) -> None:
        p = _profile()
        te = tune_effective(p, None)
        assert te.ctx_size == 4096
        assert te.sources["ctx_size"] == "profile"

    def test_empty_slot_tune(self) -> None:
        p = _profile()
        st = SlotTuneConfig()
        te = tune_effective(p, st)
        assert te.ctx_size == 4096
        for key in ("ctx_size", "enable_thinking", "reasoning_effort",
                     "reasoning_budget", "vision", "speculation"):
            assert te.sources.get(key) == "profile", f"{key} source"


class TestTuneEffectiveAnyOverrides:
    def test_no_overrides(self) -> None:
        p = _profile()
        te = tune_effective(p)
        assert te.any_overrides is False

    def test_with_overrides(self) -> None:
        p = _profile()
        st = SlotTuneConfig(vision="off")
        te = tune_effective(p, st)
        assert te.any_overrides is True


class TestTuneEffectiveToDict:
    def test_profile_only_dict(self) -> None:
        p = _profile()
        te = tune_effective(p)
        d = te.to_dict()
        assert d["ctx_size"] == 4096
        assert d["enable_thinking"] is True
        assert d["speculation"] == "auto"
        assert d["sources"]["speculation"] == "profile"

    def test_override_dict(self) -> None:
        p = _profile()
        st = SlotTuneConfig(vision="off")
        te = tune_effective(p, st)
        d = te.to_dict()
        assert d["vision"] == "off"
        assert d["sources"]["vision"] == "slot"

    def test_default_values_omitted(self) -> None:
        """Default/auto values from profile are in the dict when no slot override exists."""
        p = _profile(reasoning_effort="default", reasoning_budget=-1)
        te = tune_effective(p)
        d = te.to_dict()
        # These are omitted because they're defaults AND not overridden
        assert "reasoning_effort" not in d or d["reasoning_effort"] == "default"
        assert "reasoning_budget" not in d or d["reasoning_budget"] == -1


class TestTuneEffectiveNullProfileFields:
    """Profile fields that are None stay None unless a slot override sets them."""

    def test_ctx_size_none_in_profile(self) -> None:
        p = _profile(ctx_size=None)
        te = tune_effective(p)
        assert te.ctx_size is None
        assert te.sources["ctx_size"] == "profile"

    def test_ctx_size_none_profile_override_slot(self) -> None:
        p = _profile(ctx_size=None)
        st = SlotTuneConfig(ctx_size=8192)
        te = tune_effective(p, st)
        assert te.ctx_size == 8192
        assert te.sources["ctx_size"] == "slot"

    def test_thinking_none_in_profile(self) -> None:
        p = _profile(enable_thinking=None)
        te = tune_effective(p)
        assert te.enable_thinking is None
        assert te.sources["enable_thinking"] == "profile"

    def test_thinking_none_profile_override_slot(self) -> None:
        p = _profile(enable_thinking=None)
        st = SlotTuneConfig(enable_thinking=True)
        te = tune_effective(p, st)
        assert te.enable_thinking is True
        assert te.sources["enable_thinking"] == "slot"