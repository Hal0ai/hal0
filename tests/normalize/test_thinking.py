import pytest

from hal0.normalize.thinking import apply_thinking_policy, template_collides_with_grammar


def test_injects_chat_template_kwargs_enable_thinking_false_by_default():
    out = apply_thinking_policy({"model": "m", "messages": []})
    assert out["chat_template_kwargs"]["enable_thinking"] is False
    # never sets the top-level field (legacy /no_think injection was ineffective)
    assert "enable_thinking" not in out


def test_top_level_enable_thinking_true_translated_to_kwarg():
    # Top-level boolean is the common/standard field; we translate it to the
    # lever Qwen3 honors and drop the ineffective top-level key.
    out = apply_thinking_policy({"enable_thinking": True})
    assert out["chat_template_kwargs"]["enable_thinking"] is True
    assert "enable_thinking" not in out


def test_top_level_enable_thinking_false_translated_to_kwarg():
    # The bug this fixes: top-level enable_thinking:false used to pass
    # through to an ineffective /no_think; now it suppresses via the kwarg.
    out = apply_thinking_policy({"enable_thinking": False})
    assert out["chat_template_kwargs"]["enable_thinking"] is False
    assert "enable_thinking" not in out


def test_top_level_thinking_bool_translated():
    out = apply_thinking_policy({"thinking": True})
    assert out["chat_template_kwargs"]["enable_thinking"] is True
    assert "thinking" not in out


def test_opt_out_thinking_dict_field_preserved():
    # Anthropic-style extended-thinking object is an explicit opt-in; untouched.
    out = apply_thinking_policy({"thinking": {"type": "enabled"}})
    assert "chat_template_kwargs" not in out
    assert out["thinking"] == {"type": "enabled"}


def test_opt_out_chat_template_kwargs_preserved():
    body = {"chat_template_kwargs": {"enable_thinking": True}}
    out = apply_thinking_policy(body)
    assert out["chat_template_kwargs"] == {"enable_thinking": True}


def test_preserves_sibling_chat_template_kwargs():
    out = apply_thinking_policy({"chat_template_kwargs": {"add_generation_prompt": True}})
    assert out["chat_template_kwargs"]["add_generation_prompt"] is True
    assert out["chat_template_kwargs"]["enable_thinking"] is False


def test_no_think_marker_passthrough_untouched():
    body = {"messages": [{"role": "user", "content": "/no_think hi"}], "no_think": True}
    out = apply_thinking_policy(body)
    assert out["no_think"] is True
    assert out["chat_template_kwargs"]["enable_thinking"] is False


def test_idempotent():
    once = apply_thinking_policy({"model": "m"})
    twice = apply_thinking_policy(once)
    assert once == twice


def test_idempotent_after_top_level_translation():
    once = apply_thinking_policy({"enable_thinking": False})
    twice = apply_thinking_policy(once)
    assert once == twice


def test_per_slot_default_thinking_true():
    # Per-slot default (slot TOML enable_thinking=true) makes reasoning the
    # default for that slot when the caller expresses no preference.
    out = apply_thinking_policy({"model": "m"}, default_thinking=True)
    assert out["chat_template_kwargs"]["enable_thinking"] is True


def test_per_slot_default_overridden_by_caller():
    # An explicit per-request preference always wins over the slot default.
    out = apply_thinking_policy({"enable_thinking": False}, default_thinking=True)
    assert out["chat_template_kwargs"]["enable_thinking"] is False


def test_per_slot_default_false_is_baseline():
    out = apply_thinking_policy({"model": "m"}, default_thinking=False)
    assert out["chat_template_kwargs"]["enable_thinking"] is False


def test_does_not_mutate_input():
    src = {"model": "m"}
    apply_thinking_policy(src)
    assert "chat_template_kwargs" not in src

    src2 = {"enable_thinking": False}
    apply_thinking_policy(src2)
    assert src2 == {"enable_thinking": False}


# --- #2020 / #2713: the grammar exemption is scoped to the colliding template ---
#
# The seeded hal0-brain-sft template emits a literal `<think>\n\n</think>` block
# when `enable_thinking is false`; combined with a json_object/json_schema
# grammar llama-server b109 throws "Failed to initialize samplers" and hard-400s.
# On THAT template (grammar_collides=True) a caller that never asked for
# reasoning suppression must not be given it. Every other template gets the
# normal default suppression (#2713: Qwen3-family reasoning otherwise eats the
# whole completion budget and structured output never arrives).

_GRAMMAR_BODIES = [
    {"model": "m", "messages": [], "response_format": {"type": "json_object"}},
    {
        "model": "m",
        "response_format": {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}},
    },
    {"model": "m", "grammar": 'root ::= "a"'},
    {"model": "m", "json_schema": {"type": "object"}},
]


@pytest.mark.parametrize("body", _GRAMMAR_BODIES)
def test_colliding_template_grammar_skips_default_suppression(body):
    out = apply_thinking_policy(body, grammar_collides=True)
    assert "chat_template_kwargs" not in out
    assert out == body


@pytest.mark.parametrize("body", _GRAMMAR_BODIES)
def test_non_colliding_template_grammar_gets_default_suppression(body):
    # #2713: a Qwen3-family template (anything not known to collide) is
    # suppressed under a grammar exactly as without one.
    out = apply_thinking_policy(body)
    assert out["chat_template_kwargs"]["enable_thinking"] is False


@pytest.mark.parametrize("body", _GRAMMAR_BODIES)
def test_explicit_operator_off_honoured_on_colliding_template(body):
    # #2713: a profile `enable_thinking = false` is the operator's explicit
    # choice; it is applied even on the colliding template (a loud 400 beats a
    # silent zero-fact retain).
    out = apply_thinking_policy(body, default_thinking=False, grammar_collides=True)
    assert out["chat_template_kwargs"]["enable_thinking"] is False


def test_template_collides_with_grammar():
    assert template_collides_with_grammar("hal0-brain-sft") is True
    assert template_collides_with_grammar("qwen3.6-27b-mtp") is False
    assert template_collides_with_grammar("chatml") is False
    assert template_collides_with_grammar(None) is False
    assert template_collides_with_grammar("") is False


def test_text_response_format_still_suppressed():
    # `{"type": "text"}` engages no grammar — the normal default still applies.
    out = apply_thinking_policy({"model": "m", "response_format": {"type": "text"}})
    assert out["chat_template_kwargs"]["enable_thinking"] is False


def test_structured_output_respects_explicit_caller_suppression():
    # An explicit caller request is honoured verbatim, json mode or not — the
    # policy only declines to *invent* a suppression the caller never asked for.
    body = {
        "model": "m",
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    assert apply_thinking_policy(body, grammar_collides=True) == body

    out = apply_thinking_policy(
        {"model": "m", "response_format": {"type": "json_object"}, "enable_thinking": False},
        grammar_collides=True,
    )
    assert out["chat_template_kwargs"]["enable_thinking"] is False


def test_structured_output_still_honours_thinking_default_true():
    # enable_thinking=true is grammar-compatible (verified live on the seeded
    # model), so a slot/model default of ON is still applied under json mode.
    out = apply_thinking_policy(
        {"model": "m", "response_format": {"type": "json_object"}},
        default_thinking=True,
        grammar_collides=True,
    )
    assert out["chat_template_kwargs"]["enable_thinking"] is True


def test_structured_output_policy_is_idempotent():
    body = {"model": "m", "response_format": {"type": "json_object"}}
    for collides in (True, False):
        once = apply_thinking_policy(body, grammar_collides=collides)
        assert apply_thinking_policy(once, grammar_collides=collides) == once


def test_malformed_response_format_falls_back_to_default():
    # Junk in response_format must not crash the normaliser.
    out = apply_thinking_policy({"model": "m", "response_format": "json_object"})
    assert out["chat_template_kwargs"]["enable_thinking"] is False
