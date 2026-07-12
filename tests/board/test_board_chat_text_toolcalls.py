"""Tolerant text tool-call parsing for the board chat.

A chat slot whose llama-server runs WITHOUT ``--jinja`` never parses the tool
grammar, so a model emits its tool call as text (leaking into the bubble) rather
than the OpenAI-native ``tool_calls`` field. ``_parse_text_tool_calls`` recovers
those — but only when the extracted name is a real surfaced tool, so ordinary
prose is never mistaken for a call.
"""

from __future__ import annotations

from hal0.api.routes import board_chat as bc

_KNOWN = frozenset({"get_board", "get_task"})


def test_tool_call_xml_block_json_body() -> None:
    text = 'Sure.\n<tool_call>{"name": "get_board", "arguments": {}}</tool_call>'
    calls, cleaned = bc._parse_text_tool_calls(text, _KNOWN)
    assert [c["name"] for c in calls] == ["get_board"]
    assert calls[0]["arguments"] == {}
    assert "<tool_call>" not in cleaned and cleaned.strip() == "Sure."


def test_nested_function_tag_with_args() -> None:
    text = '<tool_call><function=get_task>{"task_id": "7"}</function></tool_call>'
    calls, cleaned = bc._parse_text_tool_calls(text, _KNOWN)
    assert calls[0]["name"] == "get_task"
    assert calls[0]["arguments"] == {"task_id": "7"}
    assert cleaned == ""


def test_fenced_json_block() -> None:
    text = 'Let me look.\n```json\n{"name": "get_board", "arguments": {}}\n```'
    calls, cleaned = bc._parse_text_tool_calls(text, _KNOWN)
    assert calls[0]["name"] == "get_board"
    assert "```" not in cleaned


def test_whole_content_json_object() -> None:
    text = '{"name": "get_board", "arguments": {}}'
    calls, cleaned = bc._parse_text_tool_calls(text, _KNOWN)
    assert calls[0]["name"] == "get_board"
    assert cleaned == ""


def test_unknown_tool_name_is_not_a_call() -> None:
    # Safety: prose that happens to look call-ish but names no real tool must
    # pass through untouched — no false-positive tool execution.
    text = '<tool_call>{"name": "launch_missiles", "arguments": {}}</tool_call>'
    calls, cleaned = bc._parse_text_tool_calls(text, _KNOWN)
    assert calls == []
    assert cleaned == text


def test_plain_prose_passes_through() -> None:
    text = "The board has three columns: todo, doing, done."
    calls, cleaned = bc._parse_text_tool_calls(text, _KNOWN)
    assert calls == []
    assert cleaned == text


def test_call_with_surrounding_prose_keeps_prose() -> None:
    text = 'Checking the board now. <tool_call>{"name":"get_board","arguments":{}}</tool_call> One sec.'
    calls, cleaned = bc._parse_text_tool_calls(text, _KNOWN)
    assert [c["name"] for c in calls] == ["get_board"]
    assert "Checking the board now." in cleaned and "One sec." in cleaned
    assert "get_board" not in cleaned


def test_empty_known_names_disables_parsing() -> None:
    text = '<tool_call>{"name": "get_board", "arguments": {}}</tool_call>'
    calls, cleaned = bc._parse_text_tool_calls(text, frozenset())
    assert calls == [] and cleaned == text
