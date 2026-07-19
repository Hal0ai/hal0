"""Pre-flight context guard: a prompt over the resolved slot's context window
emits the documented SSE ``error`` frame instead of burning a 400 round-trip.

The steward system prompt alone is ~7.3k tokens; a brain slot loaded at a small
context (the on-box chat@4096 incident) 400s the completion with
``exceed_context``. The guard estimates the assembled prompt against the
resolved slot's ``context_length`` and short-circuits with an actionable error.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from hal0.brain import chat as bc
from hal0.config.schema import BrainChatConfig, Hal0Config
from hal0.mcp.approval_queue import ApprovalQueue
from hal0.normalize.resolver import SlotView


class _RecordingLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}


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
        slot_manager=None,
        hal0_config=Hal0Config(brain_chat=BrainChatConfig(read_only=False)),
        audit=None,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state), headers={})


def _drive(request: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    frames: list[dict[str, Any]] = []

    async def _run() -> None:
        async for f in bc._chat_stream(request, payload):
            line = f[len("data: ") :].strip()
            frames.append(json.loads(line))

    asyncio.new_event_loop().run_until_complete(_run())
    return frames


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_estimate_prompt_tokens_chars_over_four_plus_overhead() -> None:
    msgs = [{"role": "user", "content": "x" * 40}]
    # 40 // 4 == 10, plus one per-message overhead.
    assert bc._estimate_prompt_tokens(msgs) == 10 + bc._MSG_TOKEN_OVERHEAD


def test_estimate_prompt_tokens_handles_multipart_content() -> None:
    msgs = [{"role": "user", "content": [{"type": "text", "text": "y" * 20}]}]
    assert bc._estimate_prompt_tokens(msgs) == 5 + bc._MSG_TOKEN_OVERHEAD


def test_context_exceeded_error_is_actionable() -> None:
    msg = bc._context_exceeded_error(9000, 4096, "hal0/brain")
    assert "exceed_context" in msg
    assert "4096" in msg and "9000" in msg
    assert "context_size" in msg


# ── the guard fires end-to-end ───────────────────────────────────────────────


def test_precheck_short_circuits_before_round_trip(monkeypatch) -> None:
    async def _views(_request: Any) -> list[SlotView]:
        return [SlotView(name="brain", device="gpu-vulkan", model_id="m1", context_length=4096)]

    monkeypatch.setattr("hal0.api.routes.v1._normalize_slot_views", _views)
    monkeypatch.setattr("hal0.api.routes.v1._normalize_loaded_models", lambda _request: {"m1"})

    stub = _RecordingLLM()
    request = _request(stub)
    # ~25k-token prompt (100k chars / 4) against a 4096 window.
    payload = {"model": "hal0/brain", "message": "x" * 100_000}
    frames = _drive(request, payload)

    types = [f.get("type") for f in frames]
    assert types == ["error", "done"]
    assert "exceed_context" in frames[0]["message"]
    # The guard must fire BEFORE the completion round-trip.
    assert stub.calls == 0


def test_precheck_does_not_fire_when_prompt_fits(monkeypatch) -> None:
    async def _views(_request: Any) -> list[SlotView]:
        return [SlotView(name="brain", device="gpu-vulkan", model_id="m1", context_length=64000)]

    monkeypatch.setattr("hal0.api.routes.v1._normalize_slot_views", _views)
    monkeypatch.setattr("hal0.api.routes.v1._normalize_loaded_models", lambda _request: {"m1"})

    stub = _RecordingLLM()
    request = _request(stub)
    payload = {"model": "hal0/brain", "message": "hello"}
    frames = _drive(request, payload)

    # No pre-flight error; the round-trip ran (the stub was called).
    assert stub.calls == 1
    assert all(f.get("type") != "error" for f in frames)
