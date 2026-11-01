"""Engine knob schema and the ``HALOGEN_*`` token screen (halogen runner).

Pins the knob table the dashboard renders, the three ownership sets of
``HALOGEN_*`` names, and the two helpers that turn a profile's flags text
into variables: :func:`parse_env_tokens` (shape) and
:func:`screen_halogen_flags` (ownership and range).
"""

from __future__ import annotations

import pytest

from hal0.errors import UnprocessableEntity
from hal0.runners.knobs import (
    ENGINE_KNOBS,
    HALOGEN_DENIED_ENV,
    HALOGEN_KNOBS,
    HALOGEN_MANAGED_ENV,
    HALOGEN_NATIVE_CTX,
    HALOGEN_TUNE_ENV,
    EngineKnob,
    parse_env_tokens,
    screen_halogen_flags,
)


def test_knob_env_names_and_order():
    assert [k.env for k in HALOGEN_KNOBS] == [
        "HALOGEN_KV_POOL_POSITIONS",
        "HALOGEN_KV_SLOTS",
        "HALOGEN_MAX_TOK",
        "HALOGEN_WEIGHTS_LOCK",
        "HALOGEN_KV8",
        "HALOGEN_PROMPT_CACHE",
        "HALOGEN_MAX_TOKENS_DEFAULT",
        "HALOGEN_HOST_RESERVE_GIB",
    ]


def test_knob_table_shape():
    by_env = {k.env: k for k in HALOGEN_KNOBS}
    assert ENGINE_KNOBS == {"halogen": HALOGEN_KNOBS}
    assert HALOGEN_NATIVE_CTX == 262144

    pool = by_env["HALOGEN_KV_POOL_POSITIONS"]
    assert (pool.kind, pool.default, pool.minimum, pool.memory) == ("int", "262144", 1024, True)
    slots = by_env["HALOGEN_KV_SLOTS"]
    assert (slots.kind, slots.default, slots.minimum, slots.maximum) == ("int", "2", 1, 64)
    assert slots.memory is True
    max_tok = by_env["HALOGEN_MAX_TOK"]
    assert (max_tok.kind, max_tok.default, max_tok.choices) == ("enum", "16384", ("16384", "32768"))
    assert max_tok.memory is True
    lock = by_env["HALOGEN_WEIGHTS_LOCK"]
    assert (lock.kind, lock.default, lock.memory) == ("bool", "1", False)
    kv8 = by_env["HALOGEN_KV8"]
    assert (kv8.kind, kv8.default, kv8.memory) == ("bool", "0", True)
    cache = by_env["HALOGEN_PROMPT_CACHE"]
    assert (cache.kind, cache.default, cache.choices) == ("enum", "2", ("0", "1", "2"))
    dflt = by_env["HALOGEN_MAX_TOKENS_DEFAULT"]
    assert (dflt.kind, dflt.default, dflt.minimum, dflt.maximum) == ("int", None, 1, 65536)
    reserve = by_env["HALOGEN_HOST_RESERVE_GIB"]
    assert (reserve.kind, reserve.default, reserve.minimum) == ("int", "20", 10)


def test_every_knob_has_a_one_sentence_help_and_a_label():
    for knob in HALOGEN_KNOBS:
        assert isinstance(knob, EngineKnob)
        assert knob.label.strip()
        assert knob.help.strip().endswith(".")
        assert "\n" not in knob.help


def test_knob_defaults_pass_their_own_screen():
    for knob in HALOGEN_KNOBS:
        if knob.default is not None:
            screen_halogen_flags(f"{knob.env}={knob.default}")


def test_ownership_sets_are_disjoint_and_cover_the_brief():
    assert {
        "HALOGEN_API_PORT",
        "HALOGEN_PORT",
        "HALOGEN_BIND",
        "HALOGEN_ENGINE",
        "HALOGEN_CHECKPOINT",
        "HALOGEN_MODEL_ID",
        "HALOGEN_TOKENIZER",
        "HALOGEN_DOWNLOAD",
        "HALOGEN_API_KEY",
        "HALOGEN_API_KEY_FILE",
        "HALOGEN_NGRAM_TABLE",
        "HALOGEN_MTP_HEAD",
    } <= HALOGEN_MANAGED_ENV
    assert {
        "HALOGEN_CTX",
        "HALOGEN_ENABLE_THINKING",
        "HALOGEN_REASONING_EFFORT",
        "HALOGEN_MAX_THINKING_TOKENS",
        "HALOGEN_VISION_TOWER",
        "HALOGEN_DRAFTER_DEFAULT",
        "HALOGEN_PLD",
    } == HALOGEN_TUNE_ENV
    assert {"HALOGEN_CACHE_DIR", "HALOGEN_CACHE_FILE"} <= HALOGEN_DENIED_ENV
    assert not HALOGEN_MANAGED_ENV & HALOGEN_TUNE_ENV
    assert not HALOGEN_MANAGED_ENV & HALOGEN_DENIED_ENV
    assert not HALOGEN_TUNE_ENV & HALOGEN_DENIED_ENV
    # No curated knob is owned by anyone else.
    knob_envs = {k.env for k in HALOGEN_KNOBS}
    assert not knob_envs & (HALOGEN_MANAGED_ENV | HALOGEN_TUNE_ENV | HALOGEN_DENIED_ENV)


def test_parse_env_tokens_keeps_quoted_value_whole():
    out = parse_env_tokens('HALOGEN_KV_SLOTS=2 HALOGEN_THINKING_BUDGET_MESSAGE="Time is up."')
    assert out == {"HALOGEN_KV_SLOTS": "2", "HALOGEN_THINKING_BUDGET_MESSAGE": "Time is up."}


@pytest.mark.parametrize("empty", [None, "", "   ", "\n"])
def test_parse_env_tokens_empty_is_empty(empty):
    assert parse_env_tokens(empty) == {}


def test_parse_env_tokens_later_duplicate_wins():
    assert parse_env_tokens("HALOGEN_KV_SLOTS=2 HALOGEN_KV_SLOTS=4") == {"HALOGEN_KV_SLOTS": "4"}


def test_parse_env_tokens_value_may_contain_equals():
    assert parse_env_tokens("HALOGEN_X=a=b") == {"HALOGEN_X": "a=b"}


@pytest.mark.parametrize("bad", ["--parallel 1", "HALOGEN_X", "halogen_x=1", "HALOGEN_X="])
def test_parse_env_tokens_refuses_non_env_shapes(bad):
    with pytest.raises(UnprocessableEntity) as e:
        parse_env_tokens(bad)
    assert e.value.code == "profiles.halogen_flag_malformed"


def test_parse_env_tokens_refuses_a_value_with_a_line_break():
    # A newline would split an Environment= line in the generated unit file.
    with pytest.raises(UnprocessableEntity) as e:
        parse_env_tokens('HALOGEN_X="a\nb"')
    assert e.value.code == "profiles.halogen_flag_malformed"


def test_parse_env_tokens_refuses_unbalanced_quotes():
    with pytest.raises(UnprocessableEntity) as e:
        parse_env_tokens('HALOGEN_X="unterminated')
    assert e.value.code == "profiles.halogen_flag_malformed"


@pytest.mark.parametrize(
    "name,owner",
    [
        ("HALOGEN_API_PORT", "slot port"),
        ("HALOGEN_CHECKPOINT", "model row"),
        ("HALOGEN_CTX", "tune"),
        ("HALOGEN_DOWNLOAD", "hal0"),
        ("HALOGEN_NPU_MODELS", "hal0"),
        ("HALOGEN_CACHE_DIR", "phase 2"),
        ("HALOGEN_RECORD_DIR", "phase 2"),
    ],
)
def test_screen_denies_owned_names(name, owner):
    with pytest.raises(UnprocessableEntity) as e:
        screen_halogen_flags(f"{name}=x")
    assert e.value.code == "profiles.halogen_flag_denied" and e.value.details["owner"] == owner
    assert e.value.details["flag"] == name


def test_screen_range_and_choices():
    screen_halogen_flags(
        "HALOGEN_KV_POOL_POSITIONS=262144 HALOGEN_KV_SLOTS=2 HALOGEN_MAX_TOK=16384 HALOGEN_WEIGHTS_LOCK=1"
    )
    for bad in (
        "HALOGEN_KV_SLOTS=65",
        "HALOGEN_MAX_TOK=4096",
        "HALOGEN_HOST_RESERVE_GIB=4",
        "HALOGEN_PROMPT_CACHE=3",
    ):
        with pytest.raises(UnprocessableEntity) as e:
            screen_halogen_flags(bad)
        assert e.value.code == "profiles.halogen_flag_out_of_range"


@pytest.mark.parametrize(
    "bad",
    [
        "HALOGEN_KV_SLOTS=0",
        "HALOGEN_KV_SLOTS=two",
        "HALOGEN_KV_POOL_POSITIONS=1023",
        "HALOGEN_MAX_TOKENS_DEFAULT=0",
        "HALOGEN_MAX_TOKENS_DEFAULT=65537",
        "HALOGEN_WEIGHTS_LOCK=2",
        "HALOGEN_KV8=yes",
    ],
)
def test_screen_rejects_out_of_range_int_and_bool_values(bad):
    with pytest.raises(UnprocessableEntity) as e:
        screen_halogen_flags(bad)
    assert e.value.code == "profiles.halogen_flag_out_of_range"
    assert e.value.details["flag"] == bad.split("=")[0]


@pytest.mark.parametrize("digits", [10, 4301, 5000])
def test_screen_huge_digit_string_is_out_of_range_not_a_crash(digits):
    # int() on >4300 digits raises ValueError (a 500 on the API path); a long
    # digit string must be a 422 for every int knob, bounded or not.
    for env in ("HALOGEN_KV_SLOTS", "HALOGEN_KV_POOL_POSITIONS", "HALOGEN_HOST_RESERVE_GIB"):
        with pytest.raises(UnprocessableEntity) as e:
            screen_halogen_flags(f"{env}=" + "9" * digits)
        assert e.value.code == "profiles.halogen_flag_out_of_range"
        assert e.value.details["flag"] == env
        assert len(e.value.message) < 200
        assert len(e.value.details["value"]) < 100


def test_screen_nine_digit_int_is_still_range_checked():
    screen_halogen_flags("HALOGEN_KV_POOL_POSITIONS=999999999")
    with pytest.raises(UnprocessableEntity) as e:
        screen_halogen_flags("HALOGEN_KV_SLOTS=999999999")
    assert e.value.code == "profiles.halogen_flag_out_of_range"


def test_kv_slots_help_says_generating_not_resident():
    help_text = next(k.help for k in HALOGEN_KNOBS if k.env == "HALOGEN_KV_SLOTS")
    assert "generating" in help_text
    assert "keeps warm" not in help_text


def test_max_tok_label_is_prefill_chunk():
    assert next(k.label for k in HALOGEN_KNOBS if k.env == "HALOGEN_MAX_TOK") == "Prefill chunk"


def test_screen_host_reserve_floor_names_the_page_cache():
    with pytest.raises(UnprocessableEntity) as e:
        screen_halogen_flags("HALOGEN_HOST_RESERVE_GIB=9")
    assert e.value.code == "profiles.halogen_flag_out_of_range"
    assert "page cache" in e.value.message
    screen_halogen_flags("HALOGEN_HOST_RESERVE_GIB=10")


def test_screen_passes_unknown_free_flags_and_empty_text():
    screen_halogen_flags(None)
    screen_halogen_flags("")
    screen_halogen_flags('HALOGEN_THINKING_BUDGET_MESSAGE="Time is up." HALOGEN_KV_POOL_FIT=1')


def test_screen_malformed_text_is_reported_as_malformed_not_denied():
    with pytest.raises(UnprocessableEntity) as e:
        screen_halogen_flags("--parallel 1")
    assert e.value.code == "profiles.halogen_flag_malformed"
