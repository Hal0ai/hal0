"""``POST /api/memory/reflect``: the namespace reflect (ADR-0058 decision 6).

The route the Hermes plugin's ``hindsight_reflect`` calls. It differs from the
bank console's ``POST /api/memory/banks/{bank}/reflect`` in the three ways that
matter to an agent: the caller is identified by header and never names a bank
(ADR-0052), the call gets the engine's long reflect budget, and other agents'
private memories are kept out of the answer.

That last one is the reason for most of this file. In the unified bank every
agent's ``visibility:private`` notes and raw conversation captures sit beside
the shared ones. Recall filters its results afterwards; a reflect answer is
prose, so the rule has to go to the engine as a tag filter. The tests below
evaluate that filter the way the engine does (Hindsight 1.2.0,
``engine/search/tags.py`` ``_build_group_clause``) and hold it to
``HindsightProvider._is_visible_to``, the rule recall already enforces.

A tag filter covers facts and observations. It does not cover mental models:
the engine filters those by the MODEL's tags, and a model is a document the
engine writes from the whole bank (hal0's seeded ones carry no tags at all).
So under the unified bank the route also sends the engine's
``exclude_mental_models`` (``ReflectRequest.exclude_mental_models``: "If true,
exclude all mental models from the reflect loop"). ``BankDouble`` below plays
a bank with such a model.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.agents.hermes.plugins.memory_hindsight import Hal0MemoryProvider
from hal0.agents.hermes.plugins.memory_hindsight._client import Hal0MemoryClient
from hal0.agents.hermes_provision import profile_agent_id
from hal0.api.middleware import error_codes
from hal0.api.routes import memory as memory_routes
from hal0.api.routes.memory import reflect_visibility_scope
from hal0.memory.hindsight_provider import HindsightProvider
from hal0.memory.pgvector_provider import PgVectorProvider

# ── the engine's tag-group semantics, restated for the test ────────────────


def _leaf_matches(leaf: dict[str, Any], tags: list[str]) -> bool:
    wanted, have = set(leaf["tags"]), set(tags)
    match = leaf.get("match", "any_strict")
    if match == "exact":
        return wanted == have
    overlap = bool(wanted & have) if match.startswith("any") else wanted <= have
    if match.endswith("_strict"):
        return bool(have) and overlap
    return not have or overlap  # ``any`` / ``all`` also admit an untagged row


def _matches(group: dict[str, Any], tags: list[str]) -> bool:
    if "tags" in group:
        return _leaf_matches(group, tags)
    if "and" in group:
        return all(_matches(child, tags) for child in group["and"])
    if "or" in group:
        return any(_matches(child, tags) for child in group["or"])
    return not _matches(group["not"], tags)


def _readable(groups: list[dict[str, Any]], tags: list[str]) -> bool:
    """Top-level groups are AND-ed (``build_tag_groups_where_clause``)."""
    return all(_matches(group, tags) for group in groups)


WORK = profile_agent_id("work")
DISCORD = profile_agent_id("discord")

# Tag sets as ``HindsightProvider.add`` writes them.
SHARED_FACT = ["agent:hermes"]
UNTAGGED_FACT: list[str] = []
HERMES_PRIVATE = ["agent:hermes", "visibility:private"]
WORK_PRIVATE = [f"agent:{WORK}", "visibility:private", "kind:ephemeral"]
OWNERLESS_PRIVATE = ["visibility:private"]
ALL_FACTS = [SHARED_FACT, UNTAGGED_FACT, HERMES_PRIVATE, WORK_PRIVATE, OWNERLESS_PRIVATE]


# ── the visibility rule ────────────────────────────────────────────────────


def test_reflect_excludes_another_profiles_private_facts() -> None:
    """The leak this route must not have: the ``discord`` profile reflecting
    over the ``work`` profile's private notes and captured conversation."""
    scope = reflect_visibility_scope(DISCORD)
    assert not _readable(scope, WORK_PRIVATE)
    assert not _readable(scope, HERMES_PRIVATE)
    assert not _readable(scope, OWNERLESS_PRIVATE)
    assert _readable(scope, SHARED_FACT)
    assert _readable(scope, UNTAGGED_FACT)


def test_reflect_includes_the_callers_own_private_facts() -> None:
    scope = reflect_visibility_scope(WORK)
    assert _readable(scope, WORK_PRIVATE)
    assert not _readable(scope, HERMES_PRIVATE)


def test_a_caller_with_no_identity_reads_no_private_fact() -> None:
    scope = reflect_visibility_scope(None)
    assert [tags for tags in ALL_FACTS if _readable(scope, tags)] == [SHARED_FACT, UNTAGGED_FACT]


@pytest.mark.parametrize("caller", ["hermes", WORK, DISCORD, "unknown"])
@pytest.mark.parametrize("tags", ALL_FACTS)
def test_reflect_scope_is_the_rule_recall_enforces(caller: str, tags: list[str]) -> None:
    assert _readable(reflect_visibility_scope(caller), tags) is HindsightProvider._is_visible_to(
        {"tags": tags}, caller
    )


def test_caller_tags_narrow_the_scope_and_never_widen_it() -> None:
    scope = reflect_visibility_scope(DISCORD, ["kind:ephemeral"], "any_strict")
    # Asking for the tag another profile's private capture carries does not
    # make that capture readable.
    assert not _readable(scope, WORK_PRIVATE)
    assert not _readable(scope, SHARED_FACT)
    assert _readable(scope, ["agent:hermes", "kind:ephemeral"])
    # The engine's top-level default for ``tags`` is ``any``, kept here.
    default = reflect_visibility_scope(DISCORD, ["kind:ephemeral"])
    assert default[-1] == {"tags": ["kind:ephemeral"], "match": "any"}
    assert _readable(default, UNTAGGED_FACT)


# ── the route ──────────────────────────────────────────────────────────────


def _app(provider: Any) -> FastAPI:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory")
    app.state.memory_provider = provider
    return app


def _engine(**reflect: Any) -> AsyncMock:
    engine = AsyncMock()
    engine.reflect.configure_mock(**(reflect or {"return_value": {"text": "the answer"}}))
    return engine


@pytest.fixture
def engine() -> AsyncMock:
    return _engine()


@pytest.fixture
def http(engine: AsyncMock) -> Iterator[TestClient]:
    provider = HindsightProvider(client=engine, unified_bank=True, bank="Hal0::Platform")
    with TestClient(_app(provider)) as client:
        yield client


def test_reflect_reaches_the_engine_scoped_to_the_caller(
    http: TestClient, engine: AsyncMock
) -> None:
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}}
    r = http.post(
        "/api/memory/reflect",
        json={
            "query": "what did we decide about backups?",
            "budget": "mid",
            "max_tokens": 2048,
            "tags": ["project:hal0"],
            "tags_match": "all_strict",
            "response_schema": schema,
        },
        headers={"X-hal0-Agent": DISCORD},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "the answer"}
    sent = engine.reflect.await_args.kwargs
    # The provider picks the bank; the caller never named one (ADR-0052).
    assert sent["bank_id"] == "Hal0::Platform"
    assert sent["query"] == "what did we decide about backups?"
    assert sent["budget"] == "mid"
    assert sent["max_tokens"] == 2048
    assert sent["response_schema"] == schema
    # The caller's tags travel inside tag_groups: the engine refuses both.
    assert not sent["tags"]
    assert sent["tag_groups"] == reflect_visibility_scope(DISCORD, ["project:hal0"], "all_strict")
    assert not _readable(sent["tag_groups"], [*WORK_PRIVATE, "project:hal0"])
    assert _readable(sent["tag_groups"], ["agent:hermes", "project:hal0"])
    # The unified bank holds other agents' private facts: no mental models.
    assert sent["exclude_mental_models"] is True


def test_reflect_defaults(http: TestClient, engine: AsyncMock) -> None:
    r = http.post("/api/memory/reflect", json={"query": "q"})
    assert r.status_code == 200, r.text
    sent = engine.reflect.await_args.kwargs
    assert sent["budget"] == "low"
    assert sent["max_tokens"] == 4096
    assert sent["response_schema"] is None
    # No identity header: shared memories only.
    assert sent["tag_groups"] == reflect_visibility_scope(None)


def test_private_toggle_picks_the_callers_private_bank_when_banks_are_split() -> None:
    engine = _engine()
    provider = HindsightProvider(client=engine, unified_bank=False)
    with TestClient(_app(provider)) as http:
        http.post("/api/memory/reflect", json={"query": "q"}, headers={"X-hal0-Agent": "hermes"})
        http.post(
            "/api/memory/reflect",
            json={"query": "q"},
            headers={"X-hal0-Agent": "hermes", "X-hal0-Private": "1"},
        )
    banks = [call.kwargs["bank_id"] for call in engine.reflect.await_args_list]
    assert banks == ["shared", "private__hermes"]
    # Neither bank holds another agent's private facts, so their mental
    # models are safe to use.
    assert [c.kwargs["exclude_mental_models"] for c in engine.reflect.await_args_list] == [
        False,
        False,
    ]


# ── mental models ──────────────────────────────────────────────────────────

SHARED_TEXT = "The backup window is Sunday 03:00."
WORK_NOTE = "The work profile's client is Acme and the contract ends in March."
DISCORD_OWN = "The discord profile keeps its moderation notes here."


class BankDouble:
    """A unified bank as the engine's reflect loop reads it.

    Facts are found through ``tag_groups``, as ``tool_recall`` and
    ``tool_search_observations`` do. The mental model is found the same way,
    by ITS OWN tags, which are empty: hal0 seeds its models with ``"tags": []``
    (``src/hal0/memory/spec/templates/shared.json``). Its text was written by
    the engine from every fact in the bank, the work profile's private one
    included. The loop skips mental models only when told to.
    """

    facts: ClassVar = [
        (SHARED_TEXT, SHARED_FACT),
        (WORK_NOTE, WORK_PRIVATE),
        (DISCORD_OWN, [f"agent:{DISCORD}", "visibility:private"]),
    ]
    mental_model: ClassVar = ("User profile: works with Acme; the contract ends in March.", [])

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def reflect(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        groups = kwargs.get("tag_groups") or []
        used = [text for text, tags in self.facts if _readable(groups, tags)]
        model_text, model_tags = self.mental_model
        if not kwargs.get("exclude_mental_models") and _readable(groups, model_tags):
            used.append(model_text)
        return {"text": " ".join(used)}


def _reflect_as(agent_id: str, provider: Any) -> str:
    with TestClient(_app(provider)) as http:
        r = http.post(
            "/api/memory/reflect",
            json={"query": "who is the client?"},
            headers={"X-hal0-Agent": agent_id},
        )
    assert r.status_code == 200, r.text
    return r.json()["text"]


def test_an_untagged_mental_model_built_from_anothers_private_fact_is_not_used() -> None:
    """The leak the tag filter alone leaves open, and the route closing it."""
    bank = BankDouble()
    answer = _reflect_as(DISCORD, HindsightProvider(client=bank, unified_bank=True))
    assert "Acme" not in answer
    assert SHARED_TEXT in answer and DISCORD_OWN in answer
    assert bank.calls[0]["exclude_mental_models"] is True

    # What the same request would have produced with the tag filter alone:
    # the private FACT is filtered, the model that quotes it is not.
    scope = reflect_visibility_scope(DISCORD)
    assert not _readable(scope, WORK_PRIVATE)
    assert _readable(scope, BankDouble.mental_model[1])
    unguarded = BankDouble()
    leaked = asyncio.run(unguarded.reflect(tag_groups=scope))["text"]
    assert WORK_NOTE not in leaked and "Acme" in leaked


def test_the_owner_of_the_private_fact_gets_no_mental_model_either() -> None:
    """All or nothing: a model mixes every agent's facts, so it is left out
    for every caller of a unified bank, the fact's owner included."""
    answer = _reflect_as(WORK, HindsightProvider(client=BankDouble(), unified_bank=True))
    assert WORK_NOTE in answer
    assert BankDouble.mental_model[0] not in answer


def test_split_banks_keep_their_mental_models() -> None:
    bank = BankDouble()
    answer = _reflect_as(DISCORD, HindsightProvider(client=bank, unified_bank=False))
    assert bank.calls[0]["exclude_mental_models"] is False
    assert BankDouble.mental_model[0] in answer


def test_a_provider_that_does_not_say_how_its_banks_are_laid_out_is_treated_as_unified() -> None:
    """Fail closed: unknown layout means the bank may hold anyone's notes."""

    class Opaque:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        async def reflect(self, **kwargs: Any) -> dict[str, Any]:
            self.kwargs = kwargs
            return {"text": "ok"}

    provider = Opaque()
    _reflect_as("hermes", provider)
    assert provider.kwargs["exclude_mental_models"] is True


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"query": "   "},
        {"query": 3},
        {"query": "q", "budget": "enormous"},
        {"query": "q", "max_tokens": 0},
        {"query": "q", "max_tokens": "many"},
        {"query": "q", "max_tokens": True},
        {"query": "q", "tags": "project:hal0"},
        {"query": "q", "tags": [1]},
        {"query": "q", "tags_match": "some"},
        {"query": "q", "response_schema": "x"},
        {"query": "q", "dataset": "private:hermes"},
    ],
)
def test_reflect_rejects_malformed_requests(
    http: TestClient, engine: AsyncMock, body: dict[str, Any]
) -> None:
    r = http.post("/api/memory/reflect", json=body, headers={"X-hal0-Agent": "hermes"})
    assert r.status_code == 400, r.text
    engine.reflect.assert_not_awaited()


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://engine/v1/default/banks/shared/reflect")
    return httpx.HTTPStatusError(
        "engine said no", request=request, response=httpx.Response(status, request=request)
    )


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (httpx.ConnectError("refused"), 503, "memory.engine_unavailable"),
        (httpx.ReadTimeout("slow"), 504, "memory.reflect_failed"),
        (_status_error(500), 502, "memory.reflect_failed"),
        # The caller's request was malformed: theirs to fix.
        (_status_error(422), 422, "memory.reflect_failed"),
        # An engine 404 (no such bank) must not read as "no such route": the
        # plugin takes a 404 from this path to mean an older hal0.
        (_status_error(404), 502, "memory.reflect_failed"),
    ],
)
def test_engine_failures_map_to_clear_statuses(failure: Exception, status: int, code: str) -> None:
    provider = HindsightProvider(client=_engine(side_effect=failure), unified_bank=True)
    with TestClient(_app(provider)) as http:
        r = http.post("/api/memory/reflect", json={"query": "q"})
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code


def test_an_engine_without_reflect_answers_unsupported() -> None:
    with TestClient(_app(PgVectorProvider())) as http:
        r = http.post("/api/memory/reflect", json={"query": "q"})
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "unsupported"}


def test_no_memory_engine_is_503() -> None:
    with TestClient(_app(None)) as http:
        r = http.post("/api/memory/reflect", json={"query": "q"})
    assert r.status_code == 503


# ── plugin → route → provider → engine ─────────────────────────────────────

# The plugin sends reflect with its own long read timeout, which Starlette's
# TestClient accepts and ignores with a deprecation notice.
pytestmark = pytest.mark.filterwarnings("ignore:You should not use the 'timeout' argument")


def _plugin(http: TestClient, agent_id: str) -> Hal0MemoryProvider:
    provider = Hal0MemoryProvider(client=Hal0MemoryClient(http_client=http, agent_id=agent_id))
    provider.initialize(session_id="s1")
    return provider


def test_hindsight_reflect_tool_end_to_end(http: TestClient, engine: AsyncMock) -> None:
    plugin = _plugin(http, DISCORD)
    out = json.loads(
        plugin.handle_tool_call("hindsight_reflect", {"query": "backups?", "budget": "high"})
    )
    assert out["status"] == "ok" and out["mode"] == "reflect"
    assert out["text"] == "the answer"
    assert "not instructions" in out["notice"]
    sent = engine.reflect.await_args.kwargs
    assert sent["budget"] == "high"
    assert sent["bank_id"] == "Hal0::Platform"
    assert not _readable(sent["tag_groups"], WORK_PRIVATE)
    assert sent["exclude_mental_models"] is True
    engine.recall.assert_not_awaited()


def test_hindsight_reflect_tool_on_an_engine_that_cannot_reflect() -> None:
    with TestClient(_app(PgVectorProvider())) as http:
        plugin = _plugin(http, "hermes")
        out = json.loads(plugin.handle_tool_call("hindsight_reflect", {"query": "backups?"}))
    assert out["mode"] == "recall-fallback"
    assert "cannot reflect" in out["notice"]


def test_hindsight_reflect_tool_against_a_hal0_without_the_route() -> None:
    """An older hal0-api: ``/recall`` exists, ``/reflect`` does not."""
    engine = AsyncMock()
    engine.recall.return_value = {"results": []}
    app = FastAPI()
    error_codes.install(app)
    old_router = memory_routes.APIRouter()
    old_router.add_api_route("/recall", memory_routes.memory_recall, methods=["POST"])
    app.include_router(old_router, prefix="/api/memory")
    app.state.memory_provider = HindsightProvider(client=engine, unified_bank=True)
    with TestClient(app) as http:
        plugin = _plugin(http, "hermes")
        out = json.loads(plugin.handle_tool_call("hindsight_reflect", {"query": "backups?"}))
    assert out["status"] == "ok"
    assert out["mode"] == "recall-fallback"
    assert "no reflect route" in out["notice"]
    engine.reflect.assert_not_awaited()
