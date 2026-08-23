from hal0.normalize.thinking import apply_thinking_policy


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


# --- #2020: structured output must not get an unrequested suppression kwarg ---
#
# The seeded hal0-brain-sft template emits a literal `<think>\n\n</think>` block
# when `enable_thinking is false`; combined with a json_object/json_schema
# grammar llama-server b109 throws "Failed to initialize samplers" and hard-400s.
# A caller that never asked for reasoning suppression must not be given it.


def test_json_object_response_format_skips_default_suppression():
    body = {"model": "m", "messages": [], "response_format": {"type": "json_object"}}
    out = apply_thinking_policy(body)
    assert "chat_template_kwargs" not in out
    assert out == body


def test_json_schema_response_format_skips_default_suppression():
    body = {
        "model": "m",
        "response_format": {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}},
    }
    out = apply_thinking_policy(body)
    assert "chat_template_kwargs" not in out


def test_llamacpp_grammar_skips_default_suppression():
    out = apply_thinking_policy({"model": "m", "grammar": 'root ::= "a"'})
    assert "chat_template_kwargs" not in out


def test_llamacpp_top_level_json_schema_skips_default_suppression():
    out = apply_thinking_policy({"model": "m", "json_schema": {"type": "object"}})
    assert "chat_template_kwargs" not in out


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
    assert apply_thinking_policy(body) == body

    out = apply_thinking_policy(
        {"model": "m", "response_format": {"type": "json_object"}, "enable_thinking": False}
    )
    assert out["chat_template_kwargs"]["enable_thinking"] is False


def test_structured_output_still_honours_thinking_default_true():
    # enable_thinking=true is grammar-compatible (verified live on the seeded
    # model), so a slot/model default of ON is still applied under json mode.
    out = apply_thinking_policy(
        {"model": "m", "response_format": {"type": "json_object"}}, default_thinking=True
    )
    assert out["chat_template_kwargs"]["enable_thinking"] is True


def test_structured_output_policy_is_idempotent():
    once = apply_thinking_policy({"model": "m", "response_format": {"type": "json_object"}})
    assert apply_thinking_policy(once) == once


def test_malformed_response_format_falls_back_to_default():
    # Junk in response_format must not crash the normaliser.
    out = apply_thinking_policy({"model": "m", "response_format": "json_object"})
    assert out["chat_template_kwargs"]["enable_thinking"] is False
