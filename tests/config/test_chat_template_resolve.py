"""Tests for chat_template resolution.

ADR-0029: the chat template is a fact about the model file, so
``resolve_chat_template`` reads only the model's ``defaults.chat_template``.
A slot has no chat-template key any more (the slot write paths reject it
with ``slot.tuning_key_denied`` and an unconverted slot file fails to load).
"""

from hal0.config.schema import resolve_chat_template


def test_model_template_id_used():
    assert resolve_chat_template({"defaults": {"chat_template": "chatml"}}) == "chatml"


def test_auto_means_the_gguf_embedded_template():
    assert resolve_chat_template({"defaults": {"chat_template": "auto"}}) is None


def test_unset_means_the_gguf_embedded_template():
    assert resolve_chat_template({}) is None
    assert resolve_chat_template({"defaults": {}}) is None
    assert resolve_chat_template({"defaults": {"chat_template": ""}}) is None
    assert resolve_chat_template({"defaults": None}) is None
