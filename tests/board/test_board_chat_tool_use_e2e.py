"""E2E tests for the sidebar Brain's tool-use loop (2026-07-11 hardening).

Pins the operator-facing behaviors added when the Brain was granted
autonomous slot/model execution:

  - gated tool calls surface in the CHAT STREAM (``approval_required``
    frame + agent-facing ``detail``), not just the approvals bell;
  - the turn PAUSES on a gated call and resumes with the executed /
    denied result once the operator decides (inline card or bell);
  - a persona ``auto_approve`` grant loosens a server-gated tool through
    the chat path (the standing grant on the live box);
  - every completion round carries a ``max_tokens`` cap (an uncapped
    runaway generation used to eat the 300 s transport window and kill
    the turn before the first tool call — "primary slot transport
    failure");
  - the loop budget terminates a pathological tool loop with the typed
    error frame;
  - the surfaced tool schemas name the BODY args the model must use
    (it used to guess: model_pull with model_id='org/repo' → 405);
  - path args containing '/' are rejected with an actionable hint.

Tests drive the module-level ``_chat_stream`` generator directly so the
pause/approve flow is deterministic (approval happens between frames on
the same event loop). The LLM is a scripted stub; the admin REST hop is
stubbed at admin._call_rest so no network happens. Run targeted:

    PYTHONPATH=src .venv/bin/python -m pytest tests/board/test_board_chat_tool_use_e2e.py -q
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from hal0.activity import AuditStore
from hal0.agents.personas import Persona, PersonaApproval, save_persona
from hal0.api.routes import board_chat as bc
from hal0.board import HermesKanbanClient
from hal0.mcp import admin
from hal0.mcp.approval_queue import ApprovalQueue

# ── harness ─────────────────────────────────────────────────────────────────


class _StubLLM:
    """Pops a canned chat-completion response per call; records bodies."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(body))
        if self._responses:
            return self._responses.pop(0)
        return _final("done")


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


class _RestRecorder:
    """Stands in for admin._call_rest — records the REST hop, no network."""

    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = response or {"ok": True}

    async def __call__(self, *, base_url, bearer, method, url, payload, **kw) -> dict[str, Any]:
        self.calls.append({"method": method, "url": url, "payload": payload})
        return self.response


def _persona_root(tmp_path: Path, *, auto_approve: tuple[str, ...] = ()) -> Path:
    persona = Persona(
        id=bc.BRAIN_PERSONA_ID,
        display_name="hal0 Brain",
        approval=PersonaApproval(default_policy="ask", auto_approve=auto_approve),
    )
    save_persona(persona, root=tmp_path)
    return tmp_path


def _fake_request(
    stub: _StubLLM,
    tmp_path: Path,
    *,
    persona_root: Path | None = None,
    queue: ApprovalQueue | None = None,
) -> Any:
    """A Request stand-in with exactly the state _chat_stream reads."""
    kanban_http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True})),
        base_url="http://127.0.0.1:9119",
    )
    store = AuditStore(tmp_path / "audit.db")
    store.init_schema()
    state = SimpleNamespace(
        board_chat_llm=stub,
        hermes_kanban=HermesKanbanClient(
            http_client=kanban_http, session_token_resolver=lambda: "TOK"
        ),
        approval_queue=queue if queue is not None else ApprovalQueue(),
        self_api_base_url="http://testserver",
        brain_persona_root=persona_root or Path("/nonexistent-personas-root"),
        audit_store=store,
        memory_dispatcher=None,
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


@pytest.fixture(autouse=True)
def _fast_approval_wait(monkeypatch):
    """Pause loop ticks fast in tests; individual tests re-patch as needed."""
    monkeypatch.setattr(bc, "_APPROVAL_WAIT_S", 0.3)
    monkeypatch.setattr(bc, "_APPROVAL_POLL_S", 0.02)


# ── approval gates surface in the chat stream ───────────────────────────────


def test_gated_tool_emits_approval_required_frame(tmp_path, monkeypatch) -> None:
    """Gated call → tool_result(pending_approval + detail) → approval_required."""
    monkeypatch.setattr(admin, "_call_rest", _RestRecorder())
    queue = ApprovalQueue()
    stub = _StubLLM([_tool_call("model_delete", {"model_id": "doomed"}, "c1"), _final("queued")])
    request = _fake_request(stub, tmp_path, queue=queue)

    events = _run(_collect(request))
    types = [e["type"] for e in events]
    assert "approval_required" in types

    result = next(e for e in events if e["type"] == "tool_result")
    assert result["result"]["status"] == "pending_approval"
    # The agent-facing hint that tells the model to inform the operator.
    assert "operator" in result["result"]["detail"]

    gate = next(e for e in events if e["type"] == "approval_required")
    assert gate["name"] == "model_delete"
    assert gate["id"] == "c1"
    assert gate["approval_id"]
    # tool_result first, then the explicit gate announcement.
    assert types.index("tool_result") < types.index("approval_required")

    # The call stays parked on the queue the bell reads (nobody decided).
    assert [p["tool"] for p in queue.list_pending()] == ["model_delete"]


def test_undecided_gate_times_out_and_turn_continues(tmp_path, monkeypatch) -> None:
    """No decision → the model gets the pending result with the timeout hint."""
    monkeypatch.setattr(admin, "_call_rest", _RestRecorder())
    stub = _StubLLM([_tool_call("model_delete", {"model_id": "x"}, "c1"), _final("parked")])
    request = _fake_request(stub, tmp_path)

    events = _run(_collect(request))
    assert events[-1]["type"] == "done"
    # The tool message the LLM saw carries the still-pending hint.
    tool_msg = next(m for m in stub.calls[-1]["messages"] if m.get("role") == "tool")
    assert "still pending" in tool_msg["content"]


# ── pause-and-resume on operator decision ───────────────────────────────────


def test_approving_mid_turn_resumes_with_executed_result(tmp_path, monkeypatch) -> None:
    rest = _RestRecorder(response={"deleted": True})
    monkeypatch.setattr(admin, "_call_rest", rest)
    monkeypatch.setattr(bc, "_APPROVAL_WAIT_S", 10.0)
    queue = ApprovalQueue()
    stub = _StubLLM([_tool_call("model_delete", {"model_id": "doomed"}, "c1"), _final("gone")])
    request = _fake_request(stub, tmp_path, queue=queue)

    async def _drive() -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        gen = bc._chat_stream(request, {"messages": [{"role": "user", "content": "rm"}]})
        async for frame in gen:
            event = _parse(frame)
            events.append(event)
            if event["type"] == "approval_required":
                # Operator clicks Approve while the turn is paused.
                await queue.approve(event["approval_id"])
        return events

    events = _run(_drive())
    results = [e for e in events if e["type"] == "tool_result"]
    # First result: pending; follow-up result: the executed payload.
    assert results[0]["result"]["status"] == "pending_approval"
    assert results[-1]["result"] == {"deleted": True}
    assert len(rest.calls) == 1
    assert rest.calls[0]["method"] == "DELETE"
    assert rest.calls[0]["url"].endswith("/api/models/doomed")
    # The LLM's next round saw the EXECUTED result, not the pending stub.
    tool_msg = next(m for m in stub.calls[-1]["messages"] if m.get("role") == "tool")
    assert json.loads(tool_msg["content"]) == {"deleted": True}


def test_denying_mid_turn_resumes_with_denial(tmp_path, monkeypatch) -> None:
    rest = _RestRecorder()
    monkeypatch.setattr(admin, "_call_rest", rest)
    monkeypatch.setattr(bc, "_APPROVAL_WAIT_S", 10.0)
    queue = ApprovalQueue()
    stub = _StubLLM([_tool_call("model_delete", {"model_id": "keep"}, "c1"), _final("ok")])
    request = _fake_request(stub, tmp_path, queue=queue)

    async def _drive() -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        async for frame in bc._chat_stream(
            request, {"messages": [{"role": "user", "content": "rm"}]}
        ):
            event = _parse(frame)
            events.append(event)
            if event["type"] == "approval_required":
                await queue.deny(event["approval_id"])
        return events

    events = _run(_drive())
    results = [e for e in events if e["type"] == "tool_result"]
    assert results[-1]["result"]["status"] == "denied"
    assert rest.calls == []  # never executed
    tool_msg = next(m for m in stub.calls[-1]["messages"] if m.get("role") == "tool")
    assert "denied" in tool_msg["content"]


def test_paused_turn_emits_keepalive_pings(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(admin, "_call_rest", _RestRecorder())
    monkeypatch.setattr(bc, "_APPROVAL_WAIT_S", 0.2)
    monkeypatch.setattr(bc, "_APPROVAL_POLL_S", 0.02)
    monkeypatch.setattr(bc, "_APPROVAL_PING_EVERY_S", 0.04)
    stub = _StubLLM([_tool_call("model_delete", {"model_id": "x"}, "c1"), _final("ok")])
    request = _fake_request(stub, tmp_path)

    events = _run(_collect(request))
    assert any(e["type"] == "ping" for e in events)


# ── persona auto_approve loosening (the operator grant) ─────────────────────


def test_persona_auto_approve_loosens_gated_tool_via_chat(tmp_path, monkeypatch) -> None:
    """The live box grants slot_create/slot_restart/model_pull — pin the path."""
    rest = _RestRecorder()
    monkeypatch.setattr(admin, "_call_rest", rest)
    root = _persona_root(tmp_path, auto_approve=("slot_create", "model_pull"))
    stub = _StubLLM(
        [_tool_call("slot_create", {"name": "ops", "model": "Grug-12B"}, "c1"), _final("made")]
    )
    request = _fake_request(stub, tmp_path, persona_root=root)

    events = _run(_collect(request))
    assert "approval_required" not in [e["type"] for e in events]
    assert len(rest.calls) == 1
    assert rest.calls[0]["method"] == "POST"
    assert rest.calls[0]["url"].endswith("/api/slots")
    assert rest.calls[0]["payload"] == {"name": "ops", "model": "Grug-12B"}


def test_persona_cannot_loosen_destructive_floor(tmp_path, monkeypatch) -> None:
    """POLICY_NO_LOOSEN tools stay gated even if the persona lists them."""
    rest = _RestRecorder()
    monkeypatch.setattr(admin, "_call_rest", rest)
    root = _persona_root(tmp_path, auto_approve=("model_delete",))
    stub = _StubLLM([_tool_call("model_delete", {"model_id": "x"}, "c1"), _final("q")])
    request = _fake_request(stub, tmp_path, persona_root=root)

    events = _run(_collect(request))
    assert "approval_required" in [e["type"] for e in events]
    assert rest.calls == []


def test_autonomous_tool_gets_no_approval_frame(tmp_path, monkeypatch) -> None:
    """slot_edit is an autonomous write — runs immediately, no gate frame.

    (slot_load/slot_restart are local platform verbs, not admin-routed —
    see _ADMIN_TOOL_EXCLUDES — so the admin-path check uses slot_edit.)
    """
    rest = _RestRecorder()
    monkeypatch.setattr(admin, "_call_rest", rest)
    stub = _StubLLM(
        [_tool_call("slot_edit", {"name": "ops", "ctx-size": 8192}, "c1"), _final("ok")]
    )
    request = _fake_request(stub, tmp_path)

    events = _run(_collect(request))
    assert "approval_required" not in [e["type"] for e in events]
    assert len(rest.calls) == 1
    assert rest.calls[0]["url"].endswith("/api/slots/ops/config")


# ── completion body hygiene ─────────────────────────────────────────────────


def test_every_round_carries_max_tokens_cap(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(admin, "_call_rest", _RestRecorder())
    stub = _StubLLM([_tool_call("slot_load", {"name": "ops"}, "c1"), _final("ok")])
    request = _fake_request(stub, tmp_path)
    _run(_collect(request))

    assert len(stub.calls) == 2
    for body in stub.calls:
        assert body["max_tokens"] == bc._MAX_COMPLETION_TOKENS


def test_payload_max_tokens_override_wins(tmp_path) -> None:
    stub = _StubLLM([_final("hi")])
    request = _fake_request(stub, tmp_path)
    _run(
        _collect_with_payload(
            request, {"messages": [{"role": "user", "content": "x"}], "max_tokens": 512}
        )
    )
    assert stub.calls[0]["max_tokens"] == 512


async def _collect_with_payload(request: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [_parse(f) async for f in bc._chat_stream(request, payload)]


def test_loop_budget_terminates_with_typed_error(tmp_path, monkeypatch) -> None:
    """The [brain_chat] max_rounds knob bounds a pathological tool loop."""
    from hal0.config.schema import BrainChatConfig

    monkeypatch.setattr(admin, "_call_rest", _RestRecorder())
    stub = _StubLLM([_tool_call("slot_load", {"name": "ops"}, f"c{i}") for i in range(5)])
    request = _fake_request(stub, tmp_path)
    request.app.state.hal0_config = SimpleNamespace(brain_chat=BrainChatConfig(max_rounds=2))
    events = _run(_collect(request))
    err = next(e for e in events if e["type"] == "error")
    assert "budget exhausted" in err["message"]
    assert len(stub.calls) == 2


# ── surfaced schemas name the body args (no more guessing) ──────────────────


def _schema(name: str) -> dict[str, Any]:
    by_name = {s["function"]["name"]: s["function"]["parameters"] for s in bc._admin_tool_schemas()}
    return by_name[name]


def test_model_pull_schema_names_hf_body_args() -> None:
    params = _schema("model_pull")
    assert "hf_repo" in params["properties"]
    assert "hf_filename" in params["properties"]
    assert "model_id" in params["required"]
    # The local-id rule that prevented model_id='org/repo' → 405.
    assert "no slashes" in params["properties"]["model_id"]["description"].lower()


def test_model_inspect_schema_names_repo_args() -> None:
    params = _schema("model_inspect")
    assert "hf_repo" in params["properties"]
    assert "hf_url" in params["properties"]


def test_slot_create_schema_covers_fpx_image_path() -> None:
    params = _schema("slot_create")
    assert set(params["required"]) == {"name", "model"}
    assert "image" in params["properties"]
    assert "runtime" in params["properties"]
    assert "port" in params["properties"]


def test_slot_model_family_distinguishes_slot_from_model() -> None:
    """model_assign/model_swap: name is the SLOT; the model rides the body."""
    assign = _schema("model_assign")
    assert "model" in assign["properties"]
    assert "not the model" in assign["properties"]["name"]["description"].lower()
    swap = _schema("model_swap")
    assert "model_id" in swap["properties"]
    # slot_load is a LOCAL platform verb (admin twin is excluded) — its
    # schema lives in the local tool list and requires the slot name.
    local = {s["function"]["name"]: s["function"]["parameters"] for s in bc._tool_schemas()}
    assert local["slot_load"]["required"] == ["name"]


# ── path-arg slash guard ────────────────────────────────────────────────────


def test_path_arg_with_slash_rejected_with_hint() -> None:
    with pytest.raises(KeyError) as exc:
        admin._split_args("model_pull", {"model_id": "org/repo"})
    assert "hf_repo" in str(exc.value)


def test_slash_guard_surfaces_as_typed_error_via_chat(tmp_path, monkeypatch) -> None:
    rest = _RestRecorder()
    monkeypatch.setattr(admin, "_call_rest", rest)
    stub = _StubLLM([_tool_call("model_pull", {"model_id": "org/repo"}, "c1"), _final("oops")])
    # model_pull is gated by default — grant it so the call reaches execution.
    root = _persona_root(tmp_path, auto_approve=("model_pull",))
    request = _fake_request(stub, tmp_path, persona_root=root)

    events = _run(_collect(request))
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["result"]["status"] == "error"
    assert "hf_repo" in json.dumps(result["result"])
    assert rest.calls == []  # never reached the REST hop
