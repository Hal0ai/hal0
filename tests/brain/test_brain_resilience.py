"""Resilience of the hal0-brain steward chat (KB-2/3 §5, invariants 5-7).

Pins the graceful-degradation behaviors the spec documents:

  * an undecided approval gate times out WITHOUT executing the gated call, and
    the model's next-round tool message carries the documented "still pending"
    hint;
  * the tool loop survives a tool raising — the exception becomes an
    ``{"error": ...}`` tool result the model reacts to, and the turn continues;
  * the brain-slot fallback chain: ``_resolve_profile`` degrades to the built-in
    prompt + ``BRAIN_SLOT_MODEL`` (``hal0/brain``, the virtual slot the resolver
    degrades to ``agent``) when no persona resolves, and a brain-slot transport
    failure surfaces as the documented ``error`` + ``done`` frames.

Scripted stub LLM, no network. See docs/rework/hal0-specs/spec-kb23-brain-tools.md.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hal0.api.routes import board_chat as bc
from hal0.config.schema import BrainChatConfig, Hal0Config
from hal0.mcp import admin
from hal0.mcp.approval_queue import ApprovalQueue

# ── harness ──────────────────────────────────────────────────────────────────


class _StubLLM:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(body))
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
    def __init__(self, *, board_result: Any = None, raise_on: str | None = None) -> None:
        self.board_result = board_result if board_result is not None else {"columns": []}
        self.raise_on = raise_on
        self.calls: list[tuple[str, str]] = []

    async def request_json(self, method: str, path: str, **kw: Any) -> Any:
        self.calls.append((method, path))
        if self.raise_on and self.raise_on in path:
            raise RuntimeError("kanban backend exploded")
        return self.board_result if path == "/board" else {"ok": True}


class _RestRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kw) -> dict[str, Any]:
        self.calls.append(kw)
        return {"ok": True}


def _fake_request(
    stub: _StubLLM,
    *,
    kanban: _FakeKanban | None = None,
    queue: ApprovalQueue | None = None,
    persona_root: Path | None = None,
) -> Any:
    state = SimpleNamespace(
        board_chat_llm=stub,
        hermes_kanban=kanban if kanban is not None else _FakeKanban(),
        approval_queue=queue if queue is not None else ApprovalQueue(),
        self_api_base_url="http://testserver",
        brain_persona_root=persona_root or Path("/nonexistent-personas-root"),
        memory_dispatcher=None,
        hal0_config=Hal0Config(brain_chat=BrainChatConfig(read_only=False)),
        audit=None,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state), headers={})


def _parse(frame: str) -> dict[str, Any]:
    assert frame.startswith("data: ")
    return json.loads(frame[len("data: ") :].strip())


async def _collect(request: Any, text: str = "go") -> list[dict[str, Any]]:
    payload = {"messages": [{"role": "user", "content": text}]}
    return [_parse(f) async for f in bc._chat_stream(request, payload)]


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ── #5 undecided approval gate times out; nothing executes ───────────────────


def test_undecided_gate_times_out_without_executing(monkeypatch) -> None:
    rest = _RestRecorder()
    monkeypatch.setattr(admin, "_call_rest", rest)
    monkeypatch.setattr(bc, "_APPROVAL_WAIT_S", 0.15)
    monkeypatch.setattr(bc, "_APPROVAL_POLL_S", 0.02)
    queue = ApprovalQueue()
    stub = _StubLLM([_tool_call("model_delete", {"model_id": "x"}, "c1"), _final("parked")])
    request = _fake_request(stub, queue=queue)

    events = _run(_collect(request))
    assert events[-1]["type"] == "done"
    # The gated call NEVER executed and stays pending for later operator action.
    assert rest.calls == []
    assert [p["tool"] for p in queue.list_pending()] == ["model_delete"]
    # The tool message the model saw next round carries the documented hint.
    tool_msg = next(m for m in stub.calls[-1]["messages"] if m.get("role") == "tool")
    assert "still pending" in tool_msg["content"]


# ── #6 the loop survives a tool raising ──────────────────────────────────────


def test_tool_loop_survives_a_tool_raising() -> None:
    """A read that raises becomes an {"error": ...} tool result; the turn
    continues to a normal completion instead of crashing the stream."""
    kanban = _FakeKanban(raise_on="/board")  # get_board will raise
    stub = _StubLLM([_tool_call("get_board", {}, "c1"), _final("recovered")])
    request = _fake_request(stub, kanban=kanban)

    events = _run(_collect(request))
    result = next(e for e in events if e["type"] == "tool_result")
    assert "error" in result["result"]
    assert "exploded" in result["result"]["error"]
    # The loop kept going: a second LLM round ran and the turn ended cleanly.
    assert len(stub.calls) == 2
    assert {"type": "token", "text": "recovered"} in events
    assert events[-1] == {"type": "done"}


def test_mutation_raising_is_caught_and_audited_as_error() -> None:
    """A mutation that raises inside dispatch is surfaced as an error tool
    result (not an unhandled exception)."""
    kanban = _FakeKanban(raise_on="/tasks/")  # move_task PATCH will raise
    stub = _StubLLM(
        [_tool_call("move_task", {"task_id": "t1", "status": "done"}, "c1"), _final("noted")]
    )
    request = _fake_request(stub, kanban=kanban)

    events = _run(_collect(request))
    result = next(e for e in events if e["type"] == "tool_result")
    assert "error" in result["result"]
    assert events[-1] == {"type": "done"}


# ── #7 brain-slot fallback chain ─────────────────────────────────────────────


def test_resolve_profile_falls_back_when_persona_absent() -> None:
    """No hal0-brain persona -> built-in prompt + BRAIN_SLOT_MODEL anchor."""
    request = _fake_request(_StubLLM([_final("x")]))
    prompt, model = bc._resolve_profile(request)
    assert prompt == bc._SYSTEM_PROMPT
    assert model == bc.BRAIN_SLOT_MODEL == "hal0/brain"


def test_default_completion_drives_the_fallback_capable_brain_slot() -> None:
    """With no per-request / config / persona model, the turn drives
    ``hal0/brain`` — the virtual slot whose resolver chain degrades to the
    ``agent`` slot when no brain slot is loaded."""
    stub = _StubLLM([_final("hello")])
    request = _fake_request(stub)
    _run(_collect(request))
    assert stub.calls[0]["model"] == "hal0/brain"


def test_brain_slot_transport_failure_surfaces_error_and_done() -> None:
    """A brain-slot transport error (the injected LLM returns an error
    envelope) ends the stream with the documented error + done frames, not a
    crash."""
    stub = _StubLLM([{"error": "primary slot transport failure: connection refused"}])
    request = _fake_request(stub)
    events = _run(_collect(request))
    err = next(e for e in events if e["type"] == "error")
    assert "transport failure" in err["message"]
    assert events[-1] == {"type": "done"}


@pytest.mark.asyncio
async def test_chat_stream_errors_when_board_backend_missing() -> None:
    """No hermes_kanban wired at all -> a clean error + done, never a crash."""
    state = SimpleNamespace(
        hermes_kanban=None,
        hal0_config=Hal0Config(brain_chat=BrainChatConfig()),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state), headers={})
    frames = [_parse(f) async for f in bc._chat_stream(request, {"messages": []})]
    assert frames[0]["type"] == "error"
    assert "not configured" in frames[0]["message"]
    assert frames[-1] == {"type": "done"}
