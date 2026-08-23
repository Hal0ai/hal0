"""Live-state grounding — the steward may not answer about this box from memory (#2022).

rc.7 fleet validation, on the SHIPPED installer-seeded brain anchor
(``hal0-brain-sft-q8-rocmfpx``, ~1.1B):

  * ``"What port is the brain slot running on? Answer in one short sentence."``
    came back with a confidently INVENTED port and ZERO tool frames;
  * dropping ONLY the brevity clause flipped the same question to a correct,
    tool-grounded answer (2/2 both directions).

So the brevity instruction — reinforced by the steward prompt's own "Keep
replies short" — was enough to suppress the tool round on that model class,
and the existing reroute could not catch it: :func:`_tool_intent_artefact`
fires on a GARBLED tool call, and here the model never attempted one.

The fix is question-side. After the fact an invented port and a read one are
the same string, so the only usable signal is that the operator ASKED about
live state: such a turn is marked ``grounding_required``, and a chat round
that answers it with no tool call is discarded and re-run on ``tool_model``,
exactly as a garbled round is.

Scripted stub LLM, no network.

Run targeted:
    uv run pytest tests/brain/test_brain_live_state_grounding.py -q
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
from hal0.mcp.approval_queue import ApprovalQueue

#: The fabrication the rc.7 run actually got back (shape, not the exact string).
FABRICATED = "The brain slot is running on port 8081."


class _StubLLM:
    """Pops a canned response per call, recording the model of EACH call."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    @property
    def models(self) -> list[str]:
        return [c["model"] for c in self.calls]

    async def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(json.loads(json.dumps(body, default=str)))
        return self._responses.pop(0) if self._responses else _final("done")


def _final(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


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


class _FakeKanban:
    async def request_json(self, method: str, path: str, **kw: Any) -> Any:
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


async def _collect(request: Any, prompt: str) -> list[dict[str, Any]]:
    out = []
    async for frame in bc._chat_stream(
        request, {"messages": [{"role": "user", "content": prompt}]}
    ):
        assert frame.startswith("data: ")
        out.append(json.loads(frame[len("data: ") :].strip()))
    return out


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _tokens(events: list[dict[str, Any]]) -> str:
    return "\n".join(e["text"] for e in events if e["type"] == "token")


# ── the reported defect ─────────────────────────────────────────────────────


def test_brevity_clause_cannot_suppress_the_tool_round() -> None:
    """THE #2022 repro: brevity clause -> confident invented port, zero tool frames.

    The brain's ungrounded answer must be discarded and the round re-run on the
    tool-capable model, which looks the value up.
    """
    stub = _StubLLM(
        [
            _final(FABRICATED),  # round 0, brain: fabricated, no tool call
            _tool_call("list_slots", {}, "c1"),  # round 0 re-run, tool model
            _final("The brain slot is on port 18102."),  # round 1, tool model
        ]
    )
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

    events = _run(
        _collect(
            request,
            "What port is the brain slot running on? Answer in one short sentence.",
        )
    )

    assert stub.models == ["hal0/brain", "hal0/agent", "hal0/agent"]
    call = next(e for e in events if e["type"] == "tool_call")
    assert call["name"] == "list_slots"
    assert FABRICATED not in _tokens(events)
    assert _tokens(events) == "The brain slot is on port 18102."


def test_plain_live_state_question_is_grounded_too() -> None:
    """The brevity clause is a trigger, not the disease — the plain form counts."""
    stub = _StubLLM(
        [
            _final("There are 12 models registered."),
            _tool_call("list_models", {}, "c1"),
            _final("Three models are registered."),
        ]
    )
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

    events = _run(_collect(request, "How many models are registered on this hal0 install?"))

    assert stub.models == ["hal0/brain", "hal0/agent", "hal0/agent"]
    assert _tokens(events) == "Three models are registered."


def test_a_grounded_brain_round_is_not_re_run() -> None:
    """The control direction: the 1B DID call a tool -> no second completion."""
    stub = _StubLLM([_tool_call("list_slots", {}, "c1"), _final("Port 18102.")])
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

    events = _run(_collect(request, "What port is the brain slot running on?"))

    # Round 0 on the brain (its own call is kept), continuation on the tool model.
    assert stub.models == ["hal0/brain", "hal0/agent"]
    assert _tokens(events) == "Port 18102."


def test_small_talk_never_wakes_the_tool_model() -> None:
    """Grounding must not cost a second completion on chat that needs no lookup."""
    for prompt in ("hi", "thanks, that helped", "you're doing great"):
        stub = _StubLLM([_final("Happy to help.")])
        request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

        events = _run(_collect(request, prompt))

        assert stub.models == ["hal0/brain"], prompt
        assert _tokens(events) == "Happy to help.", prompt


def test_a_conceptual_question_stays_on_the_brain() -> None:
    """ "What is a slot?" is documentation the system prompt already carries."""
    stub = _StubLLM([_final("A slot is a named inference unit.")])
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

    events = _run(_collect(request, "What is a slot in hal0?"))

    assert stub.models == ["hal0/brain"]
    assert _tokens(events) == "A slot is a named inference unit."


def test_grounding_is_a_no_op_with_nowhere_to_reroute() -> None:
    """`tool_model` off -> the turn keeps its documented single-completion shape.

    There is no second model to ask, so re-running the same one would only cost
    a round; the honesty layer (`_tools_unavailable_notice`) and the system
    prompt's grounding rules own this path.
    """
    stub = _StubLLM([_final(FABRICATED)])
    request = _fake_request(stub, model="hal0/brain", tool_model="off")

    events = _run(_collect(request, "What port is the brain slot running on?"))

    assert stub.models == ["hal0/brain"]
    assert events[-1] == {"type": "done"}


def test_grounding_fires_at_most_once_per_turn() -> None:
    """The reroute enters the tool chain — it cannot loop the turn on itself."""
    stub = _StubLLM(
        [
            _final(FABRICATED),  # brain, ungrounded
            _final("I could not read the slot table."),  # tool model, also no call
        ]
    )
    request = _fake_request(stub, model="hal0/brain", tool_model="hal0/agent")

    events = _run(_collect(request, "Which port is the brain slot bound to?"))

    assert stub.models == ["hal0/brain", "hal0/agent"]
    assert _tokens(events) == "I could not read the slot table."


# ── the detector itself ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "prompt",
    [
        "What port is the brain slot running on? Answer in one short sentence.",
        "Which port is the brain slot bound to?",
        "How many models are registered on this hal0 install right now?",
        "list my slots",
        "show me the board",
        "is the npu slot loaded",
        "what's on the board",
        "how much VRAM is free",
        "which agents are installed",
    ],
)
def test_live_state_questions_are_detected(prompt: str) -> None:
    assert bc._asks_for_live_state(prompt) is True


@pytest.mark.parametrize(
    "prompt",
    [
        "hi",
        "thanks, that helped",
        "What is a slot?",
        "what is an inference backend",
        "Explain how the dispatcher decides what to run",
        "How do I create a new profile?",
        "Why does the board have a triage lane",
        "",
        "   ",
    ],
)
def test_non_live_state_prompts_are_not_detected(prompt: str) -> None:
    assert bc._asks_for_live_state(prompt) is False


def test_last_user_text_reads_the_latest_turn_and_content_parts() -> None:
    messages = [
        {"role": "system", "content": "steward"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": [{"type": "text", "text": "list my slots"}]},
    ]
    assert bc._last_user_text(messages) == "list my slots"
    assert bc._last_user_text([{"role": "system", "content": "x"}]) == ""


# ── the wrong-tool half of the report ───────────────────────────────────────


def _description(name: str) -> str:
    for schema in bc._tool_schemas():
        if schema["function"]["name"] == name:
            return schema["function"]["description"]
    raise AssertionError(f"{name} is not a surfaced local tool")


def test_local_registry_and_remote_catalogue_are_distinguishable() -> None:
    """`list_models` is THIS box; `model_catalogue` is what could be pulled.

    rc.7 saw "how many models are registered?" answered from the HuggingFace
    catalogue. Both descriptions now name their scope and point at each other.
    """
    from hal0.mcp.admin import TOOL_DESCRIPTIONS

    local = _description("list_models").lower()
    assert "this box" in local
    assert "not the remote" in local

    remote = TOOL_DESCRIPTIONS["model_catalogue"].lower()
    assert "remote" in remote
    assert "not what is installed" in remote


def test_the_board_tool_does_not_advertise_itself_for_every_question() -> None:
    """rc.7 saw `get_board` answer a SLOT question.

    Its old description carried a bare "Call this FIRST", which a small model
    reads as a global instruction; it is scoped to task ids now, and the tool
    says out loud that it knows nothing about slots.
    """
    board = _description("get_board").lower()
    assert "task ids before mutating a task" in board
    assert "slots" in board  # the explicit "not slots/models/ports/hardware" disclaimer


def test_slot_tools_advertise_the_port() -> None:
    """A port question has to have an obvious tool to land on."""
    assert "port" in _description("list_slots").lower()
    assert "port" in _description("get_slot").lower()


def test_the_system_prompt_forbids_trading_grounding_for_brevity() -> None:
    """The prompt half of the fix — the model must not read "short" as "guess"."""
    prompt = bc._SYSTEM_PROMPT
    assert "BREVITY NEVER CANCELS A TOOL CALL" in prompt
    assert "GROUND EVERY CLAIM ABOUT THIS BOX IN A TOOL RESULT" in prompt
    assert "short AFTER you have looked" in prompt
