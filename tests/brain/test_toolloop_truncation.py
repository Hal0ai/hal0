"""Truncated tool rounds — ``finish_reason: "length"`` handling (#1598).

Observed live on lxc105 (v1.0.0-rc.1): steward chats "stopping after one
reply" — the visible text cut off right after the intent prose ("Let me check
what models are available in the catalog:") with no tool call, no error, and a
clean ``done``. The mechanism: on a thinking model the reasoning is drawn from
the SAME completion budget as the visible content (see
``_MIN_COMPLETION_TOKENS`` in brain/chat.py), so a long chain of thought can
exhaust ``max_tokens`` before the tool-call syntax is emitted. The loop never
consulted ``finish_reason``, so the truncated fragment was indistinguishable
from a complete reply and was finalized as one.

What this file locks down:

  * a ``length`` round with no tool calls is retried ONCE with a doubled
    budget, and the truncated fragment is NOT streamed to the operator;
  * a retry that completes normally proceeds as if the truncation never
    happened (tool calls run, budget stays raised for the rest of the turn);
  * a second consecutive truncation finalizes WITH the visible cut-note —
    never a silent mid-sentence stop;
  * ``finish_reason: "stop"`` rounds are untouched (no retry, no note);
  * a truncated round that still carried a parseable tool call executes it
    (the call is the round's product; the budget question is moot).

Scripted stub LLM, no network.

Run targeted:
    uv run pytest tests/brain/test_toolloop_truncation.py -q
"""

from __future__ import annotations

import asyncio
from typing import Any

from hal0.toolloop.engine import run_tool_loop

TOOLS = [
    {
        "type": "function",
        "function": {"name": "list_models", "description": "list", "parameters": {}},
    }
]
KNOWN = frozenset({"list_models"})


def _completion(
    text: str,
    *,
    finish_reason: str = "stop",
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message, "finish_reason": finish_reason}]}


def _tool_call(name: str = "list_models") -> dict[str, Any]:
    return {"id": "tc-1", "type": "function", "function": {"name": name, "arguments": "{}"}}


class _ScriptedLlm:
    """Return each queued completion in order; record the body per call."""

    def __init__(self, completions: list[dict[str, Any]]) -> None:
        self._queue = list(completions)
        self.bodies: list[dict[str, Any]] = []

    async def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append({k: v for k, v in body.items() if k != "messages"})
        return self._queue.pop(0)


async def _dispatch(calls: list[dict[str, Any]]):
    for call in calls:
        yield {"type": "tool_call", "id": call["id"], "name": call["name"]}
        yield {"type": "tool_result", "id": call["id"], "name": call["name"], "result": {}}


def _drive(
    llm: _ScriptedLlm, *, max_tokens: Any = 4096, max_rounds: int = 4
) -> list[dict[str, Any]]:
    async def _run() -> list[dict[str, Any]]:
        body: dict[str, Any] = {"messages": [{"role": "user", "content": "hi"}]}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        events = []
        async for ev in run_tool_loop(
            llm,
            TOOLS,
            _dispatch,
            body=body,
            max_rounds=max_rounds,
            known_tool_names=KNOWN,
        ):
            events.append(ev)
        return events

    return asyncio.run(_run())


def _of_type(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e.get("type") == kind]


# ── the retry ────────────────────────────────────────────────────────────────


def test_truncated_round_retries_with_doubled_budget_and_hides_the_fragment() -> None:
    llm = _ScriptedLlm(
        [
            _completion("Let me check the catalog:", finish_reason="length"),
            _completion("Here is the full answer."),
        ]
    )

    events = _drive(llm, max_tokens=4096)

    tokens = [e["text"] for e in _of_type(events, "token")]
    assert tokens == ["Here is the full answer."], tokens  # fragment discarded
    assert len(llm.bodies) == 2
    assert llm.bodies[0]["max_tokens"] == 4096
    assert llm.bodies[1]["max_tokens"] == 8192  # doubled
    assert _of_type(events, "done")


def test_retry_budget_floor_applies_when_caller_budget_was_small() -> None:
    llm = _ScriptedLlm(
        [
            _completion("cut", finish_reason="length"),
            _completion("done now"),
        ]
    )

    _drive(llm, max_tokens=512)

    assert llm.bodies[1]["max_tokens"] == 8192  # floor, not 1024


def test_retry_handles_absent_max_tokens() -> None:
    llm = _ScriptedLlm(
        [
            _completion("cut", finish_reason="length"),
            _completion("done now"),
        ]
    )

    events = _drive(llm, max_tokens=None)

    assert llm.bodies[1]["max_tokens"] == 8192
    assert [e["text"] for e in _of_type(events, "token")] == ["done now"]


def test_retry_that_produces_a_tool_call_executes_it() -> None:
    llm = _ScriptedLlm(
        [
            _completion("Let me check:", finish_reason="length"),
            _completion("Checking.", tool_calls=[_tool_call()]),
            _completion("All models listed."),
        ]
    )

    events = _drive(llm)

    assert [e["name"] for e in _of_type(events, "tool_call")] == ["list_models"]
    assert [e["text"] for e in _of_type(events, "token")] == [
        "Checking.",
        "All models listed.",
    ]


# ── the concession ───────────────────────────────────────────────────────────


def test_second_truncation_finalizes_with_a_visible_cut_note() -> None:
    llm = _ScriptedLlm(
        [
            _completion("first cut", finish_reason="length"),
            _completion("second cut", finish_reason="length"),
        ]
    )

    events = _drive(llm)

    tokens = [e["text"] for e in _of_type(events, "token")]
    assert len(tokens) == 1
    assert tokens[0].startswith("second cut")
    assert "truncated" in tokens[0]  # never a silent mid-sentence stop
    assert _of_type(events, "done")
    assert len(llm.bodies) == 2  # exactly one retry, ever


# ── non-regression ───────────────────────────────────────────────────────────


def test_stop_rounds_are_untouched() -> None:
    llm = _ScriptedLlm([_completion("plain reply")])

    events = _drive(llm)

    assert [e["text"] for e in _of_type(events, "token")] == ["plain reply"]
    assert len(llm.bodies) == 1  # no retry


def test_truncated_round_with_a_parseable_call_still_executes_it() -> None:
    # The budget ran out AFTER the call syntax was emitted — the call is the
    # round's product; retrying would just re-buy an answer we already have.
    llm = _ScriptedLlm(
        [
            _completion("On it.", finish_reason="length", tool_calls=[_tool_call()]),
            _completion("Result summarised."),
        ]
    )

    events = _drive(llm)

    assert [e["name"] for e in _of_type(events, "tool_call")] == ["list_models"]
    assert len(llm.bodies) == 2  # round 2 is the continuation, not a retry
    assert llm.bodies[1]["max_tokens"] == 4096  # budget untouched
