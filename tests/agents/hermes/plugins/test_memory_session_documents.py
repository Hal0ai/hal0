"""One memory document per Hermes session (#2017, ADR-0058 decision 5).

The hal0-memory plugin used to send no ``document_id``, so every turn became
its own randomly named document. Session writes (turn capture, the
pre-compress continuity note, the session-end checkpoint) are now appended to
one document per session, ``<client>[-<profile>]-<session-id>``.

Three engine and server facts shape the contract these tests pin:

* Hindsight REPLACES a document when a retain reuses its ``document_id``,
  unless the item says ``update_mode: "append"``.
* Every released hal0-api reads ``document_id`` off the add body and knows no
  ``update_mode``; it ignores body keys it does not read. So a session id sent
  as ``document_id`` to such a server replaces the session's document, while a
  session id sent in a field it does not know costs nothing. The plugin
  therefore sends ``append_document_id`` and never ``document_id`` for a
  session write. The two server doubles below play the two generations.
* An append re-stamps the retain's tags onto every memory the document already
  holds, and a changed tag set deletes the observations built on them. So all
  session writes carry one fixed tag set and the write kind is metadata.

``tests/api/test_memory_session_documents_route.py`` covers the real route and
the engine call; this file is the plugin side.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import Mock

import pytest

from hal0.agents.hermes.plugins.memory_hindsight import Hal0MemoryProvider
from hal0.agents.hermes.plugins.memory_hindsight._client import Hal0MemoryClient
from hal0.agents.hermes.plugins.memory_hindsight.provider import (
    _SESSION_TAGS,
    _session_document_id,
)
from hal0.agents.hermes_provision import profile_agent_id
from hal0.memory.namespace import DOCUMENT_ID_PATTERN

# The shape Hermes mints for an interactive session (upstream
# ``hermes_state_ids.new_session_id``): ``<YYYYMMDD_HHMMSS>_<6 hex>``.
SESSION = "20261110_101500_ab12cd"
SESSION_DOC = f"hermes-{SESSION}"

# What the envelope (ADR-0058 decision 4) moves out of tags into
# ``metadata["lifecycle"]``. None of them may be a session tag.
LIFECYCLE_WORDS = {"checkpoint", "session-end", "continuity", "pre-compress", "bootstrap", "chat"}

MESSAGES = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]


# ── two generations of hal0-api, as the add route behaves ──────────────────


class _Response:
    status_code = 200
    text = ""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


class _Server:
    """The part of ``POST /api/memory/add`` that decides which document a
    write lands in. ``store`` maps document id to the texts it holds, and may
    be shared between two servers (a rollback keeps the data)."""

    def __init__(self, store: dict[str, list[str]] | None = None) -> None:
        self.store: dict[str, list[str]] = {} if store is None else store
        self.bodies: list[dict[str, Any]] = []
        self._minted = 0

    def request(self, method: str, path: str, **kwargs: Any) -> _Response:
        assert (method, path) == ("POST", "/api/memory/add")
        body = kwargs["json"]
        self.bodies.append(body)
        return _Response(self.add(body))

    def _fresh(self) -> str:
        self._minted += 1
        return f"uuid-{id(self)}-{self._minted}"

    def add(self, body: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def acted_on_ids(self) -> list[str]:
        """Every id this server received in a field it acts on."""
        raise NotImplementedError


class OldServer(_Server):
    """A released hal0-api (the add route as of v1.5.0, v1.7.1 and v1.8.0).

    It reads ``document_id`` and replaces the document stored under it; it has
    never heard of ``update_mode`` or ``append_document_id`` and ignores both.
    """

    def add(self, body: dict[str, Any]) -> dict[str, Any]:
        doc_id = body.get("document_id") or self._fresh()
        self.store[doc_id] = [body["text"]]  # replace: the engine's default
        return {"id": doc_id, "timestamp": "t"}

    def acted_on_ids(self) -> list[str]:
        return [b["document_id"] for b in self.bodies if "document_id" in b]


class NewServer(_Server):
    """The add route of this change, on the Hindsight provider."""

    def add(self, body: dict[str, Any]) -> dict[str, Any]:
        if "append_document_id" in body:
            assert "document_id" not in body and "update_mode" not in body
            doc_id = body["append_document_id"]
            self.store.setdefault(doc_id, []).append(body["text"])
            return {"id": doc_id, "timestamp": "t", "update_mode": "append"}
        doc_id = body.get("document_id") or self._fresh()
        self.store[doc_id] = [body["text"]]
        return {"id": doc_id, "timestamp": "t"}

    def acted_on_ids(self) -> list[str]:
        return [b.get("document_id") or b.get("append_document_id") or "" for b in self.bodies]


class _Switch:
    """The plugin's HTTP client, pointed at whichever server is live."""

    def __init__(self, server: _Server) -> None:
        self.server = server

    def request(self, method: str, path: str, **kwargs: Any) -> _Response:
        return self.server.request(method, path, **kwargs)


def _plugin(
    server: Any, *, agent_id: str = "hermes", session_id: str = SESSION, **init: Any
) -> Hal0MemoryProvider:
    provider = Hal0MemoryProvider(client=Hal0MemoryClient(http_client=server, agent_id=agent_id))
    provider.initialize(session_id=session_id, **init)
    return provider


def _turn(provider: Hal0MemoryProvider, n: int = 0) -> None:
    provider.sync_turn(f"question {n}", f"answer {n}", session_id=SESSION)


def _pre_compress(provider: Hal0MemoryProvider) -> None:
    provider.on_pre_compress(MESSAGES)


def _session_end(provider: Hal0MemoryProvider) -> None:
    provider.on_session_end(MESSAGES)


# ── the id ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("agent_id", "expected"),
    [
        ("hermes", f"hermes-{SESSION}"),
        (profile_agent_id("work"), f"hermes-work-{SESSION}"),
        # An identity an operator configured is the client part as it stands.
        ("ops-bot", f"ops-bot-{SESSION}"),
    ],
)
def test_session_document_id_is_client_profile_session(agent_id: str, expected: str) -> None:
    assert _session_document_id(agent_id, SESSION) == expected


def test_no_session_id_means_no_stable_id() -> None:
    assert _session_document_id("hermes", "") is None


@pytest.mark.parametrize(
    ("agent_id", "session_id"),
    [
        ("hermes", "api_1762769700_9f8e7d6c"),
        ("hermes", "room_" + "a" * 32),
        ("hermes", "4b1c0d2e-3f4a-4b5c-8d6e-7f8091a2b3c4"),
        # The API server takes a caller-supplied session id as it comes.
        ("hermes", "chat/üñí code: 42"),
        ("hermes", "x" * 200),
        (profile_agent_id("a-rather-long-profile-name-for-a-team-workspace"), SESSION),
    ],
)
def test_session_document_id_always_fits_hal0_grammar(agent_id: str, session_id: str) -> None:
    """Whatever Hermes hands over, the id passes the add route's check
    (``hal0.memory.namespace.DOCUMENT_ID_PATTERN``) and is stable."""
    doc_id = _session_document_id(agent_id, session_id)
    assert doc_id is not None
    assert DOCUMENT_ID_PATTERN.match(doc_id)
    assert len(doc_id) <= 64
    assert doc_id == _session_document_id(agent_id, session_id)


def test_rewritten_ids_stay_distinct() -> None:
    """Sanitising or cutting an id must not fold two sessions into one document."""
    long_a = "s" * 100 + "a"
    long_b = "s" * 100 + "b"
    assert _session_document_id("hermes", long_a) != _session_document_id("hermes", long_b)
    assert _session_document_id("hermes", "a/b") != _session_document_id("hermes", "a_b")


# ── a capable server: one appended document from the first write ───────────


@pytest.mark.parametrize(
    ("write", "lifecycle", "kind"),
    [
        (_turn, "chat", "raw_turn"),
        (_pre_compress, "pre-compress", "continuity"),
        (_session_end, "session-end", "checkpoint"),
    ],
)
@pytest.mark.parametrize(
    ("agent_id", "document_id"),
    [
        ("hermes", SESSION_DOC),
        (profile_agent_id("work"), f"hermes-work-{SESSION}"),
    ],
)
def test_session_writes_append_to_the_session_document(
    write: Any, lifecycle: str, kind: str, agent_id: str, document_id: str
) -> None:
    server = NewServer()
    write(_plugin(server, agent_id=agent_id))
    (sent,) = server.bodies
    # The first write already carries it: there is no probe and no warm-up.
    assert sent["append_document_id"] == document_id
    # The session id never travels in the field an older server acts on.
    assert "document_id" not in sent and "update_mode" not in sent
    assert sent["tags"] == list(_SESSION_TAGS)
    assert sent["metadata"]["lifecycle"] == lifecycle
    assert sent["metadata"]["kind"] == kind
    assert sent["metadata"]["visibility"] == "private"


def test_a_session_accumulates_in_one_document() -> None:
    server = NewServer()
    provider = _plugin(server)
    for n in range(3):
        _turn(provider, n)
    _session_end(provider)
    assert list(server.store) == [SESSION_DOC]
    assert len(server.store[SESSION_DOC]) == 4
    assert server.store[SESSION_DOC][0] == "User: question 0\nAssistant: answer 0"


def test_session_writes_share_one_tag_set_with_no_lifecycle_word() -> None:
    """The engine re-stamps an append's tags over the whole document and
    re-consolidates on a change, so the three write kinds must agree."""
    server = NewServer()
    provider = _plugin(server)
    _turn(provider)
    _pre_compress(provider)
    _session_end(provider)
    assert {tuple(sent["tags"]) for sent in server.bodies} == {_SESSION_TAGS}
    assert not LIFECYCLE_WORDS & set(_SESSION_TAGS)


def test_the_turns_own_session_id_wins_over_the_bound_one() -> None:
    """The manager names the session on every ``sync_turn`` (upstream
    ``agent/memory_manager.py`` ``sync_all``). The idempotency key follows the
    document: both name the session the write goes to."""
    server = NewServer()
    provider = _plugin(server, session_id="bound")
    provider.sync_turn("hi", "hello", session_id="other")
    (sent,) = server.bodies
    assert sent["append_document_id"] == "hermes-other"
    assert sent["metadata"]["source_event"].startswith("other:")


def test_the_idempotency_key_uses_the_bound_session_otherwise() -> None:
    server = NewServer()
    _pre_compress(_plugin(server, session_id="bound"))
    assert server.bodies[0]["metadata"]["source_event"].startswith("bound:")


def test_without_a_session_id_each_write_keeps_its_own_document() -> None:
    server = NewServer()
    provider = _plugin(server, session_id="")
    provider.sync_turn("hi", "hello")
    provider.sync_turn("again", "yes")
    assert all("append_document_id" not in b and "document_id" not in b for b in server.bodies)
    assert len(server.store) == 2


@pytest.mark.parametrize("context", ["cron", "flush", "subagent"])
def test_non_primary_contexts_still_write_nothing(context: str) -> None:
    server = NewServer()
    provider = _plugin(server, agent_context=context)
    _turn(provider)
    _pre_compress(provider)
    _session_end(provider)
    assert server.bodies == []


def test_session_switch_moves_later_writes_to_the_new_document() -> None:
    """Hermes rotates the id without re-initialising (/new, /resume, compression).
    The manager ends the old session first, then switches (upstream
    ``commit_session_boundary_async``): the checkpoint stays with the old one."""
    server = NewServer()
    provider = _plugin(server, session_id="old")
    provider.on_session_end(MESSAGES)
    provider.on_session_switch("new", parent_session_id="old", reset=True, reason="new_session")
    provider.on_pre_compress(MESSAGES)
    assert [b["append_document_id"] for b in server.bodies] == ["hermes-old", "hermes-new"]


def test_session_switch_ignores_an_empty_id() -> None:
    server = NewServer()
    provider = _plugin(server, session_id="old")
    provider.on_session_switch("")
    provider.on_session_end(MESSAGES)
    assert server.bodies[0]["append_document_id"] == "hermes-old"


# ── an older hal0-api can never replace a session document ─────────────────


def _seeded_store() -> dict[str, list[str]]:
    """A session a newer hal0 already appended a conversation to."""
    return {SESSION_DOC: [f"turn {n}" for n in range(12)]}


def _assert_untouched(server: OldServer, store: dict[str, list[str]]) -> None:
    assert not [i for i in server.acted_on_ids() if i.startswith("hermes-")], (
        "a session document id reached an older server in a field it acts on"
    )
    assert store[SESSION_DOC] == [f"turn {n}" for n in range(12)]


def test_old_server_never_gets_the_session_id_after_initialize() -> None:
    """The first write of a provider is the dangerous one: nothing has been
    learnt about the server yet. It must already be safe."""
    store = _seeded_store()
    server = OldServer(store)
    provider = _plugin(server)
    _turn(provider)
    _pre_compress(provider)
    _session_end(provider)
    _assert_untouched(server, store)
    assert len(store) == 4  # three fresh documents beside the session's


def test_old_server_never_gets_the_session_id_from_a_fresh_instance_per_write() -> None:
    """A provider built again for the same session (a restart, an evicted
    agent, a resume) starts with no memory of what the server can do."""
    store = _seeded_store()
    server = OldServer(store)
    for n in range(4):
        _turn(_plugin(server), n)
    _assert_untouched(server, store)


def test_old_server_never_gets_the_session_id_after_a_session_switch() -> None:
    store = _seeded_store()
    server = OldServer(store)
    provider = _plugin(server, session_id="elsewhere")
    provider.on_session_switch(SESSION, parent_session_id="elsewhere")
    _pre_compress(provider)
    _session_end(provider)
    _assert_untouched(server, store)


def test_old_server_never_gets_the_session_id_from_an_explicit_turn_session() -> None:
    store = _seeded_store()
    server = OldServer(store)
    provider = _plugin(server, session_id="elsewhere")
    provider.sync_turn("hi", "hello", session_id=SESSION)
    _assert_untouched(server, store)


def test_a_server_rolled_back_under_a_live_process_keeps_the_conversation() -> None:
    """hal0-api is rolled back while Hermes keeps running: the plugin has seen
    appends succeed and has no reason to doubt the server. The next write must
    still not replace anything."""
    store: dict[str, list[str]] = {}
    new, old = NewServer(store), OldServer(store)
    live = _Switch(new)
    provider = _plugin(live)
    for n in range(5):
        _turn(provider, n)
    before = list(store[SESSION_DOC])
    assert len(before) == 5

    live.server = old
    for n in range(5, 8):
        _turn(provider, n)
    _session_end(provider)

    assert store[SESSION_DOC] == before
    assert not [i for i in old.acted_on_ids() if i.startswith("hermes-")]
    assert len(old.bodies) == 4


def test_an_unconfirmed_append_stops_the_field(caplog: pytest.LogCaptureFixture) -> None:
    """The second line: a server that does not report the append is not
    appending, so the field is dropped for the rest of the session."""
    server = OldServer()
    provider = _plugin(server)
    with caplog.at_level(logging.WARNING):
        _turn(provider, 0)
        _turn(provider, 1)
        _session_end(provider)
    first, second, third = server.bodies
    assert first["append_document_id"] == SESSION_DOC
    assert "append_document_id" not in second and "append_document_id" not in third
    assert caplog.text.count("did not append to the session document") == 1


def test_initialize_gives_the_next_session_a_fresh_chance() -> None:
    live = _Switch(OldServer())
    provider = _plugin(live)
    _turn(provider, 0)
    _turn(provider, 1)
    live.server = new = NewServer()
    provider.initialize(session_id="next")
    provider.sync_turn("hi", "hello")
    assert new.bodies[-1]["append_document_id"] == "hermes-next"


# ── writes that are not session captures ───────────────────────────────────


def _mocked(agent_id: str = "hermes", session_id: str = SESSION) -> tuple[Hal0MemoryProvider, Mock]:
    fake = Mock(spec=Hal0MemoryClient)
    fake.agent_id = agent_id
    fake.add.return_value = {"id": "fresh", "timestamp": "t"}
    provider = Hal0MemoryProvider(client=fake)
    provider.initialize(session_id=session_id)
    return provider, fake


def test_delegation_and_builtin_mirror_keep_per_call_documents() -> None:
    provider, fake = _mocked()
    provider.on_delegation("task", "result", child_session_id="child")
    provider.on_memory_write("add", "user", "likes tea")
    for call in fake.add.call_args_list:
        assert "document_id" not in call.kwargs and "append_document_id" not in call.kwargs


def test_explicit_retain_keeps_a_per_call_document() -> None:
    provider, fake = _mocked()
    out = json.loads(provider.handle_tool_call("hindsight_retain", {"text": "a fact"}))
    sent = fake.add.call_args.kwargs
    assert "document_id" not in sent and "append_document_id" not in sent
    assert out["visibility"] == "shared"


def test_explicit_retain_passes_the_callers_document_id() -> None:
    """A caller-named id is a plain upsert (no append): revising a fact replaces it."""
    provider, fake = _mocked()
    provider.handle_tool_call("hindsight_retain", {"text": "v2", "document_id": "deploy-notes"})
    sent = fake.add.call_args.kwargs
    assert sent["document_id"] == "deploy-notes"
    assert "append_document_id" not in sent


@pytest.mark.parametrize(
    ("agent_id", "document_id"),
    [
        # This session's document, an earlier session's, one never seen here.
        ("hermes", SESSION_DOC),
        ("hermes", "hermes-20260101_000000_aaaaaa"),
        ("hermes", "hermes-anything"),
        # The form hal0 hands back for a private document in the unified bank.
        ("hermes", f"private:hermes:{SESSION_DOC}"),
        # A named profile: the id shape it writes, and its raw identity.
        (profile_agent_id("work"), f"hermes-work-{SESSION}"),
        (profile_agent_id("work"), f"{profile_agent_id('work')}-{SESSION}"),
        ("ops-bot", "ops-bot-s1"),
    ],
)
def test_explicit_retain_cannot_target_any_session_document(
    agent_id: str, document_id: str
) -> None:
    """A plain upsert replaces. Every id in the session-document form is
    refused, not only the session bound right now."""
    provider, fake = _mocked(agent_id=agent_id)
    out = json.loads(
        provider.handle_tool_call(
            "hindsight_retain",
            {"text": "oops", "document_id": document_id, "visibility": "private"},
        )
    )
    assert out["status"] == "error"
    assert "reserved for session capture" in out["error"]
    fake.add.assert_not_called()


def test_a_long_identitys_cut_session_ids_are_refused_too() -> None:
    agent_id = profile_agent_id("a-rather-long-profile-name-for-a-team-workspace-x")
    session_doc = _session_document_id(agent_id, SESSION)
    assert session_doc is not None
    provider, fake = _mocked(agent_id=agent_id)
    out = json.loads(
        provider.handle_tool_call("hindsight_retain", {"text": "oops", "document_id": session_doc})
    )
    assert out["status"] == "error"
    fake.add.assert_not_called()


@pytest.mark.parametrize("document_id", ["deploy-notes", "hermesnotes", "work-hermes-1"])
def test_ordinary_document_ids_are_still_accepted(document_id: str) -> None:
    provider, fake = _mocked()
    provider.handle_tool_call("hindsight_retain", {"text": "v1", "document_id": document_id})
    assert fake.add.call_args.kwargs["document_id"] == document_id


# ── the client puts the fields on the wire ─────────────────────────────────


def test_client_add_sends_the_append_field_alone() -> None:
    server = NewServer()
    Hal0MemoryClient(http_client=server).add(  # type: ignore[arg-type]
        "turn", append_document_id="hermes-s1", observation_scopes="shared"
    )
    assert server.bodies == [
        {"text": "turn", "append_document_id": "hermes-s1", "observation_scopes": "shared"}
    ]


def test_client_add_refuses_both_id_fields() -> None:
    """``document_id`` beside the append field is the unsafe pair again."""
    server = NewServer()
    client = Hal0MemoryClient(http_client=server)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="mutually exclusive"):
        client.add("turn", document_id="a", append_document_id="a")
    assert server.bodies == []


def test_client_add_omits_the_fields_it_was_not_given() -> None:
    server = NewServer()
    Hal0MemoryClient(http_client=server).add("fact", tags=["t"], private=False)  # type: ignore[arg-type]
    # #317 still holds: the bank comes from the headers, never from the body.
    assert server.bodies == [{"text": "fact", "tags": ["t"]}]
