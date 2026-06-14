"""Tests for chat_template resolution: slot override > model default > auto(None)."""

from hal0.config.schema import resolve_chat_template


def test_slot_override_wins():
    assert (
        resolve_chat_template({"chat_template": "qwen3"}, {"defaults": {"chat_template": "chatml"}})
        == "qwen3"
    )


def test_model_default_used_when_slot_absent():
    assert resolve_chat_template({}, {"defaults": {"chat_template": "chatml"}}) == "chatml"


def test_auto_returns_none():
    assert resolve_chat_template({"chat_template": "auto"}, {}) is None
    assert resolve_chat_template({}, {}) is None
