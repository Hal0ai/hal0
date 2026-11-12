"""MCP ``memory_reflect`` keeps other agents' private memories out of the answer.

``POST /api/memory/reflect`` has held a reflect to what the caller may read
since ADR-0058 decision 6; the MCP tool did not, on either mount: it handed
the caller's arguments to the engine as sent. The rule now lives in
``HindsightProvider.reflect``, so both entry points get the same one. These
tests drive the MCP dispatcher the way ``tests/api/test_memory_reflect_route.py``
drives the route, against the same double of a unified bank: facts are found
through ``tag_groups`` (evaluated as Hindsight 1.2.0 does), and a mental model
is found by ITS OWN tags, which are empty, although its text was written from
every fact in the bank.
"""

from __future__ import annotations

from typing import Any, ClassVar
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.agents.hermes_provision import profile_agent_id
from hal0.api.middleware import error_codes
from hal0.api.routes import memory as memory_routes
from hal0.mcp import memory as memory_mcp
from hal0.memory.hindsight_provider import HindsightProvider
from hal0.memory.read_scope import reflect_visibility_scope

# ── the engine's tag-group semantics, restated for the test ────────────────


def _leaf_matches(leaf: dict[str, Any], tags: list[str]) -> bool:
    wanted, have = set(leaf["tags"]), set(tags)
    match = leaf.get("match", "any_strict")
    if match == "exact":
        return wanted == have
    overlap = bool(wanted & have) if match.startswith("any") else wanted <= have
    if match.endswith("_strict"):
        return bool(have) and overlap
    return not have or overlap


def _matches(group: dict[str, Any], tags: list[str]) -> bool:
    if "tags" in group:
        return _leaf_matches(group, tags)
    if "and" in group:
        return all(_matches(child, tags) for child in group["and"])
    if "or" in group:
        return any(_matches(child, tags) for child in group["or"])
    return not _matches(group["not"], tags)


def _readable(groups: list[dict[str, Any]], tags: list[str]) -> bool:
    return all(_matches(group, tags) for group in groups)


WORK = profile_agent_id("work")
DISCORD = profile_agent_id("discord")

SHARED_TEXT = "The backup window is Sunday 03:00."
WORK_NOTE = "The work profile's client is Acme and the contract ends in March."
DISCORD_OWN = "The discord profile keeps its moderation notes here."
MODEL_TEXT = "User profile: works with Acme; the contract ends in March."

SHARED_FACT = ["agent:hermes", "kind:durable", "scope:global"]
WORK_PRIVATE = [f"agent:{WORK}", "kind:durable", "scope:global", "visibility:private"]
DISCORD_PRIVATE = [f"agent:{DISCORD}", "kind:durable", "scope:global", "visibility:private"]


class BankDouble:
    """A bank as the engine's reflect loop reads it (see the module docstring).

    It also refuses a request the engine would refuse: ``tags`` beside
    ``tag_groups`` is a 422 on Hindsight 1.2.0.
    """

    facts: ClassVar = [
        (SHARED_TEXT, SHARED_FACT),
        (WORK_NOTE, WORK_PRIVATE),
        (DISCORD_OWN, DISCORD_PRIVATE),
    ]
    mental_model: ClassVar = (MODEL_TEXT, [])

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def reflect(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        assert not (kwargs.get("tags") and kwargs.get("tag_groups")), "the engine answers 422"
        groups = kwargs.get("tag_groups") or []
        if kwargs.get("tags"):
            groups = [{"tags": kwargs["tags"], "match": kwargs.get("tags_match") or "any"}]
        used = [text for text, tags in self.facts if _readable(groups, tags)]
        model_text, model_tags = self.mental_model
        if not kwargs.get("exclude_mental_models") and _readable(groups, model_tags):
            used.append(model_text)
        return {"text": " ".join(used)}


def _mcp(provider: Any, agent_id: str | None, *, private: bool = False):
    return memory_mcp.make_dispatcher(
        provider,
        client_id_resolver=(lambda: agent_id) if agent_id else None,
        private_resolver=lambda: private,
    )


async def _reflect_as(agent_id: str | None, provider: Any, **args: Any) -> str:
    out = await _mcp(provider, agent_id)("memory_reflect", {"query": "who is the client?", **args})
    assert out["status"] == "ok", out
    return out["text"]


# ── the gap, and the tool closing it ───────────────────────────────────────


async def test_another_profiles_private_fact_is_not_used() -> None:
    bank = BankDouble()
    answer = await _reflect_as(DISCORD, HindsightProvider(client=bank, unified_bank=True))
    assert WORK_NOTE not in answer
    assert SHARED_TEXT in answer and DISCORD_OWN in answer
    sent = bank.calls[0]
    assert sent["tag_groups"] == reflect_visibility_scope(DISCORD)
    assert not sent["tags"] and sent["tags_match"] is None


async def test_an_untagged_mental_model_built_from_anothers_private_fact_is_not_used() -> None:
    bank = BankDouble()
    answer = await _reflect_as(DISCORD, HindsightProvider(client=bank, unified_bank=True))
    assert "Acme" not in answer
    assert bank.calls[0]["exclude_mental_models"] is True

    # What the tool sent before this change: the caller's arguments, as given.
    unguarded = BankDouble()
    leaked = (await unguarded.reflect(tag_groups=None, exclude_mental_models=False))["text"]
    assert WORK_NOTE in leaked and MODEL_TEXT in leaked
    # And what a tag filter alone would leave open: the model quotes the fact.
    half = (await BankDouble().reflect(tag_groups=reflect_visibility_scope(DISCORD)))["text"]
    assert WORK_NOTE not in half and "Acme" in half


async def test_the_owner_reads_its_own_private_fact_and_no_mental_model() -> None:
    answer = await _reflect_as(WORK, HindsightProvider(client=BankDouble(), unified_bank=True))
    assert WORK_NOTE in answer and SHARED_TEXT in answer
    assert DISCORD_OWN not in answer
    assert MODEL_TEXT not in answer


async def test_a_caller_with_no_identity_reads_no_private_fact() -> None:
    bank = BankDouble()
    answer = await _reflect_as(None, HindsightProvider(client=bank, unified_bank=True))
    assert answer == SHARED_TEXT
    assert bank.calls[0]["tag_groups"] == reflect_visibility_scope(None)


@pytest.mark.parametrize(
    "args",
    [
        # Each of these asks, one way or another, for what is not the caller's.
        {"exclude_mental_models": False},
        {"tags": ["visibility:private"], "tags_match": "any"},
        {"tags": [f"agent:{WORK}"], "tags_match": "any_strict"},
        {"tag_groups": [{"tags": ["visibility:private"], "match": "any"}]},
        {"tag_groups": [{"or": [{"tags": [f"agent:{WORK}"]}, {"tags": ["kind:durable"]}]}]},
        {"tag_groups": [{"not": {"tags": ["no-such-tag"], "match": "any_strict"}}]},
        {
            "tags": ["kind:durable"],
            "tag_groups": [{"tags": ["visibility:private"]}],
            "exclude_mental_models": False,
        },
    ],
)
async def test_caller_arguments_narrow_the_read_and_never_widen_it(args) -> None:
    bank = BankDouble()
    answer = await _reflect_as(DISCORD, HindsightProvider(client=bank, unified_bank=True), **args)
    assert WORK_NOTE not in answer and "Acme" not in answer
    sent = bank.calls[0]
    assert sent["exclude_mental_models"] is True
    assert sent["tag_groups"][0] == reflect_visibility_scope(DISCORD)[0]
    assert not _readable(sent["tag_groups"], WORK_PRIVATE)


def _used(answer: str) -> set[str]:
    """Which of the bank's texts the answer was built from."""
    return {text for text in (SHARED_TEXT, WORK_NOTE, DISCORD_OWN, MODEL_TEXT) if text in answer}


#: A leaf that matches every fact in the bank, the other profile's included.
_EVERYTHING = {"tags": ["kind:durable"], "match": "any"}
_PRIVATE_LEAF = {"tags": ["visibility:private"], "match": "any"}
_WORK_LEAF = {"tags": [f"agent:{WORK}"], "match": "any_strict"}


@pytest.mark.parametrize(
    "tag_groups",
    [
        # A top-level ``or`` whose branches, between them, admit everything.
        [{"or": [_PRIVATE_LEAF, _EVERYTHING]}],
        [{"or": [_WORK_LEAF, {"not": _WORK_LEAF}]}],
        [{"or": [{"and": [_PRIVATE_LEAF, _WORK_LEAF]}, _EVERYTHING]}],
        # The visibility rule itself, restated for the other profile.
        [{"or": [{"not": {**_PRIVATE_LEAF, "match": "any_strict"}}, _WORK_LEAF]}],
        # Two ``or`` groups at the top level.
        [{"or": [_WORK_LEAF, _EVERYTHING]}, {"or": [_PRIVATE_LEAF, _EVERYTHING]}],
    ],
)
async def test_a_top_level_or_cannot_re_admit_another_agents_private_fact(tag_groups) -> None:
    """Alone, each of these groups reads the other profile's private note. The
    engine ANDs the caller's groups with the visibility group, so the ``or``
    only chooses among facts the caller may already read."""
    assert _readable(tag_groups, WORK_PRIVATE)
    bank = BankDouble()
    provider = HindsightProvider(client=bank, unified_bank=True)
    answer = await _reflect_as(DISCORD, provider, tag_groups=tag_groups)
    assert WORK_NOTE not in answer and "Acme" not in answer
    assert _used(answer) <= {SHARED_TEXT, DISCORD_OWN}
    assert SHARED_TEXT in answer
    sent = bank.calls[0]["tag_groups"]
    assert sent == [*reflect_visibility_scope(DISCORD), *tag_groups]
    assert not _readable(sent, WORK_PRIVATE)


@pytest.mark.parametrize(
    "tag_groups",
    [
        # Negations that are true of the other profile's private note.
        [{"not": {"tags": ["no-such-tag"], "match": "any_strict"}}],
        [{"not": {"tags": [f"agent:{DISCORD}"], "match": "any_strict"}}],
        [{"not": {"not": _PRIVATE_LEAF}}],
        [{"not": {"not": _WORK_LEAF}}],
        # The visibility rule negated: "only what I may not read".
        [{"not": {"not": {**_PRIVATE_LEAF, "match": "any_strict"}}}],
        [{"not": {"or": [{"not": _PRIVATE_LEAF}, {"tags": [f"agent:{DISCORD}"]}]}}],
    ],
)
async def test_a_not_cannot_re_admit_another_agents_private_fact(tag_groups) -> None:
    assert _readable(tag_groups, WORK_PRIVATE)
    bank = BankDouble()
    provider = HindsightProvider(client=bank, unified_bank=True)
    answer = await _reflect_as(DISCORD, provider, tag_groups=tag_groups)
    assert WORK_NOTE not in answer and "Acme" not in answer
    sent = bank.calls[0]["tag_groups"]
    assert sent[0] == reflect_visibility_scope(DISCORD)[0]
    assert not _readable(sent, WORK_PRIVATE)
    # Nothing but shared facts and the caller's own can be in the answer.
    assert _used(answer) <= {SHARED_TEXT, DISCORD_OWN}


@pytest.mark.parametrize(
    "caller",
    [
        f"{WORK}:evil",
        f"{WORK} ",
        f" {WORK}",
        f"{WORK}\t",
        f"agent:{WORK}",
        f"x {WORK}",
        f"{WORK} visibility:private",
        "visibility:private",
        "a:b:c",
        "with space",
    ],
)
async def test_an_identity_with_a_colon_or_whitespace_reads_nobody_elses_private_fact(
    caller,
) -> None:
    """An odd identity stays one opaque string on the way to the engine: it
    cannot be made to look like another agent's ``agent:<id>`` tag."""
    bank = BankDouble()
    answer = await _reflect_as(caller, HindsightProvider(client=bank, unified_bank=True))
    assert answer == SHARED_TEXT
    sent = bank.calls[0]
    assert sent["tag_groups"] == reflect_visibility_scope(caller)
    assert sent["tag_groups"][0]["or"][1] == {"tags": [f"agent:{caller}"], "match": "any_strict"}
    assert sent["exclude_mental_models"] is True


async def test_caller_tags_travel_inside_the_groups() -> None:
    """The engine refuses ``tags`` beside ``tag_groups``, so the tool can
    accept both from a caller and still send a request the engine takes."""
    bank = BankDouble()
    own_group = {"tags": [f"agent:{DISCORD}"], "match": "any_strict"}
    answer = await _reflect_as(
        DISCORD,
        HindsightProvider(client=bank, unified_bank=True),
        tags=["kind:durable"],
        tags_match="all_strict",
        tag_groups=[own_group],
    )
    sent = bank.calls[0]
    assert not sent["tags"] and sent["tags_match"] is None
    assert sent["tag_groups"] == [
        *reflect_visibility_scope(DISCORD, ["kind:durable"], "all_strict"),
        own_group,
    ]
    assert answer == DISCORD_OWN


async def test_split_banks_keep_their_mental_models_and_the_tag_filter() -> None:
    bank = BankDouble()
    answer = await _reflect_as(DISCORD, HindsightProvider(client=bank, unified_bank=False))
    sent = bank.calls[0]
    assert sent["bank_id"] == "shared"
    assert sent["exclude_mental_models"] is False
    assert MODEL_TEXT in answer
    assert sent["tag_groups"] == reflect_visibility_scope(DISCORD)
    # A caller may still ask for mental models to be left out.
    await _reflect_as(
        DISCORD, HindsightProvider(client=bank, unified_bank=False), exclude_mental_models=True
    )
    assert bank.calls[1]["exclude_mental_models"] is True


async def test_the_private_toggle_reads_the_callers_own_bank_when_banks_are_split() -> None:
    bank = BankDouble()
    provider = HindsightProvider(client=bank, unified_bank=False)
    out = await _mcp(provider, "hermes", private=True)("memory_reflect", {"query": "q"})
    assert out["status"] == "ok", out
    assert bank.calls[0]["bank_id"] == "private__hermes"
    assert bank.calls[0]["tag_groups"] == reflect_visibility_scope("hermes")


# ── one implementation for both entry points ───────────────────────────────


def _rest_reflect(provider: Any, agent_id: str, body: dict[str, Any]) -> None:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory")
    app.state.memory_provider = provider
    with TestClient(app) as http:
        response = http.post("/api/memory/reflect", json=body, headers={"X-hal0-Agent": agent_id})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("unified", [True, False])
@pytest.mark.parametrize(
    "request_body",
    [
        {"query": "q"},
        {"query": "q", "tags": ["project:atlas"], "tags_match": "all_strict"},
        {"query": "q", "budget": "mid", "max_tokens": 2048, "tags": ["kind:durable"]},
    ],
)
async def test_the_mcp_tool_and_the_route_send_the_engine_the_same_scope(
    unified, request_body
) -> None:
    rest_engine, mcp_engine = AsyncMock(), AsyncMock()
    rest_engine.reflect.return_value = mcp_engine.reflect.return_value = {"text": "ok"}
    _rest_reflect(
        HindsightProvider(client=rest_engine, unified_bank=unified), DISCORD, request_body
    )
    out = await _mcp(HindsightProvider(client=mcp_engine, unified_bank=unified), DISCORD)(
        "memory_reflect", request_body
    )
    assert out["status"] == "ok", out
    via_rest, via_mcp = rest_engine.reflect.await_args.kwargs, mcp_engine.reflect.await_args.kwargs
    for key in ("bank_id", "tags", "tags_match", "tag_groups", "exclude_mental_models", "budget"):
        assert via_mcp[key] == via_rest[key], key
    assert via_mcp["exclude_mental_models"] is unified
    assert via_mcp["tag_groups"] == reflect_visibility_scope(
        DISCORD, request_body.get("tags"), request_body.get("tags_match")
    )


# ── the parameter an operator surface can leave off ────────────────────────


async def test_enforce_visibility_off_sends_the_request_as_given() -> None:
    """For a surface that names a bank and carries no caller identity. No
    entry point in hal0 uses it today: the bank console's reflect is a
    passthrough that never reaches the provider."""
    engine = AsyncMock()
    engine.reflect.return_value = {"text": "ok"}
    provider = HindsightProvider(client=engine, unified_bank=True)
    groups = [{"tags": ["topic:cats"]}]
    await provider.reflect("q", client_id=DISCORD, tag_groups=groups, enforce_visibility=False)
    await provider.reflect(
        "q", client_id=DISCORD, tags=["a"], tags_match="all", enforce_visibility=False
    )
    first, second = (call.kwargs for call in engine.reflect.await_args_list)
    assert first["tag_groups"] is groups and first["tags"] is None
    assert first["exclude_mental_models"] is False
    assert second["tags"] == ["a"] and second["tags_match"] == "all"
    assert second["tag_groups"] is None


async def test_enforcement_is_the_default_for_a_direct_provider_call() -> None:
    bank = BankDouble()
    provider = HindsightProvider(client=bank, unified_bank=True)
    answer = (await provider.reflect("q", client_id=DISCORD))["text"]
    assert WORK_NOTE not in answer and MODEL_TEXT not in answer
    anonymous = (await provider.reflect("q"))["text"]
    assert anonymous == SHARED_TEXT
