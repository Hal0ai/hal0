"""effective_tune resolver, --reasoning pin detection and the tune write guard."""

from __future__ import annotations

import pytest

from hal0.config.schema import TUNE_KEYS, ProfileConfig
from hal0.errors import BadRequest
from hal0.providers.container import resolve_slot_profile
from hal0.slot_config import reject_slot_tuning_keys
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


@pytest.mark.parametrize(
    ("enable_thinking", "code"),
    [(False, "slot.tune_pinned_by_flags"), (None, None)],
)
def test_guard_pin_refuses_only_a_set_enable_thinking(enable_thinking, code):
    base, profile = {"type": "llm"}, ProfileConfig(flags="--reasoning off")
    update = {"tune": {"enable_thinking": enable_thinking}}
    if code is None:
        guard_tune_write(base, update, profile)
        return
    with pytest.raises(BadRequest) as e:
        guard_tune_write(base, update, profile)
    assert e.value.code == code


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


@pytest.mark.parametrize(
    ("flags", "want"),
    [
        ("--reasoning 'off'", False),
        ('--reasoning "on"', True),
        ("--reasoning 'off", None),  # unbalanced quote: no pin rather than a crash
    ],
)
def test_reasoning_flag_in_reads_flags_the_way_the_save_screen_does(flags, want):
    assert reasoning_flag_in(flags) == want


def test_top_level_speculation_gets_the_tune_hint():
    with pytest.raises(BadRequest, match=r"\[tune\]"):
        reject_slot_tuning_keys({"speculation": "off"})


def test_top_level_mtp_gets_no_tune_hint():
    with pytest.raises(BadRequest) as exc:
        reject_slot_tuning_keys({"mtp": "off"})
    assert "[tune]" not in str(exc.value)


# ── invalid hand-edited [tune] keys are dropped, not launched ────────────────


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("reasoning_effort", "high --hf-file /etc/shadow"),
        ("reasoning_effort", "extreme"),
        ("reasoning_budget", "abc"),
        ("reasoning_budget", -2),
        ("reasoning_budget", True),
        ("ctx_size", -5),
        ("ctx_size", 2**24 + 1),
        ("ctx_size", "lots"),
        ("ctx_size", True),
        ("vision", "maybe"),
        ("speculation", "yes"),
        ("enable_thinking", "sometimes"),
    ],
)
def test_invalid_tune_key_falls_back_to_the_profile(key, bad, caplog):
    profile = ProfileConfig(flags="-b 512", ctx_size=32768, reasoning_effort="low")
    with caplog.at_level("WARNING", logger="hal0.slots.tune"):
        t = effective_tune({"name": "bad-tune", "tune": {key: bad}}, profile)
    assert t.sources[key] == "profile"
    assert getattr(t, key) == getattr(profile, key)
    assert any("slot.tune_key_invalid" in r.getMessage() for r in caplog.records)


def test_one_bad_key_does_not_drop_the_good_ones():
    t = effective_tune({"tune": {"vision": "off", "reasoning_budget": "abc", "ctx_size": 4096}}, P)
    assert (t.vision, t.ctx_size, t.reasoning_budget) == ("off", 4096, -1)
    assert t.sources["vision"] == "slot" and t.sources["reasoning_budget"] == "profile"


def test_numeric_text_in_a_tune_key_is_taken_as_the_number():
    t = effective_tune({"tune": {"ctx_size": "4096", "reasoning_budget": "0"}}, P)
    assert (t.ctx_size, t.reasoning_budget) == (4096, 0)


# ── free reasoning flags in the profile are what runs ────────────────────────


def test_flag_effort_and_budget_beat_the_typed_profile_fields():
    p = ProfileConfig(
        flags="--reasoning-effort low --reasoning-budget 256",
        reasoning_effort="high",
        reasoning_budget=1024,
    )
    t = effective_tune({"tune": {}}, p)
    assert (t.reasoning_effort, t.reasoning_budget) == ("low", 256)
    assert t.sources["reasoning_effort"] == "profile-flags"
    assert t.sources["reasoning_budget"] == "profile-flags"


def test_flag_spellings_and_the_negative_budget_are_read():
    p = ProfileConfig(flags="--reasoning-effort=medium --reasoning-budget -1", reasoning_budget=64)
    t = effective_tune({}, p)
    assert (t.reasoning_effort, t.reasoning_budget) == ("medium", -1)
    assert t.sources["reasoning_budget"] == "profile-flags"


def test_slot_override_beats_the_flags():
    p = ProfileConfig(flags="--reasoning-effort low --reasoning-budget 256")
    t = effective_tune({"tune": {"reasoning_effort": "high", "reasoning_budget": 0}}, p)
    assert (t.reasoning_effort, t.reasoning_budget) == ("high", 0)
    assert t.sources["reasoning_effort"] == "slot" and t.sources["reasoning_budget"] == "slot"


def test_slot_send_nothing_value_cannot_cancel_a_flag():
    p = ProfileConfig(flags="--reasoning-effort low --reasoning-budget 256")
    t = effective_tune({"tune": {"reasoning_effort": "default", "reasoning_budget": -1}}, p)
    assert (t.reasoning_effort, t.reasoning_budget) == ("low", 256)
    assert t.sources["reasoning_effort"] == "profile-flags"


def test_unusable_flag_values_are_ignored():
    p = ProfileConfig(
        flags="--reasoning-effort sideways --reasoning-budget x", reasoning_effort="low"
    )
    t = effective_tune({}, p)
    assert t.reasoning_effort == "low" and t.sources["reasoning_effort"] == "profile"
    assert t.reasoning_budget == -1 and t.sources["reasoning_budget"] == "profile"


def test_tune_effective_field_set_is_unchanged():
    t = effective_tune({}, ProfileConfig(flags="--reasoning-effort low"))
    assert set(t.as_dict()) == {*TUNE_KEYS, "sources"}
