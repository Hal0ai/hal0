"""O18: outbound message framing satisfies chat-template user-query guards.

A completion request with no user-role turn trips templates like qwen3.5's
``multi_step_tool`` (500 "No user query found in messages"). The steward must
frame every round template-safely: the first round ends with the user message,
tool-continuation rounds carry a template-safe (system → user → assistant →
tool) shape, and no round ever emits a messages list with zero user-role
entries. These tests assert the exact wire shape the LLM backend receives.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from hal0.brain import chat as bc
from hal0.config.schema import BrainChatConfig, Hal0Config
from hal0.mcp.approval_queue import ApprovalQueue


class _RecordingLLM:
    """Pops a canned completion per call; snapshots the round's messages."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.rounds: list[list[dict[str, Any]]] = []

    async def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        # Deep-copy: the engine mutates body["messages"] in place across rounds.
        self.rounds.append(copy.deepcopy(body["messages"]))
        return self._responses.pop(0) if self._responses else _final("done")


def _tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ],
                }
            }
        ]
    }


def _final(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class _FakeKanban:
    async def request_json(self, method: str, path: str, **kw: Any) -> Any:
        return {"columns": []}


def _request(stub: Any) -> Any:
    state = SimpleNamespace(
        board_chat_llm=stub,
        hermes_kanban=_FakeKanban(),
        approval_queue=ApprovalQueue(),
        self_api_base_url="http://testserver",
        brain_persona_root=Path("/nonexistent-personas-root"),
        memory_dispatcher=None,
        hal0_config=Hal0Config(brain_chat=BrainChatConfig(read_only=False)),
        audit=None,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state), headers={})


def _drive(request: Any, payload: dict[str, Any]) -> None:
    async def _run() -> None:
        async for _ in bc._chat_stream(request, payload):
            pass

    asyncio.new_event_loop().run_until_complete(_run())


def _roles(messages: list[dict[str, Any]]) -> list[str]:
    return [m.get("role") for m in messages]


# ── _frame_messages unit shape ───────────────────────────────────────────────


def test_frame_messages_folds_singular_message() -> None:
    # The O18 repro: {"message": "..."} was dropped, leaving a system-only turn.
    framed = bc._frame_messages({"message": "hello"}, "SYS")
    assert _roles(framed) == ["system", "user"]
    assert framed[0]["content"] == "SYS"
    assert framed[-1] == {"role": "user", "content": "hello"}


def test_frame_messages_keeps_explicit_messages() -> None:
    framed = bc._frame_messages({"messages": [{"role": "user", "content": "hi"}]}, "SYS")
    assert _roles(framed) == ["system", "user"]
    assert framed[-1]["content"] == "hi"


def test_frame_messages_respects_client_system() -> None:
    framed = bc._frame_messages(
        {"messages": [{"role": "system", "content": "OWN"}, {"role": "user", "content": "hi"}]},
        "SYS",
    )
    assert _roles(framed) == ["system", "user"]
    assert framed[0]["content"] == "OWN"  # client's system kept, not double-seeded


def test_frame_messages_guarantees_a_user_turn() -> None:
    # Empty payload must not yield a system-only (user-less) turn.
    framed = bc._frame_messages({}, "SYS")
    assert "user" in _roles(framed)


# ── first-round wire shape through the loop ──────────────────────────────────


def test_singular_message_first_round_ends_with_user() -> None:
    stub = _RecordingLLM([_final("ok")])
    _drive(_request(stub), {"message": "what is blocked?"})
    first = stub.rounds[0]
    assert _roles(first) == ["system", "user"]
    assert first[-1]["role"] == "user"
    assert first[-1]["content"] == "what is blocked?"


def test_messages_first_round_ends_with_user() -> None:
    stub = _RecordingLLM([_final("ok")])
    _drive(_request(stub), {"messages": [{"role": "user", "content": "hi"}]})
    first = stub.rounds[0]
    assert first[-1]["role"] == "user"


# ── tool-continuation round is template-safe ─────────────────────────────────


def test_tool_continuation_round_shape_is_template_safe() -> None:
    stub = _RecordingLLM([_tool_call("get_board", {}, "c1"), _final("done")])
    _drive(_request(stub), {"message": "show the board"})
    assert len(stub.rounds) == 2  # a tool round then the follow-up completion
    # Round 1: system → user.
    assert _roles(stub.rounds[0]) == ["system", "user"]
    # Round 2: the loop appended the assistant tool-call turn + the tool result;
    # the earlier user turn persists, so it stays a valid alternating shape.
    assert _roles(stub.rounds[1]) == ["system", "user", "assistant", "tool"]


def test_no_round_emits_zero_user_role_entries() -> None:
    stub = _RecordingLLM([_tool_call("get_board", {}, "c1"), _final("done")])
    _drive(_request(stub), {"message": "show the board"})
    for round_messages in stub.rounds:
        assert any(m.get("role") == "user" for m in round_messages)
