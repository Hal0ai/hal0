"""#1419 — attribute-style XML tool calls (the hal0-brain dialect).

``hal0-brain-sft-fpx8``'s chat template documents its own contract as::

    <function name="function-name"><param name="param-name">param-value</param></function>

llama.cpp's ``--jinja`` tool-call parsers do not recognise that shape, so the
server returns no ``tool_calls`` at all and the markup lands in
``message.content``. ``parse_text_tool_calls``'s ``<function...>`` regex only
accepted the *equals* form with a JSON body, so hal0's own fallback missed it
too and the brain tool loop could never fire.

Both forms are pinned here: the canonical one from the template, and the
**mangled** one the live slot actually returned (llama.cpp's partial-tool-call
scanner eats the ``<function``/``<param`` openers before giving up, leaving
``  name="get_weather"> name="city">Paris``). Parsing only the canonical form
would leave the reported bug unfixed on the wire.
"""

from __future__ import annotations

from hal0.toolloop import parse_text_tool_calls

_KNOWN = frozenset({"get_weather", "get_board"})


# ── the canonical template form ──────────────────────────────────────────────


def test_attribute_xml_function_with_param_child() -> None:
    text = '<function name="get_weather"><param name="city">Paris</param></function>'
    calls, cleaned = parse_text_tool_calls(text, _KNOWN)
    assert [c["name"] for c in calls] == ["get_weather"]
    assert calls[0]["arguments"] == {"city": "Paris"}
    assert cleaned == ""


def test_attribute_xml_multiple_params() -> None:
    text = (
        '<function name="get_weather">'
        '<param name="city">Paris</param>'
        '<param name="units">metric</param>'
        "</function>"
    )
    calls, _ = parse_text_tool_calls(text, _KNOWN)
    assert calls[0]["arguments"] == {"city": "Paris", "units": "metric"}


def test_attribute_xml_no_params_is_a_zero_arg_call() -> None:
    text = '<function name="get_board"></function>'
    calls, cleaned = parse_text_tool_calls(text, _KNOWN)
    assert [c["name"] for c in calls] == ["get_board"]
    assert calls[0]["arguments"] == {}
    assert cleaned == ""


def test_attribute_xml_cdata_param_value_is_unwrapped() -> None:
    """The template specifies CDATA for multi-line / ``<``-containing values."""
    text = (
        '<function name="get_weather"><param name="city">'
        "<![CDATA[Paris\nlat < 49]]>"
        "</param></function>"
    )
    calls, _ = parse_text_tool_calls(text, _KNOWN)
    assert calls[0]["arguments"] == {"city": "Paris\nlat < 49"}


def test_attribute_xml_json_param_value_is_decoded() -> None:
    text = '<function name="get_weather"><param name="opts">{"units":"metric"}</param></function>'
    calls, _ = parse_text_tool_calls(text, _KNOWN)
    assert calls[0]["arguments"] == {"opts": {"units": "metric"}}


def test_attribute_xml_surrounding_prose_survives() -> None:
    text = (
        "Let me check.\n"
        '<function name="get_weather"><param name="city">Paris</param></function>\n'
        "One moment."
    )
    calls, cleaned = parse_text_tool_calls(text, _KNOWN)
    assert [c["name"] for c in calls] == ["get_weather"]
    assert "Let me check." in cleaned and "One moment." in cleaned
    assert "get_weather" not in cleaned and "<param" not in cleaned


def test_two_attribute_xml_calls_in_one_reply() -> None:
    text = (
        '<function name="get_weather"><param name="city">Paris</param></function>'
        '<function name="get_board"></function>'
    )
    calls, cleaned = parse_text_tool_calls(text, _KNOWN)
    assert [c["name"] for c in calls] == ["get_weather", "get_board"]
    assert cleaned == ""


# ── the mangled shape the live slot actually emitted ─────────────────────────


def test_llamacpp_mangled_attribute_xml_is_still_recovered() -> None:
    """Verbatim from #1419's live capture against slot ``brain`` on :8087.

    ``finish_reason: "stop"``, no ``tool_calls`` key, and this in ``content``.
    """
    text = ' name="get_weather"> name="city">Paris'
    calls, cleaned = parse_text_tool_calls(text, _KNOWN)
    assert [c["name"] for c in calls] == ["get_weather"]
    assert calls[0]["arguments"] == {"city": "Paris"}
    assert cleaned == ""


def test_mangled_form_without_params() -> None:
    calls, cleaned = parse_text_tool_calls(' name="get_board">', _KNOWN)
    assert [c["name"] for c in calls] == ["get_board"]
    assert calls[0]["arguments"] == {}
    assert cleaned == ""


# ── safety: the known-names gate still holds ─────────────────────────────────


def test_attribute_xml_unknown_tool_is_not_a_call() -> None:
    text = '<function name="launch_missiles"><param name="when">now</param></function>'
    calls, cleaned = parse_text_tool_calls(text, _KNOWN)
    assert calls == []
    assert cleaned == text


def test_mangled_form_naming_no_real_tool_passes_through() -> None:
    """The mangled shape has no tag anchor at all, so the known-names gate is
    the ONLY thing keeping prose out. Verify it does the work."""
    text = 'The attribute is written name="colour"> in the docs.'
    calls, cleaned = parse_text_tool_calls(text, _KNOWN)
    assert calls == []
    assert cleaned == text


def test_prose_mentioning_a_tool_name_without_the_syntax_is_untouched() -> None:
    text = "You can call get_weather to find out."
    calls, cleaned = parse_text_tool_calls(text, _KNOWN)
    assert calls == []
    assert cleaned == text


def test_empty_known_names_disables_the_xml_dialect_too() -> None:
    text = '<function name="get_weather"><param name="city">Paris</param></function>'
    calls, cleaned = parse_text_tool_calls(text, frozenset())
    assert calls == [] and cleaned == text


def test_equals_form_still_wins_and_is_unchanged() -> None:
    """Regression guard: the pre-existing ``<function=NAME>{json}</function>``
    dialect must keep parsing exactly as before."""
    text = '<function=get_weather>{"city":"Paris"}</function>'
    calls, cleaned = parse_text_tool_calls(text, _KNOWN)
    assert calls[0]["name"] == "get_weather"
    assert calls[0]["arguments"] == {"city": "Paris"}
    assert cleaned == ""


# ── the Gemma-4 agentic dialect (#1419 follow-up comment) ────────────────────


def test_gemma4_pipe_delimited_dialect() -> None:
    """``<|tool_call>call:NAME{...}<tool_call|>`` — pipes INSIDE both
    delimiters, so it never matched ``<tool_call>...</tool_call>``."""
    text = '<|tool_call>call:get_weather{"city":"Paris"}<tool_call|>'
    calls, cleaned = parse_text_tool_calls(text, _KNOWN)
    assert [c["name"] for c in calls] == ["get_weather"]
    assert calls[0]["arguments"] == {"city": "Paris"}
    assert cleaned == ""


def test_gemma4_dialect_unknown_tool_is_not_a_call() -> None:
    text = '<|tool_call>call:launch_missiles{"when":"now"}<tool_call|>'
    calls, cleaned = parse_text_tool_calls(text, _KNOWN)
    assert calls == []
    assert cleaned == text


# ── #1509 — sibling calls in one wrapper, and close-less prose capture ───────
#
# Both are silent-loss bugs in the same parser as #1419/#1434: nothing raises,
# nothing logs, and the cleaned text carries no trace of what went missing.


def test_two_functions_in_one_tool_call_wrapper_both_execute() -> None:
    """A wrapper holding two calls must run BOTH (#1509).

    The nested branch accepted only ``nested[0]`` while recording the whole
    wrapper's span, so pass 2b then skipped the siblings as already-consumed
    and the second tool was dropped with no error. A model that asks for two
    tools and silently gets one is indistinguishable, from the operator's
    side, from a model that only asked for one.
    """
    text = (
        "<tool_call>"
        '<function name="get_weather"><param name="city">Paris</param></function>'
        '<function name="get_board"><param name="slug">main</param></function>'
        "</tool_call>"
    )
    calls, cleaned = parse_text_tool_calls(text, _KNOWN)
    assert [c["name"] for c in calls] == ["get_weather", "get_board"]
    assert calls[0]["arguments"] == {"city": "Paris"}
    assert calls[1]["arguments"] == {"slug": "main"}
    assert cleaned == ""


def test_three_functions_in_one_wrapper_all_execute() -> None:
    """Not special-cased at two — the loop must accept every sibling."""
    text = (
        "<tool_call>"
        '<function name="get_weather"><param name="city">Paris</param></function>'
        '<function name="get_board"><param name="slug">a</param></function>'
        '<function name="get_weather"><param name="city">Berlin</param></function>'
        "</tool_call>"
    )
    calls, _ = parse_text_tool_calls(text, _KNOWN)
    assert [c["name"] for c in calls] == ["get_weather", "get_board", "get_weather"]
    assert [c["arguments"] for c in calls] == [
        {"city": "Paris"},
        {"slug": "a"},
        {"city": "Berlin"},
    ]


def test_unknown_sibling_does_not_suppress_the_known_one() -> None:
    """An unsurfaced tool beside a real one must not cost us the real one."""
    text = (
        "<tool_call>"
        '<function name="launch_missiles"><param name="when">now</param></function>'
        '<function name="get_weather"><param name="city">Paris</param></function>'
        "</tool_call>"
    )
    calls, _ = parse_text_tool_calls(text, _KNOWN)
    assert [c["name"] for c in calls] == ["get_weather"]


def test_close_less_call_does_not_swallow_trailing_prose() -> None:
    """A mangled call with no ``</function>`` must not eat the rest of the turn.

    ``body_end = span_end = next_call_at`` degrades to ``len(text)`` when no
    later call follows, so the last param's value ran to end-of-text AND the
    span covering it stripped that prose from the visible reply — the argument
    is corrupted and the user never sees the sentence (#1509).
    """
    text = (
        "Let me check that for you.\n"
        '<function name="get_weather"><param name="city">Paris\n'
        "\n"
        "I will summarise the forecast once I have it."
    )
    calls, cleaned = parse_text_tool_calls(text, _KNOWN)
    assert [c["name"] for c in calls] == ["get_weather"]
    # the argument stops at the value, not at end-of-turn
    assert calls[0]["arguments"] == {"city": "Paris"}
    # and the model's prose survives into the reply
    assert "I will summarise the forecast once I have it." in cleaned
    assert "Let me check that for you." in cleaned


def test_close_less_call_keeps_a_multiline_value_intact() -> None:
    """The blank-line bound must not truncate a legitimately multi-line value.

    A close-less call whose value genuinely spans consecutive lines (no blank
    line) still has to arrive whole — the fix bounds on a paragraph break, not
    on the first newline.
    """
    text = '<function name="get_board"><param name="slug">line-one\nline-two'
    calls, _ = parse_text_tool_calls(text, _KNOWN)
    assert calls[0]["arguments"] == {"slug": "line-one\nline-two"}
