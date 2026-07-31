"""The brain turn's completion budget has a floor (v1.0, Stream A).

The brain model is a thinking model: it emits ``reasoning_content`` FIRST and
draws it from the SAME ``max_tokens`` budget as the visible answer. Measured on
a GPU box with ``max_tokens: 80``, the whole budget went to reasoning and
``content`` came back as the empty string with ``finish_reason: stop`` — a
well-formed, "successful", COMPLETELY BLANK steward reply.

That is unrecoverable downstream: :func:`hal0.toolloop.engine.run_tool_loop`
only emits a ``token`` frame when the visible text is non-empty, so the user
gets a collapsed "thinking" disclosure followed by silence, and reads it as the
feature being broken.

So a caller-supplied ``max_tokens`` is a REQUEST, not a contract. Below the
floor it is raised; above it, honoured verbatim.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from hal0.brain.chat import (
    _MAX_COMPLETION_TOKENS,
    _MIN_COMPLETION_TOKENS,
    _completion_budget,
)

# ── the resolver ────────────────────────────────────────────────────────────


def test_a_tiny_budget_is_raised_to_the_floor() -> None:
    """80 tokens is the exact value that produced a blank reply in the field."""
    assert _completion_budget(80) == _MIN_COMPLETION_TOKENS


@pytest.mark.parametrize("n", [1, 16, 80, _MIN_COMPLETION_TOKENS - 1])
def test_every_sub_floor_budget_is_raised(n: int) -> None:
    assert _completion_budget(n) == _MIN_COMPLETION_TOKENS


def test_the_floor_leaves_real_room_for_an_answer() -> None:
    """A floor of ~0 would satisfy the letter of the fix and none of the point.

    The measured failure burned 80 tokens on reasoning alone, so the floor has
    to be several times that before "reasoning finishes AND an answer fits" is
    a claim rather than a hope.
    """
    assert _MIN_COMPLETION_TOKENS >= 256
    assert _MIN_COMPLETION_TOKENS < _MAX_COMPLETION_TOKENS


@pytest.mark.parametrize("absent", [None, 0, "", "0"])
def test_an_absent_budget_still_gets_the_default_cap(absent: Any) -> None:
    """Unchanged behaviour — the floor must not become the new default."""
    assert _completion_budget(absent) == _MAX_COMPLETION_TOKENS


@pytest.mark.parametrize("junk", ["lots", [], {}, object()])
def test_junk_falls_back_to_the_default_cap_instead_of_raising(junk: Any) -> None:
    """A malformed body must not 500 a chat turn."""
    assert _completion_budget(junk) == _MAX_COMPLETION_TOKENS


def test_a_negative_budget_falls_back_to_the_default_cap() -> None:
    assert _completion_budget(-1) == _MAX_COMPLETION_TOKENS


def test_a_generous_budget_is_honoured_verbatim() -> None:
    """The floor clamps UP only. A caller asking for more room is never
    second-guessed — including above the default cap."""
    assert _completion_budget(_MIN_COMPLETION_TOKENS + 1) == _MIN_COMPLETION_TOKENS + 1
    assert _completion_budget(2048) == 2048
    assert _completion_budget(_MAX_COMPLETION_TOKENS * 4) == _MAX_COMPLETION_TOKENS * 4


def test_a_numeric_string_budget_is_accepted() -> None:
    """JSON bodies in the wild carry ``"max_tokens": "80"``."""
    assert _completion_budget("80") == _MIN_COMPLETION_TOKENS
    assert _completion_budget("2048") == 2048


# ── end to end through the route ────────────────────────────────────────────


def _final_response(text: str, *, reasoning: str = "") -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": text}
    if reasoning:
        msg["reasoning_content"] = reasoning
    return {"choices": [{"message": msg, "finish_reason": "stop"}]}


class _StubLLM:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(json.loads(json.dumps(body, default=str)))
        return self._responses.pop(0) if self._responses else _final_response("done")


@pytest.fixture()
def app_client(tmp_path):
    from tests.board.test_board_chat import _make_app, _Recorder

    def _build(stub: _StubLLM):
        return _make_app(_Recorder(), stub, tmp_path)

    return _build


def test_a_starved_request_reaches_the_slot_with_the_floor(app_client) -> None:
    """The regression itself: `max_tokens: 80` must not reach llama-server."""
    stub = _StubLLM([_final_response("hi")])
    _app, client = app_client(stub)
    client.post(
        "/api/board/chat",
        json={"max_tokens": 80, "messages": [{"role": "user", "content": "x"}]},
    )
    assert stub.calls, "the LLM was never called"
    assert stub.calls[0]["max_tokens"] == _MIN_COMPLETION_TOKENS


def test_an_unspecified_request_still_gets_the_default_cap(app_client) -> None:
    stub = _StubLLM([_final_response("hi")])
    _app, client = app_client(stub)
    client.post("/api/board/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert stub.calls[0]["max_tokens"] == _MAX_COMPLETION_TOKENS


def test_reasoning_content_is_surfaced_as_a_collapsed_thinking_frame(app_client) -> None:
    """The reasoning is DISCLOSED, not discarded — and not inlined into the
    answer either. The floor exists so both frames can be non-empty."""
    from tests.board.test_board_chat import _sse_events

    stub = _StubLLM([_final_response("the answer", reasoning="let me think about it")])
    _app, client = app_client(stub)
    r = client.post(
        "/api/board/chat",
        json={"max_tokens": 80, "messages": [{"role": "user", "content": "x"}]},
    )
    events = _sse_events(r.text)
    thinking = [e for e in events if e.get("type") == "thinking"]
    tokens = [e for e in events if e.get("type") == "token"]
    assert thinking and thinking[0]["text"] == "let me think about it"
    assert tokens and tokens[0]["text"] == "the answer"
    assert "let me think" not in tokens[0]["text"]
