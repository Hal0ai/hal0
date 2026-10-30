"""The steward's memory path: scoped memory_add / memory_search / memory_list (#2658).

Before #2658 brain chat dropped every ``memory_*`` tool by prefix and nothing
else in the chat path reached the memory engine, so a "remember X" turn got a
confident "I've noted that" with zero tool frames and nothing stored, while the
docs and the seeded persona promised a private bank.

These tests pin the real path:

* the brain surfaces exactly the persona-approved trio (memory_add,
  memory_search, memory_list) among the memory_* tools, with one-line
  descriptions and minimal arguments for the small steward model;
* every call is scoped server-side to the persona's ``memory_namespace``
  (``private:hermes__hal0-brain``) through the app's memory dispatcher, which
  now writes the SHARED pool as that agent id (private mode off); a
  ``dataset``/``bank`` argument from the model is dropped, never trusted;
* the #2022/#2675 grounding guard treats "remember ..." / "what did I tell
  you ..." as live state, so a no-tool "I've noted that" is rerouted.

Scripted stub LLM and an in-memory fake provider, no network.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hal0.agents.personas import (
    BRAIN_PROFILE_AGENT_ID,
    Persona,
    PersonaApproval,
    save_persona,
    seed_default_personas,
)
from hal0.api.routes import board_chat as bc
from hal0.config.schema import BrainChatConfig, Hal0Config
from hal0.dispatcher.memory_dispatcher import MemoryDispatcher
from hal0.mcp import admin
from hal0.mcp.approval_queue import ApprovalQueue

BRAIN_NAMESPACE = f"private:{BRAIN_PROFILE_AGENT_ID}"  # the persona's identity source
SHARED = "shared"
MEMORY_TRIO = {"memory_add", "memory_search", "memory_list"}


class _FakeProvider:
    """An in-memory MemoryProvider recording every call's dataset + client_id."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def add(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("add", kw))
        item = {
            "id": f"doc-{len(self.items) + 1}",
            "text": kw["text"],
            "dataset": kw["dataset"],
            "timestamp": "2026-10-15T00:00:00Z",
        }
        self.items.append(item)
        return {"id": item["id"], "timestamp": item["timestamp"]}

    async def search(self, **kw: Any) -> list[dict[str, Any]]:
        self.calls.append(("search", kw))
        words = {w.strip("?.,!").lower() for w in kw["query"].split()}
        datasets = kw["dataset"] if isinstance(kw["dataset"], list) else [kw["dataset"]]
        return [
            {**i, "score": 1.0}
            for i in self.items
            if i["dataset"] in datasets and words & set(i["text"].lower().strip(".").split())
        ][: kw["limit"]]

    async def list_items(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("list", kw))
        return {
            "items": [i for i in self.items if i["dataset"] == kw["dataset"]][: kw["limit"]],
            "next_cursor": None,
        }


class _StubLLM:
    """Pops a canned response per call, recording each request body."""

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


def _request(
    tmp_path: Path,
    *,
    stub: _StubLLM | None = None,
    provider: _FakeProvider | None = None,
    read_only: bool = True,
    persona_root: Path | None = None,
    **brain_chat: Any,
) -> Any:
    """A Request stand-in with the seeded personas and a real MemoryDispatcher.

    ``read_only`` defaults to the SHIPPED default (true): the steward's own
    scoped memory must work on a fresh install, not only after an opt-in.
    """
    if persona_root is None:
        persona_root = tmp_path / "personas"
        seed_default_personas(root=persona_root)
    state = SimpleNamespace(
        board_chat_llm=stub,
        approval_queue=ApprovalQueue(),
        self_api_base_url="http://testserver",
        brain_persona_root=persona_root,
        memory_provider=provider,
        memory_dispatcher=MemoryDispatcher(provider) if provider is not None else None,
        hal0_config=Hal0Config(brain_chat=BrainChatConfig(read_only=read_only, **brain_chat)),
        audit=None,
        platform_http=None,
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


def _tokens(events: list[dict[str, Any]]) -> str:
    return "\n".join(e["text"] for e in events if e["type"] == "token")


# ── (a) the catalog carries exactly the persona-approved trio ───────────────


def test_brain_catalog_memory_tools_are_exactly_the_persona_trio(tmp_path: Path) -> None:
    surfaced = {s["function"]["name"] for s in bc._surfaced_tool_schemas(_request(tmp_path))}
    assert {n for n in surfaced if n.startswith("memory_")} == MEMORY_TRIO

    catalog = admin.AUTONOMOUS_READ_TOOLS | admin.AUTONOMOUS_WRITE_TOOLS | admin.GATED_TOOLS
    others = {t for t in catalog if t.startswith("memory_")} - MEMORY_TRIO
    assert others, "the admin catalog should still carry the rest of the memory family"
    assert all(bc._is_admin_tool_excluded(t) for t in others)
    # A future memory_* tool is excluded without touching chat.py.
    assert bc._is_admin_tool_excluded("memory_some_future_tool")
    # The static exclusion set and the predicate agree.
    assert isinstance(bc._ADMIN_TOOL_EXCLUDES, frozenset)
    assert others <= bc._ADMIN_TOOL_EXCLUDES
    assert MEMORY_TRIO.isdisjoint(bc._ADMIN_TOOL_EXCLUDES)


def test_memory_schemas_are_minimal_for_the_small_model() -> None:
    by_name = {
        s["function"]["name"]: s["function"]
        for s in bc._admin_tool_schemas()
        if s["function"]["name"] in MEMORY_TRIO
    }
    assert set(by_name) == MEMORY_TRIO
    shapes = {
        "memory_add": ({"text"}, ["text"]),
        "memory_search": ({"query", "limit"}, ["query"]),
        "memory_list": ({"limit"}, []),
    }
    for name, (props, required) in shapes.items():
        fn = by_name[name]
        assert "\n" not in fn["description"] and len(fn["description"]) <= 120, name
        params = fn["parameters"]
        assert set(params["properties"]) == props, name
        assert params["required"] == required, name
        # No bank/dataset knob for the model to turn.
        assert params.get("additionalProperties") is False, name


# ── (b) a remember turn retains into the persona's bank ─────────────────────


def test_remember_turn_retains_into_the_brain_bank_and_ignores_model_bank(
    tmp_path: Path,
) -> None:
    provider = _FakeProvider()
    stub = _StubLLM(
        [
            _tool_call(
                "memory_add",
                {
                    "text": "The operator's cat is called Zorbo.",
                    "dataset": "shared",
                    "bank": "private:hermes",
                },
                "c1",
            ),
            _final("Saved: your cat is called Zorbo."),
        ]
    )
    request = _request(tmp_path, stub=stub, provider=provider)

    events = asyncio.run(_collect(request, "Remember that my cat is called Zorbo."))

    adds = [kw for op, kw in provider.calls if op == "add"]
    assert len(adds) == 1
    assert adds[0]["text"] == "The operator's cat is called Zorbo."
    assert adds[0]["dataset"] == SHARED
    assert adds[0]["client_id"] == BRAIN_PROFILE_AGENT_ID
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["name"] == "memory_add"
    assert result["result"]["status"] == "ok"
    assert _tokens(events) == "Saved: your cat is called Zorbo."


@pytest.mark.asyncio
async def test_memory_add_strips_every_unknown_argument(tmp_path: Path) -> None:
    provider = _FakeProvider()
    request = _request(tmp_path, provider=provider)
    result = await bc._dispatch_tool(
        request,
        "memory_add",
        {"text": "x fact", "dataset": "shared", "tags": ["t"], "metadata": {"a": 1}},
        board=None,
    )
    assert result["status"] == "ok"
    (kw,) = [kw for op, kw in provider.calls if op == "add"]
    assert kw["dataset"] == SHARED
    assert kw["tags"] == []
    assert kw["metadata"] == {}


# ── (c) a later turn reads it back through memory_search ────────────────────


def test_recall_turn_reads_the_stored_fact_back(tmp_path: Path) -> None:
    provider = _FakeProvider()
    persona_root = tmp_path / "personas"
    seed_default_personas(root=persona_root)

    first = _StubLLM(
        [
            _tool_call("memory_add", {"text": "The operator's cat is called Zorbo."}, "c1"),
            _final("Saved."),
        ]
    )
    asyncio.run(
        _collect(
            _request(tmp_path, stub=first, provider=provider, persona_root=persona_root),
            "Remember that my cat is called Zorbo.",
        )
    )

    second = _StubLLM(
        [
            _tool_call("memory_search", {"query": "cat", "dataset": "shared"}, "c2"),
            _final("Your cat is called Zorbo."),
        ]
    )
    events = asyncio.run(
        _collect(
            _request(tmp_path, stub=second, provider=provider, persona_root=persona_root),
            "What is my cat called?",
        )
    )

    searches = [kw for op, kw in provider.calls if op == "search"]
    assert len(searches) == 1
    assert searches[0]["dataset"] == SHARED
    assert searches[0]["client_id"] == BRAIN_PROFILE_AGENT_ID
    result = next(e for e in events if e["type"] == "tool_result")
    texts = [r["text"] for r in result["result"]["results"]]
    assert texts == ["The operator's cat is called Zorbo."]
    assert _tokens(events) == "Your cat is called Zorbo."


@pytest.mark.asyncio
async def test_memory_list_is_scoped_and_limit_is_coerced(tmp_path: Path) -> None:
    provider = _FakeProvider()
    request = _request(tmp_path, provider=provider)
    result = await bc._dispatch_tool(
        request, "memory_list", {"limit": "5", "dataset": "shared"}, board=None
    )
    assert result["status"] == "ok"
    (kw,) = [kw for op, kw in provider.calls if op == "list"]
    assert kw["dataset"] == SHARED
    assert kw["limit"] == 5


@pytest.mark.asyncio
async def test_memory_tool_without_a_memory_engine_is_an_honest_error(tmp_path: Path) -> None:
    request = _request(tmp_path, provider=None)
    result = await bc._dispatch_tool(request, "memory_add", {"text": "x"}, board=None)
    assert "error" in result
    assert "memory" in json.dumps(result).lower()


@pytest.mark.asyncio
async def test_persona_tools_allowed_still_narrows_memory(tmp_path: Path) -> None:
    root = tmp_path / "narrow"
    save_persona(
        Persona(
            id=bc.BRAIN_PERSONA_ID,
            display_name="hal0 Brain",
            tools_allowed=("get_board",),
            memory_namespace=BRAIN_NAMESPACE,
            approval=PersonaApproval(),
        ),
        root=root,
    )
    provider = _FakeProvider()
    request = _request(tmp_path, provider=provider, persona_root=root)
    surfaced = {s["function"]["name"] for s in bc._surfaced_tool_schemas(request)}
    assert surfaced.isdisjoint(MEMORY_TRIO)
    result = await bc._dispatch_tool(request, "memory_add", {"text": "x"}, board=None)
    assert "error" in result
    assert provider.calls == []


# ── (d) the grounding guard reroutes a no-tool "I've noted that" ────────────


@pytest.mark.parametrize(
    "prompt",
    [
        "Remember that my cat is called Zorbo.",
        "Please remember my wifi password hint is blue.",
        "What did I tell you about my cat?",
        "Do you remember what my cat is called?",
        "Note that the NAS is in the attic.",
        "Don't forget I prefer short answers.",
    ],
)
def test_memory_turns_count_as_live_state(prompt: str) -> None:
    assert bc._asks_for_live_state(prompt) is True


def test_no_tool_noted_answer_to_a_remember_turn_is_rerouted(tmp_path: Path) -> None:
    provider = _FakeProvider()
    stub = _StubLLM(
        [
            _final("I've noted that your cat is named Zorbo."),  # brain: no tool call
            _tool_call("memory_add", {"text": "The operator's cat is called Zorbo."}, "c1"),
            _final("Saved: your cat is called Zorbo."),
        ]
    )
    request = _request(
        tmp_path, stub=stub, provider=provider, model="hal0/brain", tool_model="hal0/agent"
    )

    events = asyncio.run(_collect(request, "Remember that my cat is called Zorbo."))

    assert stub.models == ["hal0/brain", "hal0/agent", "hal0/agent"]
    assert "I've noted that" not in _tokens(events)
    assert [op for op, _ in provider.calls] == ["add"]


# ── prompts say what memory the steward has ─────────────────────────────────


def test_fallback_prompt_and_seeded_persona_describe_the_memory_rule(tmp_path: Path) -> None:
    root = tmp_path / "personas"
    seed_default_personas(root=root)
    from hal0.agents.personas import load_persona

    seeded = load_persona(bc.BRAIN_PERSONA_ID, root=root).system_prompt
    for prompt in (bc._SYSTEM_PROMPT, seeded):
        assert "memory_add" in prompt
        assert "memory_search" in prompt


def test_an_older_persona_prompt_gets_the_memory_rule_appended(tmp_path: Path) -> None:
    """Upgraded boxes keep their seeded TOML (the seeder never overwrites):
    a persona prompt that predates #2658 still gets the memory rule."""
    root = tmp_path / "old"
    save_persona(
        Persona(
            id=bc.BRAIN_PERSONA_ID,
            display_name="hal0 Brain",
            system_prompt="You are hal0-brain. Keep replies short.",
            memory_namespace=BRAIN_NAMESPACE,
            approval=PersonaApproval(),
        ),
        root=root,
    )
    prompt, _model = bc._resolve_profile(_request(tmp_path, persona_root=root))
    assert prompt.startswith("You are hal0-brain. Keep replies short.")
    assert bc._MEMORY_RULE in prompt
    # Not appended when the prompt already describes the memory tools.
    seeded_root = tmp_path / "seeded"
    seed_default_personas(root=seeded_root)
    seeded_prompt, _ = bc._resolve_profile(_request(tmp_path, persona_root=seeded_root))
    assert bc._MEMORY_RULE not in seeded_prompt
    assert "memory_add" in seeded_prompt


def test_a_post_2658_persona_prompt_loses_the_private_bank_claim(tmp_path: Path) -> None:
    """Boxes seeded after #2658 keep a prompt describing a private bank; the
    resolved prompt must describe shared memory instead (spec R7)."""
    root = tmp_path / "post2658"
    old_rule = (
        "Memory: you have a private memory bank (namespace private:hermes__hal0-brain), "
        "separate from the Hermes chat agent. memory_add(text) saves one fact, "
        "memory_search(query) finds saved facts, memory_list() shows recent ones. "
        "Nothing is remembered unless you call memory_add: never say you noted, saved "
        "or remembered something without a memory_add result in this turn. When asked "
        "what the operator told you before, call memory_search first."
    )
    save_persona(
        Persona(
            id=bc.BRAIN_PERSONA_ID,
            display_name="hal0 Brain",
            system_prompt=f"You are hal0-brain.\n\n{old_rule}\n\nKeep replies short.",
            memory_namespace=BRAIN_NAMESPACE,
            approval=PersonaApproval(),
        ),
        root=root,
    )
    prompt, _model = bc._resolve_profile(_request(tmp_path, persona_root=root))
    assert "private memory bank" not in prompt
    assert "private:hermes__hal0-brain" not in prompt
    assert bc._MEMORY_RULE in prompt
    assert prompt.startswith("You are hal0-brain.")
    assert prompt.endswith("Keep replies short.")
    assert prompt.count("memory_add(text)") == 1


def test_a_hand_edited_private_bank_sentence_is_stripped(tmp_path: Path) -> None:
    root = tmp_path / "edited"
    save_persona(
        Persona(
            id=bc.BRAIN_PERSONA_ID,
            display_name="hal0 Brain",
            system_prompt="You are hal0-brain. You own a private memory bank. Use memory_add.",
            memory_namespace=BRAIN_NAMESPACE,
            approval=PersonaApproval(),
        ),
        root=root,
    )
    prompt, _model = bc._resolve_profile(_request(tmp_path, persona_root=root))
    assert "private memory bank" not in prompt
    assert bc._MEMORY_RULE in prompt


def test_stale_bank_sentence_without_a_period_is_stripped_and_paragraphs_survive(
    tmp_path: Path,
) -> None:
    """Case-insensitive, no terminating period, and the paragraph break stays."""
    root = tmp_path / "noperiod"
    save_persona(
        Persona(
            id=bc.BRAIN_PERSONA_ID,
            display_name="hal0 Brain",
            system_prompt="You are hal0-brain.\nYou own a Private Memory Bank\n\nBe brief.",
            memory_namespace=BRAIN_NAMESPACE,
            approval=PersonaApproval(),
        ),
        root=root,
    )
    prompt, _model = bc._resolve_profile(_request(tmp_path, persona_root=root))
    assert "private memory bank" not in prompt.lower()
    assert "You are hal0-brain.\n" in prompt and "\n\nBe brief." in prompt
    assert bc._MEMORY_RULE in prompt


# ── the real fallback engine, end to end ────────────────────────────────────


@pytest.mark.asyncio
async def test_round_trip_through_the_pgvector_fallback_is_shared(tmp_path: Path) -> None:
    """The Hindsight-unavailable fallback provider takes the same path: the
    fact lands in the shared pool (tagged as the steward), reads back through
    memory_search, and is visible to a plain shared read."""
    from hal0.memory.pgvector_provider import PgVectorProvider

    provider = PgVectorProvider()
    request = _request(tmp_path)
    request.app.state.memory_provider = provider
    request.app.state.memory_dispatcher = MemoryDispatcher(provider)

    added = await bc._dispatch_tool(
        request, "memory_add", {"text": "The operator's cat is called Zorbo."}, board=None
    )
    assert added["status"] == "ok"

    found = await bc._dispatch_tool(request, "memory_search", {"query": "Zorbo cat"}, board=None)
    assert found["status"] == "ok"
    assert any("Zorbo" in r["text"] for r in found["results"])

    shared = await provider.search(query="Zorbo cat", limit=10, dataset="shared")
    assert any("Zorbo" in r["text"] for r in shared)


@pytest.mark.asyncio
async def test_brain_memory_add_dispatches_shared(tmp_path: Path) -> None:
    provider = _FakeProvider()
    request = _request(tmp_path, provider=provider)
    await bc._dispatch_tool(request, "memory_add", {"text": "CT151 is the utility box"}, board=None)
    (kw,) = [kw for op, kw in provider.calls if op == "add"]
    assert kw["dataset"] == SHARED
    assert kw["client_id"] == BRAIN_PROFILE_AGENT_ID
    assert not kw.get("private")


def test_no_steward_string_claims_a_private_bank(tmp_path: Path) -> None:
    """R7: the steward never describes privacy it no longer has."""
    from hal0.agents.personas import load_persona

    root = tmp_path / "personas"
    seed_default_personas(root=root)
    texts = [
        bc._MEMORY_RULE,
        bc._SYSTEM_PROMPT,
        load_persona(bc.BRAIN_PERSONA_ID, root=root).system_prompt,
        *(desc for desc, _p, _r in bc._BRAIN_MEMORY_SCHEMAS.values()),
    ]
    for text in texts:
        assert "private" not in text.lower(), text
    assert "shared" in bc._MEMORY_RULE.lower()
    assert BRAIN_PROFILE_AGENT_ID in bc._MEMORY_RULE
