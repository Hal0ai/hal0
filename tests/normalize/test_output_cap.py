"""The ``X-hal0-Max-Tokens`` output cap (#3804)."""

import pytest

from hal0.normalize.output_cap import apply_output_cap, parse_output_cap


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8192", 8192),
        (" 512 ", 512),
        (None, None),
        ("", None),
        ("0", None),
        ("-1", None),
        ("8192.0", None),
        ("8k", None),
        ("٨١٩٢", None),
        ("1" * 10, None),
    ],
)
def test_parse_output_cap(raw, expected):
    assert parse_output_cap(raw) == expected


def test_a_body_with_no_limit_gains_max_tokens():
    assert apply_output_cap({"model": "m"}, 8192) == {"model": "m", "max_tokens": 8192}


def test_a_smaller_limit_is_kept_and_the_body_is_returned_unchanged():
    body = {"model": "m", "max_tokens": 64}
    assert apply_output_cap(body, 8192) is body


def test_a_larger_limit_is_lowered():
    assert apply_output_cap({"max_tokens": 32000}, 8192) == {"max_tokens": 8192}


@pytest.mark.parametrize("unlimited", [-1, 0, "lots", True])
def test_a_value_that_is_not_a_usable_limit_is_replaced(unlimited):
    assert apply_output_cap({"max_tokens": unlimited}, 8192) == {"max_tokens": 8192}


def test_a_null_limit_counts_as_absent():
    assert apply_output_cap({"max_tokens": None}, 8192) == {"max_tokens": 8192}


def test_max_completion_tokens_is_bounded_without_adding_max_tokens():
    assert apply_output_cap({"max_completion_tokens": 20000}, 8192) == {
        "max_completion_tokens": 8192
    }
    kept = {"max_completion_tokens": 100}
    assert apply_output_cap(kept, 8192) is kept


def test_both_fields_are_bounded_independently():
    assert apply_output_cap({"max_completion_tokens": 9000, "max_tokens": 100}, 8192) == {
        "max_completion_tokens": 8192,
        "max_tokens": 100,
    }
