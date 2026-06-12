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
