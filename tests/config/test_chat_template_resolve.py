"""Tests for chat_template resolution.

FLAGS-own §7 (slot-purity fold): the chat template is model-intrinsic, so
``resolve_chat_template`` reads ONLY ``model.defaults.chat_template`` — the
per-slot override is sunset/inert at launch. The one-shot migrator
(``slot_flags_fold``) folds each slot's effective template into its model; the
launch reader no longer consults the slot tier.
"""

from hal0.config.schema import resolve_chat_template


def test_slot_override_is_inert_model_wins():
    """The sunset slot override no longer wins — the model default is the
    single source (spec §7). Pre-§7 this returned the slot's 'qwen3'."""
    assert (
        resolve_chat_template({"chat_template": "qwen3"}, {"defaults": {"chat_template": "chatml"}})
        == "chatml"
    )


def test_model_default_used():
    assert resolve_chat_template({}, {"defaults": {"chat_template": "chatml"}}) == "chatml"


def test_slot_override_alone_is_ignored():
    """A slot template with no model default folds to auto (None) at launch —
    the value must have been folded into the model by the migrator first."""
    assert resolve_chat_template({"chat_template": "qwen3"}, {}) is None


def test_auto_returns_none():
    assert resolve_chat_template({}, {"defaults": {"chat_template": "auto"}}) is None
    assert resolve_chat_template({}, {}) is None
