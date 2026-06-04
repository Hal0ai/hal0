from hal0.normalize.thinking import apply_thinking_policy


def test_injects_chat_template_kwargs_enable_thinking_false_by_default():
    out = apply_thinking_policy({"model": "m", "messages": []})
    assert out["chat_template_kwargs"]["enable_thinking"] is False
    # never sets the top-level field (lemond would /no_think-inject, ineffective)
    assert "enable_thinking" not in out


def test_opt_out_enable_thinking_preserved():
    out = apply_thinking_policy({"enable_thinking": True})
    assert out["enable_thinking"] is True
    assert "chat_template_kwargs" not in out


def test_opt_out_thinking_field_preserved():
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


def test_does_not_mutate_input():
    src = {"model": "m"}
    apply_thinking_policy(src)
    assert "chat_template_kwargs" not in src
