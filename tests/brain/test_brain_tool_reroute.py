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
    it is unchanged;
  * the native-tools preflight is a CAPABILITY question about the resolved
    runner image, not ``image == DEFAULT_ROCMFPX_IMAGE`` (#1789) — a non-Strix
    box's fallback toolbox keeps real tool rounds, a runner caught rejecting
    tools is learned at runtime, and a turn with no tool surface at all admits
    it instead of fabricating live state.

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
    DEFAULT_ROCMFPX_IMAGE,
    FALLBACK_CUDA_IMAGE,
    FALLBACK_VULKAN_IMAGE,
    NATIVE_TOOL_INCOMPATIBLE_IMAGE_REFS,
    STALE_ROCMFPX_IMAGE_REFS,
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


# ── native `tools` ride every round again (runner ade07ba) ──────────────────
#
# #1626 stripped `tools` from brain rounds because the c077206 runner 500'd
# every tools-attached completion ("peg-native format"). The real bug was the
# runtime stripping the vocab's tool-syntax CONTROL tokens at decode; the
# ade07ba runner preserves them and parses the 1B's attribute-XML natively,
# so the brain round must get the schemas back — a native `tool_calls` round
# from the brain is now the EXPECTED fast path, with the reroute as fallback.


def test_brain_rounds_carry_native_tools() -> None:
    """Every round — brain and tool-model alike — goes out with `tools`.

    The ade07ba runner parses the brain's dialect natively; stripping the
    schemas would forfeit the native fast path this runner exists for.
    """
    stub = _StubLLM(
        [
            _leaked("", reasoning=STATED_INTENT),  # round 0, brain (reroute fallback)
            _tool_call("list_slots", {}, "c1"),  # round 0 re-run, tool model
            _final("Three slots."),  # round 1, tool model
        ]
    )
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

    _run(_collect(request))

    assert stub.models == ["hal0/brain", "hal0/agent", "hal0/agent"]
    for n, call in enumerate(stub.calls):
        assert call.get("tools"), f"round {n} lost the native tools"


def test_a_native_brain_tool_call_stays_on_the_fast_path() -> None:
    """The brain returning real `tool_calls` is dispatched as-is — no re-run,
    no reroute of the round that produced it (the continuation still goes to
    the tool model per the chain semantics)."""
    stub = _StubLLM([_tool_call("list_slots", {}, "c1"), _final("done")])
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

    _run(_collect(request))

    assert stub.models == ["hal0/brain", "hal0/agent"]
    assert stub.calls[0].get("tools"), "brain round lost the native tools"


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
    # Slash-qualified names — measured live on lxc105 after the tools-less
    # round landed: the brain reached for `hal0/slot_list` and the remnant
    # ` name="hal0/slot_list">` sailed past a `/`-less name char class.
    assert art(_leaked(' name="hal0/slot_list">'), known) == "hal0/slot_list"
    assert art(_leaked('<function name="hal0/list_slots">'), known) == "hal0/list_slots"
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


# ── #1655: the chat-model round checks its resolved slot's runner first ─────
#
# ade07ba is the GA default, but `_chat_stream` permits overrides (a custom
# `model`, or a rollback via `image_pin`) and `retag_stale_slot_images` only
# fixes a stale pin on `hal0 update run` — not on every chat call. A brain
# round whose resolved slot is STILL on an older image (e.g. c077206) 500s on
# a `tools`-attached completion exactly like #1626 measured. These tests pin
# that the preflight in `_resolved_slot_native_tools` catches it and
# `_tool_routing_llm` strips `tools` off just the CHAT-model round.

_STALE_IMAGE = "ghcr.io/hal0ai/hal0-rocmfpx:c077206"


class _FakeSlotManager:
    """Mirrors the parts of ``SlotManager`` chat.py's preflight reads."""

    def __init__(self, configs: list[dict[str, Any]]) -> None:
        self._configs = configs

    async def iter_configs(self) -> list[dict[str, Any]]:
        return list(self._configs)


def _brain_slot_cfg(*, image_pin: str, device: str = "gpu-rocm") -> dict[str, Any]:
    return {
        "name": "brain",
        "type": "llm",
        "device": device,
        "profile": "rocmfpx-rocm",
        "image_pin": image_pin,
        "model": {"default": "hal0-brain-sft-fpx8"},
    }


def _fake_request_with_slot(stub: _StubLLM, slot_cfg: dict[str, Any], **brain_chat: Any) -> Any:
    request = _fake_request(stub, **brain_chat)
    request.app.state.slot_manager = _FakeSlotManager([slot_cfg])
    return request


def test_resolved_slot_native_tools_true_with_no_slot_manager() -> None:
    """Best-effort default: no slot_manager on app.state -> assume supported.

    Every OTHER test in this file hits exactly this path — proof the new
    preflight is a pure addition, not a behaviour change, when the check
    can't be reasoned about (matches _resolved_context_length's contract).
    """
    request = _fake_request(_StubLLM([]), model="hal0/brain", tool_model="hal0/agent")
    assert _run(bc._resolved_slot_native_tools(request, "hal0/brain")) is True


def test_resolved_slot_native_tools_true_on_the_default_image() -> None:
    """A slot pinned to the current DEFAULT_ROCMFPX_IMAGE (ade07ba) -> True."""
    request = _fake_request_with_slot(
        _StubLLM([]), _brain_slot_cfg(image_pin=DEFAULT_ROCMFPX_IMAGE), model="hal0/brain"
    )
    assert _run(bc._resolved_slot_native_tools(request, "hal0/brain")) is True


def test_resolved_slot_native_tools_false_on_a_stale_pin() -> None:
    """A slot pinned to c077206 (pre-ade07ba) -> False: it 500s on `tools`."""
    request = _fake_request_with_slot(
        _StubLLM([]), _brain_slot_cfg(image_pin=_STALE_IMAGE), model="hal0/brain"
    )
    assert _run(bc._resolved_slot_native_tools(request, "hal0/brain")) is False


def test_resolved_slot_native_tools_true_for_npu_backed_slots() -> None:
    """FLM/NPU-backed slots never route through the ROCmFPX runner this check
    exists for — an image_pin field there (if present at all) is noise."""
    request = _fake_request_with_slot(
        _StubLLM([]),
        _brain_slot_cfg(image_pin=_STALE_IMAGE, device="npu"),
        model="hal0/brain",
    )
    assert _run(bc._resolved_slot_native_tools(request, "hal0/brain")) is True


def test_stale_pin_strips_tools_from_the_chat_round_only() -> None:
    """The end-to-end contract: chat round goes out WITHOUT tools when the
    resolved slot 500s on them; the tool-model round still carries them, and
    the reroute still fires — detection reads the reply text, not the
    request, so a stale pin degrades to the fallback instead of a 500."""
    stub = _StubLLM(
        [
            _leaked(JINJA_LEAK),  # round 0, brain: garbage (as if tools-less)
            _tool_call("list_slots", {}, "c1"),  # round 0 re-run, tool model
            _final("You have three slots loaded."),  # round 1, tool model
        ]
    )
    request = _fake_request_with_slot(
        stub, _brain_slot_cfg(image_pin=_STALE_IMAGE), model="hal0/brain", tool_model="hal0/agent"
    )

    events = _run(_collect(request))

    assert stub.models == ["hal0/brain", "hal0/agent", "hal0/agent"]
    assert not stub.calls[0].get("tools"), "stale-pinned chat round must NOT carry tools"
    assert stub.calls[1].get("tools"), "tool-model round must still carry tools"
    assert stub.calls[2].get("tools"), "tool-model continuation must still carry tools"
    assert _tokens(events) == "You have three slots loaded."


def test_default_image_pin_keeps_tools_on_the_chat_round() -> None:
    """Regression guard: a slot explicitly pinned to today's default image
    keeps the native fast path — this preflight must not be a blanket strip."""
    stub = _StubLLM([_tool_call("list_slots", {}, "c1"), _final("done")])
    request = _fake_request_with_slot(
        stub,
        _brain_slot_cfg(image_pin=DEFAULT_ROCMFPX_IMAGE),
        model="hal0/brain",
        tool_model="hal0/agent",
    )

    _run(_collect(request))

    assert stub.models == ["hal0/brain", "hal0/agent"]
    assert stub.calls[0].get("tools"), "chat round on the default image lost native tools"


# ── #1789: the gate asks about CAPABILITY, not image identity ───────────────
#
# The #1655 preflight above shipped as `image == DEFAULT_ROCMFPX_IMAGE`. That
# is not the question it meant to ask: a CPU-only or CUDA box resolves its
# runner to FALLBACK_VULKAN_IMAGE / FALLBACK_CUDA_IMAGE — ordinary upstream
# llama.cpp builds with no MiniCPM5 parser and no peg-format failure mode, so
# they parse OpenAI `tools` fine — and got `False`, which stripped `tools` from
# EVERY brain round. Measured on CT151 during rc.4 validation: zero tool
# frames, zero dispatch, and a confident answer naming slots that do not
# exist. These tests pin the capability gate and the honesty fallback.


def _clear_learned() -> None:
    bc._LEARNED_TOOL_INCAPABLE_IMAGES.clear()


_PEG_FORMAT_ERROR = (
    "upstream 500 from slot brain: The model produced output that does not match "
    "the expected peg-native format"
)


def test_image_native_tools_denies_only_the_known_bad_lineage() -> None:
    """The gate's core table: deny the pre-ade07ba ROCmFPX refs, allow the rest.

    Every ref in NATIVE_TOOL_INCOMPATIBLE_IMAGE_REFS is a runner measured to
    500 the whole request on a tools-attached completion (#1626). Everything
    else — today's default, the two HW-gated fallbacks a non-Strix box lands
    on, an operator's own build — is assumed capable, which is the entire
    #1789 fix.
    """
    _clear_learned()
    for bad in NATIVE_TOOL_INCOMPATIBLE_IMAGE_REFS:
        assert bc._image_native_tools(bad) is False, bad
    for good in (
        DEFAULT_ROCMFPX_IMAGE,
        FALLBACK_VULKAN_IMAGE,  # CPU-only / non-Strix AMD box — the #1789 report
        FALLBACK_CUDA_IMAGE,
        "ghcr.io/example/my-own-llama-server:v9",
    ):
        assert bc._image_native_tools(good) is True, good
    # An unresolvable image is not a verdict — best-effort default (True).
    assert bc._image_native_tools(None) is True
    assert bc._image_native_tools("   ") is True
    # The former basic-lane toolbox defaults are STALE, not tool-incompatible:
    # the updater retags them, but they parse `tools` perfectly meanwhile.
    assert "ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server" in (
        STALE_ROCMFPX_IMAGE_REFS
    )
    assert NATIVE_TOOL_INCOMPATIBLE_IMAGE_REFS < STALE_ROCMFPX_IMAGE_REFS


def test_resolved_slot_native_tools_true_on_a_non_default_capable_image() -> None:
    """The #1789 regression, at the preflight: a CPU-only box's runner is
    NOT the rocmfpx default and must still be treated as tools-capable."""
    _clear_learned()
    request = _fake_request_with_slot(
        _StubLLM([]),
        _brain_slot_cfg(image_pin=FALLBACK_VULKAN_IMAGE, device="cpu"),
        model="hal0/brain",
    )
    assert _run(bc._resolved_slot_native_tools(request, "hal0/brain")) is True


def test_capable_non_default_image_runs_real_tool_rounds() -> None:
    """End to end on a CPU-only box's runner: the chat round CARRIES `tools`,
    the brain's native tool_call is dispatched, and the turn reads live state
    instead of inventing it."""
    _clear_learned()
    stub = _StubLLM([_tool_call("list_slots", {}, "c1"), _final("Three slots are loaded.")])
    request = _fake_request_with_slot(
        stub,
        _brain_slot_cfg(image_pin=FALLBACK_VULKAN_IMAGE, device="cpu"),
        model="hal0/brain",
        tool_model="hal0/agent",
    )

    events = _run(_collect(request))

    assert stub.calls[0].get("tools"), "#1789: a capable non-default runner lost native tools"
    assert [e["name"] for e in events if e["type"] == "tool_call"] == ["list_slots"]
    assert not [e for e in events if e["type"] == "notice"], "tools worked — no notice is due"
    assert _tokens(events) == "Three slots are loaded."


def test_read_only_refusal_is_reachable_on_a_capable_non_default_image() -> None:
    """#1789's second symptom: with tools stripped, `read_only=true` never got
    a tool call to refuse, so a mutation request drew a hallucinated refusal
    instead of the documented one. With the gate fixed the guard fires."""
    _clear_learned()
    stub = _StubLLM(
        [
            _tool_call("config_write", {"path": "brain_chat.read_only", "value": False}, "c1"),
            _final("I can't do that in read-only mode."),
        ]
    )
    request = _fake_request_with_slot(
        stub,
        _brain_slot_cfg(image_pin=FALLBACK_VULKAN_IMAGE, device="cpu"),
        model="hal0/brain",
        tool_model="hal0/agent",
    )
    request.app.state.hal0_config = Hal0Config(
        brain_chat=BrainChatConfig(read_only=True, model="hal0/brain", tool_model="hal0/agent")
    )

    events = _run(_collect(request))

    results = [e for e in events if e["type"] == "tool_result"]
    assert results, "no tool was dispatched — the read-only guard is still unreachable"
    assert "read-only mode" in json.dumps(results[0]["result"])


def test_a_tool_format_reject_is_learned_and_the_round_is_salvaged() -> None:
    """The runtime half of the gate. "Assume capable" is only safe because a
    runner that turns out NOT to be teaches the gate on its first failure:
    THIS round retries tools-less (no 500 reaches the operator) and every
    later turn on that image goes out tools-less from the start."""
    _clear_learned()
    try:
        stub = _StubLLM([{"error": _PEG_FORMAT_ERROR}, _final("Plain answer.")])
        request = _fake_request_with_slot(
            stub,
            _brain_slot_cfg(image_pin="ghcr.io/example/surprise-runner:v1"),
            model="hal0/brain",
        )

        events = _run(_collect(request))

        assert stub.calls[0].get("tools"), "first attempt should try the native fast path"
        assert not stub.calls[1].get("tools"), "the retry must drop tools"
        assert _tokens(events) == "Plain answer."  # no 500 surfaced
        assert bc._image_native_tools("ghcr.io/example/surprise-runner:v1") is False
        # ...and the NEXT turn skips the doomed attempt entirely.
        assert _run(bc._resolved_slot_native_tools(request, "hal0/brain")) is False, (
            "the learned verdict must survive into later turns"
        )
    finally:
        _clear_learned()


def test_an_ordinary_dispatch_error_does_not_teach_the_gate() -> None:
    """Fail-safe: a timeout / no_route / plain 500 must NOT mute tools for the
    rest of the process, and must keep the documented error contract."""
    _clear_learned()
    try:
        stub = _StubLLM([{"error": "dispatch.no_route: nothing loaded for hal0/brain"}])
        request = _fake_request_with_slot(
            stub, _brain_slot_cfg(image_pin=FALLBACK_CUDA_IMAGE), model="hal0/brain"
        )

        events = _run(_collect(request))

        assert len(stub.calls) == 1, "a non-tool-format error must not trigger a retry"
        assert any(e["type"] == "error" for e in events)
        assert bc._image_native_tools(FALLBACK_CUDA_IMAGE) is True
    finally:
        _clear_learned()


# ── #1789: honesty when tools cannot ride the turn at all ───────────────────


def test_incapable_runner_with_no_reroute_says_so() -> None:
    """No tool surface AND no reroute -> the turn is told, in the system seed,
    that it has no live view, and the client gets a `tools_unavailable` frame.

    This is the actual bug the operator hit: not an error, a fabrication. An
    admitted gap is the required behaviour.
    """
    _clear_learned()
    stub = _StubLLM([_final("I can't check live state right now.")])
    request = _fake_request_with_slot(
        stub,
        _brain_slot_cfg(image_pin=_STALE_IMAGE),
        model="hal0/brain",
        tool_model="off",  # in BRAIN_TOOL_MODEL_DISABLED -> normalised to "" (no reroute)
    )

    events = _run(_collect(request))

    notice = next(e for e in events if e["type"] == "notice")
    assert notice["code"] == "tools_unavailable"
    assert "context only" in notice["message"]
    # The MODEL is constrained too — the notice rides the system seed, never a
    # trailing system turn (template-safety, see _frame_messages / O18).
    seed = stub.calls[0]["messages"][0]
    assert seed["role"] == "system"
    assert "LIVE PLATFORM TOOLS ARE UNAVAILABLE" in seed["content"]
    assert all(m["role"] == "system" for m in stub.calls[0]["messages"][:1])
    assert stub.calls[0]["messages"][-1]["role"] != "system"
    assert not stub.calls[0].get("tools")


def test_no_notice_when_a_reroute_can_still_run_tools() -> None:
    """An incapable chat runner is NOT a lost tool surface while `tool_model`
    is there to run the round — that path already works, so claiming tools are
    unavailable would be its own lie."""
    _clear_learned()
    stub = _StubLLM(
        [
            _leaked(JINJA_LEAK),
            _tool_call("list_slots", {}, "c1"),
            _final("Three slots."),
        ]
    )
    request = _fake_request_with_slot(
        stub, _brain_slot_cfg(image_pin=_STALE_IMAGE), model="hal0/brain", tool_model="hal0/agent"
    )

    events = _run(_collect(request))

    assert not [e for e in events if e["type"] == "notice"]
    assert "LIVE PLATFORM TOOLS ARE UNAVAILABLE" not in json.dumps(stub.calls[0]["messages"])
    assert _tokens(events) == "Three slots."


def test_prepend_system_notice_folds_into_the_existing_seed() -> None:
    """Never a trailing system turn: the chat templates this module already
    fights expect the conversation to END on user/assistant/tool."""
    messages = [
        {"role": "system", "content": "You are the steward."},
        {"role": "user", "content": "what slots are loaded?"},
    ]
    bc._prepend_system_notice(messages, "NOTICE")
    assert messages[0]["content"] == "You are the steward.\n\nNOTICE"
    assert len(messages) == 2

    # No system seed at all -> the notice becomes one, at the FRONT.
    bare = [{"role": "user", "content": "hi"}]
    bc._prepend_system_notice(bare, "NOTICE")
    assert bare[0] == {"role": "system", "content": "NOTICE"}
    assert bare[-1]["role"] == "user"
