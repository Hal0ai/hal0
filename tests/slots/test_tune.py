"""effective_tune resolver, --reasoning pin detection and the tune write guard."""

from __future__ import annotations

import pytest

from hal0.config.schema import TUNE_KEYS, ProfileConfig
from hal0.errors import BadRequest
from hal0.providers.container import resolve_slot_profile
from hal0.slots.tune import (
    effective_tune,
    guard_tune_write,
    reasoning_flag_in,
    slot_tune_table,
    tune_restart_keys,
)

P = ProfileConfig(
    flags="-b 512", ctx_size=32768, enable_thinking=None, vision="auto", speculation="auto"
)


def test_no_table_is_profile_verbatim():
    t = effective_tune({"name": "a", "type": "llm"}, P)
    assert t.as_dict() == {
        "ctx_size": 32768,
        "enable_thinking": None,
        "reasoning_effort": "default",
        "reasoning_budget": -1,
        "vision": "auto",
        "speculation": "auto",
        "sources": {k: "profile" for k in TUNE_KEYS},
    }


def test_overrides_win_and_are_sourced_slot():
    t = effective_tune({"tune": {"vision": "off", "enable_thinking": True, "ctx_size": 8192}}, P)
    assert (t.vision, t.enable_thinking, t.ctx_size) == ("off", True, 8192)
    assert t.sources["vision"] == "slot" and t.sources["speculation"] == "profile"


def test_flags_reasoning_pins_thinking():
    p = ProfileConfig(flags="-b 512 --reasoning off")
    t = effective_tune({"tune": {"enable_thinking": True}}, p)
    assert t.enable_thinking is False and t.thinking_pinned is False
    assert t.sources["enable_thinking"] == "profile-flags"


def test_effective_tune_on_type_seed():
    seed = resolve_slot_profile({"type": "llm"})
    assert (
        effective_tune({"type": "llm", "tune": {"speculation": "ngram"}}, seed).speculation
        == "ngram"
    )


@pytest.mark.parametrize(
    "flags,want",
    [
        ("", None),
        ("-rea on", True),
        ("--reasoning=off", False),
        ("--reasoning auto", None),
        ("--reasoning on --reasoning off", False),
        ("--reasoning_budget 0 --reasoning-format deepseek", None),
    ],
)
def test_reasoning_flag_in(flags, want):
    assert reasoning_flag_in(flags) == want


def test_slot_tune_table_drops_none_and_non_mapping():
    assert slot_tune_table({}) == {}
    assert slot_tune_table({"tune": "x"}) == {}
    assert slot_tune_table({"tune": {"vision": "off", "ctx_size": None}}) == {"vision": "off"}


def test_tune_restart_keys():
    assert tune_restart_keys() == {
        "ctx_size",
        "reasoning_effort",
        "reasoning_budget",
        "vision",
        "speculation",
    }


def test_guard_refuses_non_llm_and_pinned():
    with pytest.raises(BadRequest) as e:
        guard_tune_write({"type": "embedding"}, {"tune": {"vision": "off"}}, P)
    assert e.value.code == "slot.tune_not_supported"
    with pytest.raises(BadRequest) as e:
        guard_tune_write(
            {"type": "llm"},
            {"tune": {"enable_thinking": True}},
            ProfileConfig(flags="--reasoning off"),
        )
    assert e.value.code == "slot.tune_pinned_by_flags"
    guard_tune_write(
        {"type": "llm"}, {"tune": {"vision": "off"}}, ProfileConfig(flags="--reasoning off")
    )


def test_guard_npu_clear_and_unresolved_profile():
    with pytest.raises(BadRequest) as e:
        guard_tune_write({"type": "llm", "device": "npu"}, {"tune": {"vision": "off"}}, P)
    assert e.value.code == "slot.tune_not_supported"
    # merged view: an update can flip the type
    with pytest.raises(BadRequest):
        guard_tune_write({"type": "llm"}, {"type": "stt", "tune": {"vision": "off"}}, P)
    guard_tune_write({"type": "embedding"}, {"tune": None}, P)  # clear never refused
    guard_tune_write({"type": "embedding"}, {"name": "x"}, P)  # no tune: no-op
    guard_tune_write({"type": "llm"}, {"tune": {"enable_thinking": True}}, None)  # profile unknown
