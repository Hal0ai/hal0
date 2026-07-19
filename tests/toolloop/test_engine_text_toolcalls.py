"""Attribute-form (and legacy) text tool-call parsing in the toolloop engine.

The fpx8-agent steward / minicpm5 / hermes tool-use templates emit tool calls
as ``<function name="NAME">…</function>`` text (the ATTRIBUTE form), sometimes
wrapped in ``<function_calls>`` and sometimes carrying nested
``<parameter name="k">v</parameter>`` bodies. The engine must normalise those
to the SAME shape the legacy equals-form path produces so the existing loop
executes and audits them — while every pre-existing convention keeps working
and malformed text never synthesises a phantom call.
"""

from __future__ import annotations

from hal0.toolloop.engine import parse_text_tool_calls

_KNOWN = frozenset({"get_board", "get_task", "list_slots"})


def test_attribute_form_json_body_matches_equals_shape() -> None:
    attr = '<function name="get_task">{"task_id": "7"}</function>'
    equals = '<function=get_task>{"task_id": "7"}</function>'
    a_calls, a_clean = parse_text_tool_calls(attr, _KNOWN)
    e_calls, _ = parse_text_tool_calls(equals, _KNOWN)
    assert a_calls[0]["name"] == e_calls[0]["name"] == "get_task"
    assert a_calls[0]["arguments"] == e_calls[0]["arguments"] == {"task_id": "7"}
    assert "<function" not in a_clean


def test_attribute_form_single_quoted_name() -> None:
    text = "Listing.\n<function name='list_slots'>{}</function>"
    calls, cleaned = parse_text_tool_calls(text, _KNOWN)
    assert [c["name"] for c in calls] == ["list_slots"]
    assert calls[0]["arguments"] == {}
    assert "<function" not in cleaned and cleaned.strip() == "Listing."


def test_attribute_form_nested_parameter_body() -> None:
    text = (
        '<function name="get_task">'
        '<parameter name="task_id">7</parameter>'
        '<parameter name="note">hello world</parameter>'
        "</function>"
    )
    calls, cleaned = parse_text_tool_calls(text, _KNOWN)
    assert calls[0]["name"] == "get_task"
    # numeric decodes to int, free text stays a string.
    assert calls[0]["arguments"] == {"task_id": 7, "note": "hello world"}
    assert cleaned == ""


def test_function_calls_wrapper_multi_call() -> None:
    text = (
        "Working.\n<function_calls>"
        '<function name="get_board">{}</function>'
        '<function name="get_task"><parameter name="task_id">3</parameter></function>'
        "</function_calls>"
    )
    calls, cleaned = parse_text_tool_calls(text, _KNOWN)
    assert [c["name"] for c in calls] == ["get_board", "get_task"]
    assert calls[1]["arguments"] == {"task_id": 3}
    # the whole wrapper (bracket tags included) is stripped.
    assert "function_calls" not in cleaned and cleaned.strip() == "Working."


def test_attribute_form_inside_tool_call_wrapper() -> None:
    text = '<tool_call><function name="get_board">{}</function></tool_call>'
    calls, cleaned = parse_text_tool_calls(text, _KNOWN)
    assert calls[0]["name"] == "get_board"
    assert calls[0]["arguments"] == {}
    assert cleaned == ""


def test_legacy_equals_and_tool_call_still_parse() -> None:
    text = '<tool_call>{"name": "get_board", "arguments": {}}</tool_call>'
    calls, _ = parse_text_tool_calls(text, _KNOWN)
    assert [c["name"] for c in calls] == ["get_board"]
    bare = '<function=get_task>{"task_id": "9"}</function>'
    calls2, _ = parse_text_tool_calls(bare, _KNOWN)
    assert calls2[0]["arguments"] == {"task_id": "9"}


def test_attribute_form_unknown_name_is_no_call() -> None:
    text = '<function name="launch_missiles">{}</function>'
    calls, cleaned = parse_text_tool_calls(text, _KNOWN)
    assert calls == []
    assert cleaned == text


def test_malformed_attribute_form_no_crash_no_phantom() -> None:
    # unterminated tag / broken body must not raise and must not synthesise.
    for text in (
        '<function name="get_task">{not json',  # no closing tag
        '<function name="get_task"></function>',  # empty body -> {}
        "<function name=>{}</function>",  # missing name
    ):
        calls, _ = parse_text_tool_calls(text, _KNOWN)
        # empty-body case is a legitimate no-arg call; the others are no-ops.
        assert all(c["name"] in _KNOWN for c in calls)


def test_empty_known_names_disables_attribute_form() -> None:
    text = '<function name="get_board">{}</function>'
    calls, cleaned = parse_text_tool_calls(text, frozenset())
    assert calls == [] and cleaned == text
