"""Tool-round rerouting — ``[brain_chat] tool_model`` (ADR-0023 / §5a).

The shipped ~1.1B brain model cannot emit tool calls the local runtime parses.
Measured on a GPU box against the published
``hal0-brain-sft-Q8_0_ROCMFPX_AGENT.gguf`` in the pinned ROCmFPX runner:

  * WITH ``--jinja`` + native OpenAI ``tools``: HTTP 200, ``finish_reason:
    stop``, ``tool_calls: null``, and ``content`` came back as the literal
    string ``' name="slot_list">'`` — llama.cpp's jinja tool parser ate the
    ``<function`` opener and dumped the remnant into content;
  * WITHOUT ``--jinja`` (the ``hal0-function-xml`` prompt contract):
    ``content`` came back EMPTY with ``reasoning_content`` ending "I'll call
    the slot_list() function to retrieve the information."

Both literal shapes are pinned below as the reroute triggers, so a regression
in the detector shows up as *these exact strings* reaching the operator.

What this file locks down:

  * a plain-chat round stays on the brain; a tool round goes to ``tool_model``;
  * once a turn is in a tool chain the TOOL model finishes it — the 1B never
    reads a ``role: tool`` payload;
  * the chain is scoped to ONE turn (a fresh turn is back on the brain), and is
    NOT inferred from replayed inbound ``role: tool`` history;
  * ``off``/``none``/``disabled`` disable the reroute; ``""``/whitespace falls
    back to the default with a warning (Stream A's contract, extended here to
    the routing behaviour that contract now drives);
  * the reroute target having NO model bound — the default fresh-box state —
    produces a clear, actionable steward message: no crash, no empty reply;
  * ``hal0.toolloop.engine.run_tool_loop`` is untouched, so OmniRouter's use of
    it is unchanged.

Scripted stub LLM, no network.

Run targeted:
    uv run pytest tests/brain/test_brain_tool_reroute.py -q
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from hal0.api.routes import board_chat as bc
from hal0.config.schema import (
    BRAIN_TOOL_MODEL_DEFAULT,
    BRAIN_TOOL_MODEL_DISABLED,
    BrainChatConfig,
    Hal0Config,
)
from hal0.mcp.approval_queue import ApprovalQueue

# ── the two measured failure shapes ─────────────────────────────────────────

#: What `--jinja` leaves in `content` after its tool parser eats the opener.
JINJA_LEAK = ' name="slot_list">'

#: What the no-`--jinja` run put in `reasoning_content`, with `content` EMPTY.
STATED_INTENT = (
    "The operator wants the slot inventory. I'll call the slot_list() function "
    "to retrieve the information."
)


# ── harness ─────────────────────────────────────────────────────────────────


class _StubLLM:
    """Pops a canned response per call, recording the model of EACH call.

    Records a deep-ish copy: the tool loop mutates one ``body`` dict in place
    across rounds, so appending the object itself would make every recorded
    call alias the last one — which is exactly the per-round routing this file
    is asserting.
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    @property
    def models(self) -> list[str]:
        return [c["model"] for c in self.calls]

    async def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(json.loads(json.dumps(body, default=str)))
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


def _leaked(content: str, reasoning: str = "") -> dict[str, Any]:
    """A 200 OK completion with NO tool_calls — the brain's real failure shape."""
    msg: dict[str, Any] = {"role": "assistant", "content": content, "tool_calls": None}
    if reasoning:
        msg["reasoning_content"] = reasoning
    return {"choices": [{"message": msg, "finish_reason": "stop"}]}


class _FakeKanban:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def request_json(self, method: str, path: str, **kw: Any) -> Any:
        self.calls.append((method, path))
        return {"columns": []} if path == "/board" else {"ok": True}


def _fake_request(stub: _StubLLM, **brain_chat: Any) -> Any:
    cfg = BrainChatConfig(read_only=False, **brain_chat)
    state = SimpleNamespace(
        board_chat_llm=stub,
        hermes_kanban=_FakeKanban(),
        approval_queue=ApprovalQueue(),
        self_api_base_url="http://testserver",
        brain_persona_root=Path("/nonexistent-personas-root"),
        memory_dispatcher=None,
        hal0_config=Hal0Config(brain_chat=cfg),
        audit=None,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state), headers={})


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _collect(request: Any, payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = payload or {"messages": [{"role": "user", "content": "list my slots"}]}
    out = []
    async for frame in bc._chat_stream(request, payload):
        assert frame.startswith("data: ")
        out.append(json.loads(frame[len("data: ") :].strip()))
    return out


def _tokens(events: list[dict[str, Any]]) -> str:
    return "\n".join(e["text"] for e in events if e["type"] == "token")


# ── the headline contract ───────────────────────────────────────────────────


def test_plain_chat_round_stays_on_the_brain() -> None:
    """No tool wanted -> the 1B answers and `tool_model` is never touched.

    This is the reason routing is per-round: a fast 1B steward that woke a 35B
    for "hi" would be pointless.
    """
    stub = _StubLLM([_final("Hey — what can I help with?")])
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

    events = _run(_collect(request, {"messages": [{"role": "user", "content": "hi"}]}))

    assert stub.models == ["hal0/brain"]
    assert _tokens(events) == "Hey — what can I help with?"
    assert events[-1] == {"type": "done"}


def test_jinja_leak_reroutes_that_round_to_the_tool_model() -> None:
    """The measured `--jinja` shape: the round re-runs on `tool_model`.

    The brain's output is DISCARDED — ' name="slot_list">' must never reach the
    operator — and the tool model's tool call is what drives the turn.
    """
    stub = _StubLLM(
        [
            _leaked(JINJA_LEAK),  # round 0, brain: garbage
            _tool_call("list_slots", {}, "c1"),  # round 0 re-run, tool model
            _final("You have three slots loaded."),  # round 1, tool model
        ]
    )
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

    events = _run(_collect(request))

    # Round 0 on the brain, re-run on the tool model, continuation on the tool
    # model. The 1B was asked once and never again this turn.
    assert stub.models == ["hal0/brain", "hal0/agent", "hal0/agent"]
    # The leak never surfaced.
    assert JINJA_LEAK not in _tokens(events)
    assert "slot_list" not in _tokens(events)
    # The tool actually ran.
    call = next(e for e in events if e["type"] == "tool_call")
    assert call["name"] == "list_slots"
    assert _tokens(events) == "You have three slots loaded."


def test_stated_intent_with_empty_content_reroutes() -> None:
    """The measured no-`--jinja` shape: intent in reasoning, content empty.

    Left alone this is a blank steward reply — a 200 OK that reads as broken.
    """
    stub = _StubLLM(
        [
            _leaked("", reasoning=STATED_INTENT),
            _tool_call("list_slots", {}, "c1"),
            _final("Three slots."),
        ]
    )
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

    events = _run(_collect(request))

    assert stub.models == ["hal0/brain", "hal0/agent", "hal0/agent"]
    assert _tokens(events) == "Three slots."
    assert events[-1] == {"type": "done"}


def test_a_reply_that_merely_mentions_a_tool_is_not_rerouted() -> None:
    """The detector must not fire on prose. A real reply is a real reply.

    Names a surfaced tool, in call form, in the reasoning — but `content` is
    non-empty, so the turn produced an actual answer and there is nothing to
    recover.
    """
    stub = _StubLLM(
        [
            _leaked(
                "You can see them on the Slots page.",
                reasoning="I could call list_slots() but the operator only asked where.",
            )
        ]
    )
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

    events = _run(_collect(request))

    assert stub.models == ["hal0/brain"]
    assert _tokens(events) == "You can see them on the Slots page."


def test_a_usable_brain_tool_call_is_not_re_run() -> None:
    """When the brain gets it right, dispatch it — don't burn a second round.

    The reroute exists because the 1B *fails*; a box whose `[brain_chat] model`
    points at a capable model must not pay double for every tool turn.
    """
    stub = _StubLLM([_tool_call("list_slots", {}, "c1"), _final("done")])
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

    _run(_collect(request))

    # Exactly two completions — round 0 on the brain (its call is used as-is),
    # round 1 on the tool model. No re-run of round 0.
    assert stub.models == ["hal0/brain", "hal0/agent"]


# ── who finishes a tool chain ───────────────────────────────────────────────


def test_the_tool_model_reads_the_tool_results_not_the_brain() -> None:
    """The settled semantics for point 1: the tool-capable model continues.

    The `role: tool` payload is raw JSON from an 82-tool admin catalogue and a
    chain usually needs a second call. The round that SEES a tool result is
    always a tool-model round.
    """
    stub = _StubLLM(
        [
            _tool_call("get_board", {}, "c1"),
            _tool_call("get_board", {}, "c2"),  # chains a second call
            _final("summary"),
        ]
    )
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

    _run(_collect(request))

    assert stub.models == ["hal0/brain", "hal0/agent", "hal0/agent"]
    # Every round that carried a tool result ran on the tool model.
    for body in stub.calls:
        if any(m.get("role") == "tool" for m in body["messages"]):
            assert body["model"] == "hal0/agent"


def test_the_chain_is_scoped_to_one_turn() -> None:
    """A fresh turn starts back on the brain — the 1B stays the steward's voice."""
    stub = _StubLLM([_tool_call("get_board", {}, "c1"), _final("done")])
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")
    _run(_collect(request))
    assert stub.models == ["hal0/brain", "hal0/agent"]

    # Second turn, same app state, same config.
    stub._responses = [_final("sure")]
    stub.calls.clear()
    _run(_collect(request, {"messages": [{"role": "user", "content": "thanks"}]}))
    assert stub.models == ["hal0/brain"]


def test_replayed_tool_history_does_not_pin_a_turn_to_the_tool_model() -> None:
    """Chain state is LOCAL, not inferred from the inbound messages.

    The dashboard replays prior tool turns as history
    (ui/src/api/hooks/useBoard.ts) — scanning `messages` for `role: tool` would
    pin every subsequent turn to the tool model and silently retire the 1B.
    """
    stub = _StubLLM([_final("Nothing else pending.")])
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

    events = _run(
        _collect(
            request,
            {
                "messages": [
                    {"role": "user", "content": "what's on the board?"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "old",
                                "type": "function",
                                "function": {"name": "get_board", "arguments": "{}"},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "old",
                        "name": "get_board",
                        "content": "{}",
                    },
                    {"role": "user", "content": "anything else?"},
                ]
            },
        )
    )

    assert stub.models == ["hal0/brain"]
    assert _tokens(events) == "Nothing else pending."


# ── the tool_model vocabulary (Stream A's contract) ─────────────────────────


def test_explicit_off_spellings_disable_the_reroute() -> None:
    """ "off"/"none"/"disabled" -> no reroute, ever. One completion per round."""
    for spelling in sorted(BRAIN_TOOL_MODEL_DISABLED):
        stub = _StubLLM([_leaked(JINJA_LEAK), _final("x")])
        request = _fake_request(stub, model="hal0/brain", tool_model=spelling)

        events = _run(_collect(request))

        # The garbage round was NOT re-run — with the reroute off there is
        # nowhere to send it, so the turn ends on the brain's own output.
        assert stub.models == ["hal0/brain"], spelling
        assert events[-1] == {"type": "done"}, spelling


def test_empty_tool_model_falls_back_to_the_default_and_still_reroutes() -> None:
    """Stream A: "" normalises to the default. It must ROUTE there too.

    Ruling 12: a live box was found with `tool_model = ""`, which overrode the
    default and silently killed tool routing. Normalising the value is only
    half the fix — the routing has to follow it.
    """
    for spelling in ("", "   ", "\t\n"):
        stub = _StubLLM([_leaked(JINJA_LEAK), _tool_call("list_slots", {}, "c1"), _final("ok")])
        request = _fake_request(stub, model="hal0/brain", tool_model=spelling)

        _run(_collect(request))

        assert stub.models == [
            "hal0/brain",
            BRAIN_TOOL_MODEL_DEFAULT,
            BRAIN_TOOL_MODEL_DEFAULT,
        ], repr(spelling)


def test_empty_tool_model_warns(caplog) -> None:
    """The fallback is loud — a config that looks unset must not be silent."""
    with caplog.at_level("WARNING", logger="hal0.config.schema"):
        cfg = BrainChatConfig(tool_model="")
    assert cfg.tool_model == BRAIN_TOOL_MODEL_DEFAULT
    assert any("tool_model is empty" in r.getMessage() for r in caplog.records)


def test_tool_model_equal_to_the_chat_model_is_a_no_op() -> None:
    """Nothing to reroute to — re-running the same model would only cost a round."""
    stub = _StubLLM([_leaked(JINJA_LEAK), _final("x")])
    request = _fake_request(stub, model="hal0/agent", tool_model="hal0/agent")

    _run(_collect(request))

    assert stub.models == ["hal0/agent"]


def test_per_request_model_sets_the_chat_model_but_does_not_pin_the_turn() -> None:
    """The dashboard sends `model` on EVERY message (useBoard.ts:1219).

    Treating a per-request model as a whole-turn pin would make the reroute
    dead in the only UI that uses it. It sets the CHAT model; tool rounds still
    go to `tool_model`.
    """
    stub = _StubLLM([_leaked(JINJA_LEAK), _tool_call("list_slots", {}, "c1"), _final("ok")])
    request = _fake_request(stub, model="hal0/npu", tool_model="hal0/agent")

    _run(
        _collect(
            request,
            {"model": "hal0/utility", "messages": [{"role": "user", "content": "slots?"}]},
        )
    )

    assert stub.models == ["hal0/utility", "hal0/agent", "hal0/agent"]


# ── degrading honestly when the target has no model (the fresh-box path) ────


def test_no_model_on_the_reroute_target_explains_itself() -> None:
    """Per ruling 10 the agent anchor is a skip-by-default install opt-in, so on
    most fresh boxes `hal0/agent` has NO model bound. That is the single most
    likely real-world path, not an error branch: the operator must get a
    sentence they can act on, not a crash, not a 500, and not silence.
    """
    stub = _StubLLM(
        [
            _leaked(JINJA_LEAK),
            # The re-run hits the resolver's dead end — what the real
            # _primary_completion returns on a 404 dispatch.no_route.
            {"error": bc._unrouteable_model_error("hal0/agent")},
        ]
    )
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

    events = _run(_collect(request))

    # No crash, and the turn terminated normally.
    assert events[-1] == {"type": "done"}
    assert not [e for e in events if e["type"] == "error"]

    # Not an empty reply — and it names the target, the surface to fix it on,
    # and the opt-out.
    said = _tokens(events)
    assert said.strip(), "the steward said nothing at all"
    assert "hal0/agent" in said
    assert "Models page" in said
    assert "`agent` slot" in said
    assert "tool_model" in said
    assert "Plain chat keeps working" in said
    # The raw leak is still not shown.
    assert JINJA_LEAK not in said


def test_a_transport_failure_on_the_tool_model_also_degrades_to_a_sentence() -> None:
    """Same first-class handling for the other reroute failure, with the
    underlying error kept visible so it stays diagnosable."""
    stub = _StubLLM(
        [
            _leaked(JINJA_LEAK),
            {"error": "primary slot transport failure: connection refused"},
        ]
    )
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

    events = _run(_collect(request))

    said = _tokens(events)
    assert "connection refused" in said
    assert "Models page" in said
    assert events[-1] == {"type": "done"}


def test_a_tool_model_failure_mid_chain_still_explains_itself() -> None:
    """The tools ran; the model that must READ their results is missing.

    Without this the turn would end on a bare error frame right after a
    successful mutation — the worst moment to go quiet.
    """
    stub = _StubLLM(
        [
            _tool_call("get_board", {}, "c1"),  # brain got it right
            {"error": bc._unrouteable_model_error("hal0/agent")},  # continuation dies
        ]
    )
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

    events = _run(_collect(request))

    assert next(e for e in events if e["type"] == "tool_result")["result"] == {"tasks": []}
    said = _tokens(events)
    assert "Models page" in said
    assert events[-1] == {"type": "done"}


def test_a_brain_side_failure_keeps_its_documented_error_frame() -> None:
    """The degrade path is for TOOL-model rounds only — a brain transport
    failure still surfaces as the documented error+done frames."""
    stub = _StubLLM([{"error": "primary slot transport failure: connection refused"}])
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

    events = _run(_collect(request))

    err = next(e for e in events if e["type"] == "error")
    assert "transport failure" in err["message"]
    assert events[-1] == {"type": "done"}


# ── the detector, unit level ────────────────────────────────────────────────


def test_tool_intent_artefact_signals() -> None:
    known = frozenset({"list_slots", "get_board"})
    art = bc._tool_intent_artefact

    # (a) the measured jinja remnant, and the whole tag it came from.
    #
    # NOTE the name in the real measured leak is `slot_list`, which is NOT a
    # tool — the catalogue has `list_slots` / `slot_state`. Gating the detector
    # on the name being real would have missed the exact failure it exists to
    # catch, so it does not.
    assert art(_leaked(JINJA_LEAK), known) == "slot_list"
    assert art(_leaked('<function name="get_board">'), known) == "get_board"
    # An unterminated `<function=NAME` opener (the terminated form is already
    # handled by the shared text-call fallback).
    assert art(_leaked("<function=list_slots"), known) == "list_slots"
    # A tag with nothing readable in it is still a failed call.
    assert art(_leaked("<tool_call>"), known) == "(unnamed)"

    # (b) stated intent with nothing to show for it.
    assert art(_leaked("", reasoning=STATED_INTENT), known) == "slot_list"
    # ...but only when there is genuinely nothing to show.
    assert art(_leaked("Here you go.", reasoning=STATED_INTENT), known) is None
    # A real tool name in the reasoning is preferred over an earlier one.
    assert (
        art(_leaked("", reasoning="maybe frobnicate(), no — list_slots()"), known) == "list_slots"
    )

    # Never fires on ordinary prose, or on a genuinely blank turn.
    assert art(_leaked("Three slots are loaded."), known) is None
    assert art(_leaked('I made a slot with name="ops" for you.'), known) is None
    assert art(_leaked("", reasoning="Thinking about the answer."), known) is None
    # No tools surfaced -> no reroute is possible, so no signal.
    assert art(_leaked(JINJA_LEAK), frozenset()) is None


def test_unavailable_message_is_actionable() -> None:
    msg = bc._tool_reroute_unavailable_message("hal0/agent", "dispatch.no_route")
    assert "hal0/agent" in msg
    assert "`agent` slot" in msg  # the slot name is extracted from hal0/<slot>
    assert "Models page" in msg
    assert "dispatch.no_route" in msg
    # A non-virtual target degrades without mangling the name.
    assert "some-model" in bc._tool_reroute_unavailable_message("some-model", "x")
