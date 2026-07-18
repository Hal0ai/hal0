"""Tests for ``hal0 chat`` (§21.14) — the terminal REPL over /v1/chat/completions.

Covers the three pieces plan §21.14 calls out explicitly:

* ``/think on|off|default`` — toggles :func:`hal0.normalize.thinking.apply_thinking_policy`
  (reused, not re-implemented) on the outgoing request body.
* Reasoning-token separation — :func:`hal0.toolloop.engine.split_thinking` /
  ``assistant_thinking`` (reused, not re-implemented) strip reasoning out of
  the assistant reply BEFORE it's folded back into ``ChatSession.history``.
* History handling across turns, and the ``--no-stream`` / streaming
  transport seam (mocked here — no live server).
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import chat_commands
from hal0.cli.chat_commands import ChatSession
from hal0.cli.main import app

runner = CliRunner()


# ── ChatSession.build_body / /think toggle ──────────────────────────────────


def test_set_think_accepts_valid_modes_only() -> None:
    session = ChatSession()
    assert session.think_mode == "default"

    assert session.set_think("on") is True
    assert session.think_mode == "on"

    assert session.set_think("OFF") is True  # case-insensitive
    assert session.think_mode == "off"

    assert session.set_think("default") is True
    assert session.think_mode == "default"

    assert session.set_think("bogus") is False
    assert session.think_mode == "default"  # unchanged on rejection


def test_build_body_default_think_mode_leaves_body_untouched() -> None:
    """think_mode='default' must NOT inject chat_template_kwargs — the
    server's own _normalize_chat_body applies the slot's default instead."""
    session = ChatSession(model="agent")
    body = session.build_body("hello")
    assert body["model"] == "agent"
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["stream"] is True
    assert "chat_template_kwargs" not in body


def test_build_body_think_on_forces_enable_thinking_true() -> None:
    session = ChatSession()
    session.set_think("on")
    body = session.build_body("hi")
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
    # top-level booleans are never sent — apply_thinking_policy drops them.
    assert "thinking" not in body
    assert "enable_thinking" not in body


def test_build_body_think_off_forces_enable_thinking_false() -> None:
    session = ChatSession()
    session.set_think("off")
    body = session.build_body("hi")
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_build_body_appends_user_turn_to_history() -> None:
    session = ChatSession()
    session.build_body("first")
    session.build_body("second")
    assert session.history == [
        {"role": "user", "content": "first"},
        {"role": "user", "content": "second"},
    ]


# ── reasoning-token separation / history strip ──────────────────────────────


def test_finish_assistant_turn_strips_inline_think_tags_from_history() -> None:
    session = ChatSession()
    session.build_body("question")
    thinking, visible = session.finish_assistant_turn(
        "<think>pondering the answer</think>the answer is 42", ""
    )
    assert thinking == "pondering the answer"
    assert visible == "the answer is 42"
    # history holds ONLY the stripped visible text — no reasoning leaks in.
    assert session.history[-1] == {"role": "assistant", "content": "the answer is 42"}


def test_finish_assistant_turn_combines_explicit_and_inline_reasoning() -> None:
    session = ChatSession()
    session.build_body("q")
    thinking, visible = session.finish_assistant_turn(
        "<think>inline reasoning</think>visible reply", "explicit reasoning_content"
    )
    assert thinking == "explicit reasoning_content\ninline reasoning"
    assert visible == "visible reply"
    assert session.history[-1]["content"] == "visible reply"


def test_finish_assistant_turn_no_reasoning_is_a_no_op_split() -> None:
    session = ChatSession()
    session.build_body("q")
    thinking, visible = session.finish_assistant_turn("plain reply, no reasoning", "")
    assert thinking == ""
    assert visible == "plain reply, no reasoning"


def test_history_never_grows_reasoning_across_multiple_turns() -> None:
    """The context-bloat guarantee: after N turns with reasoning on, history
    holds N user + N assistant messages, none of which carry think-tags."""
    session = ChatSession()
    for i in range(3):
        session.build_body(f"turn {i}")
        session.finish_assistant_turn(f"<think>reasoning {i}</think>reply {i}", "")
    assert len(session.history) == 6
    for msg in session.history:
        if msg["role"] == "assistant":
            assert "<think>" not in msg["content"]
            assert "reasoning" not in msg["content"]


# ── transport seam (mocked — no live server) ────────────────────────────────


class _FakeClient:
    """Stand-in for httpx.Client — the turn helpers only need a placeholder
    object to thread through; the real I/O is monkeypatched out below."""


def test_run_nonstreaming_turn_strips_reasoning_before_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(client: Any, url: str, body: dict[str, Any]) -> dict[str, Any]:
        assert url == "http://example.invalid/chat/completions"
        assert body["model"] == "agent"
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "<think>scratch work</think>final answer",
                        "reasoning_content": "explicit chain of thought",
                    }
                }
            ]
        }

    monkeypatch.setattr(chat_commands, "_post_completions", fake_post)

    session = ChatSession(model="agent", stream=False)
    chat_commands._run_nonstreaming_turn(
        session, _FakeClient(), "http://example.invalid/chat/completions", "what is it?"
    )

    assert session.history[-1] == {"role": "assistant", "content": "final answer"}
    assert "scratch work" not in session.history[-1]["content"]


def test_run_streaming_turn_accumulates_deltas_and_strips_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_stream(client: Any, url: str, body: dict[str, Any]):
        assert body["stream"] is True
        yield {"choices": [{"delta": {"reasoning_content": "thinking a bit… "}}]}
        yield {"choices": [{"delta": {"content": "hel"}}]}
        yield {"choices": [{"delta": {"content": "lo there"}}]}

    monkeypatch.setattr(chat_commands, "_iter_stream_events", fake_stream)

    session = ChatSession(model="agent", stream=True)
    chat_commands._run_streaming_turn(
        session, _FakeClient(), "http://example.invalid/chat/completions", "hey"
    )

    assert session.history[-1] == {"role": "assistant", "content": "hello there"}
    assert "thinking a bit" not in session.history[-1]["content"]


# ── CLI wiring / REPL loop ───────────────────────────────────────────────────


def test_chat_registered_on_the_root_app() -> None:
    names = {cmd.name for cmd in app.registered_commands}
    assert "chat" in names


def test_repl_think_and_clear_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(client: Any, url: str, body: dict[str, Any]) -> dict[str, Any]:
        calls.append(body)
        return {"choices": [{"message": {"content": "ack"}}]}

    monkeypatch.setattr(chat_commands, "_post_completions", fake_post)

    result = runner.invoke(
        app,
        ["chat", "--no-stream"],
        input="/think on\nhello\n/clear\n/quit\n",
    )

    assert result.exit_code == 0, result.output
    assert "(thinking: on)" in result.output
    assert "(history cleared)" in result.output
    assert len(calls) == 1
    assert calls[0]["chat_template_kwargs"] == {"enable_thinking": True}


def test_repl_exits_cleanly_on_eof() -> None:
    result = runner.invoke(app, ["chat"], input="")
    assert result.exit_code == 0, result.output
