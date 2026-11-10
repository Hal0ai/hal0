"""One memory document per Hermes session (#2017, ADR-0058 decision 5).

The hal0-memory plugin used to send no ``document_id``, so every turn became
its own randomly named document. It now sends
``<client>[-<profile>]-<session-id>`` for the writes that belong to a session:
turn capture, the pre-compress continuity note and the session-end checkpoint.

Two engine facts shape the contract these tests pin (Hindsight 1.2.0):

* A retain on a document id the engine already holds REPLACES that document
  unless the item says ``update_mode: "append"``. One id for a whole session
  without append would delete every earlier turn, so the id never travels
  without the mode, and the plugin stops sending it to a hal0-api that does
  not echo the mode back.
* An append re-stamps the retain's tags onto every memory the document already
  holds, and a changed tag set deletes the observations built on them. So all
  session writes carry one fixed tag set and the write kind is metadata.

``tests/api/test_memory_session_documents_route.py`` covers the route and the
engine call; this file is the plugin side, against a mocked client.
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

# What the envelope (ADR-0058 decision 4) moves out of tags into
# ``metadata["lifecycle"]``. None of them may be a session tag.
LIFECYCLE_WORDS = {"checkpoint", "session-end", "continuity", "pre-compress", "bootstrap", "chat"}

MESSAGES = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]


def _provider(
    *, agent_id: str = "hermes", session_id: str = SESSION, echo: bool = True, **init: Any
) -> tuple[Hal0MemoryProvider, Mock]:
    """A provider on a mocked client. ``echo=False`` plays a hal0-api too old
    to forward ``update_mode``: it answers without the field."""
    fake = Mock(spec=Hal0MemoryClient)
    fake.agent_id = agent_id

    def _add(text: str, **kwargs: Any) -> dict[str, Any]:
        out: dict[str, Any] = {"id": kwargs.get("document_id") or "fresh", "timestamp": "t"}
        if echo and kwargs.get("update_mode"):
            out["update_mode"] = kwargs["update_mode"]
        return out

    fake.add.side_effect = _add
    provider = Hal0MemoryProvider(client=fake)
    provider.initialize(session_id=session_id, **init)
    return provider, fake


def _writes(fake: Mock) -> list[dict[str, Any]]:
    return [call.kwargs for call in fake.add.call_args_list]


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


# ── every session write kind lands in the session document, appended ───────


def _turn(provider: Hal0MemoryProvider) -> None:
    provider.sync_turn("hi", "hello", session_id=SESSION)


def _pre_compress(provider: Hal0MemoryProvider) -> None:
    provider.on_pre_compress(MESSAGES)


def _session_end(provider: Hal0MemoryProvider) -> None:
    provider.on_session_end(MESSAGES)


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
        ("hermes", f"hermes-{SESSION}"),
        (profile_agent_id("work"), f"hermes-work-{SESSION}"),
    ],
)
def test_session_writes_append_to_the_session_document(
    write: Any, lifecycle: str, kind: str, agent_id: str, document_id: str
) -> None:
    provider, fake = _provider(agent_id=agent_id)
    write(provider)
    (sent,) = _writes(fake)
    assert sent["document_id"] == document_id
    # The data-loss guard: the id is never sent without append mode.
    assert sent["update_mode"] == "append"
    assert sent["private"] is True
    assert sent["tags"] == list(_SESSION_TAGS)
    assert sent["metadata"]["lifecycle"] == lifecycle
    assert sent["metadata"]["kind"] == kind
    assert sent["metadata"]["visibility"] == "private"


def test_session_writes_share_one_tag_set_with_no_lifecycle_word() -> None:
    """The engine re-stamps an append's tags over the whole document and
    re-consolidates on a change, so the three write kinds must agree."""
    provider, fake = _provider()
    _turn(provider)
    _pre_compress(provider)
    _session_end(provider)
    tag_sets = {tuple(sent["tags"]) for sent in _writes(fake)}
    assert tag_sets == {_SESSION_TAGS}
    assert not LIFECYCLE_WORDS & set(_SESSION_TAGS)
    assert {sent["document_id"] for sent in _writes(fake)} == {f"hermes-{SESSION}"}


def test_the_turns_own_session_id_wins_over_the_bound_one() -> None:
    """The manager names the session on every ``sync_turn`` (upstream
    ``agent/memory_manager.py`` ``sync_all``)."""
    provider, fake = _provider(session_id="bound")
    provider.sync_turn("hi", "hello", session_id="other")
    assert _writes(fake)[0]["document_id"] == "hermes-other"


def test_without_a_session_id_each_write_keeps_its_own_document() -> None:
    provider, fake = _provider(session_id="")
    provider.sync_turn("hi", "hello")
    (sent,) = _writes(fake)
    assert "document_id" not in sent and "update_mode" not in sent
    assert sent["tags"] == list(_SESSION_TAGS)


@pytest.mark.parametrize("context", ["cron", "flush", "subagent"])
def test_non_primary_contexts_still_write_nothing(context: str) -> None:
    provider, fake = _provider(agent_context=context)
    _turn(provider)
    _pre_compress(provider)
    _session_end(provider)
    fake.add.assert_not_called()


# ── session rotation ───────────────────────────────────────────────────────


def test_session_switch_moves_later_writes_to_the_new_document() -> None:
    """Hermes rotates the id without re-initialising (/new, /resume, compression).
    The manager ends the old session first, then switches (upstream
    ``commit_session_boundary_async``): the checkpoint stays with the old one."""
    provider, fake = _provider(session_id="old")
    provider.on_session_end(MESSAGES)
    provider.on_session_switch("new", parent_session_id="old", reset=True, reason="new_session")
    provider.on_pre_compress(MESSAGES)
    assert [sent["document_id"] for sent in _writes(fake)] == ["hermes-old", "hermes-new"]


def test_session_switch_ignores_an_empty_id() -> None:
    provider, fake = _provider(session_id="old")
    provider.on_session_switch("")
    provider.on_session_end(MESSAGES)
    assert _writes(fake)[0]["document_id"] == "hermes-old"


# ── a hal0-api that does not confirm append mode ───────────────────────────


def test_an_unconfirmed_append_stops_the_session_id(caplog: pytest.LogCaptureFixture) -> None:
    """An older hal0-api drops ``update_mode`` and the engine would replace the
    session document on each turn. After one unconfirmed write the plugin goes
    back to one document per write, which loses nothing."""
    provider, fake = _provider(echo=False)
    with caplog.at_level(logging.WARNING):
        _turn(provider)
        _turn(provider)
        _session_end(provider)
    first, second, third = _writes(fake)
    assert first["document_id"] == f"hermes-{SESSION}" and first["update_mode"] == "append"
    for later in (second, third):
        assert "document_id" not in later and "update_mode" not in later
    assert caplog.text.count("did not confirm append mode") == 1


def test_a_confirmed_append_keeps_the_session_id() -> None:
    provider, fake = _provider(echo=True)
    _turn(provider)
    _turn(provider)
    assert [sent.get("document_id") for sent in _writes(fake)] == [f"hermes-{SESSION}"] * 2


def test_initialize_gives_the_next_session_a_fresh_chance() -> None:
    provider, fake = _provider(echo=False)
    _turn(provider)
    _turn(provider)
    provider.initialize(session_id="next")
    provider.sync_turn("hi", "hello")
    assert _writes(fake)[-1]["document_id"] == "hermes-next"


# ── writes that are not session captures ───────────────────────────────────


def test_delegation_and_builtin_mirror_keep_per_call_documents() -> None:
    provider, fake = _provider()
    provider.on_delegation("task", "result", child_session_id="child")
    provider.on_memory_write("add", "user", "likes tea")
    for sent in _writes(fake):
        assert "document_id" not in sent and "update_mode" not in sent


def test_explicit_retain_keeps_a_per_call_document() -> None:
    provider, fake = _provider()
    out = json.loads(provider.handle_tool_call("hindsight_retain", {"text": "a fact"}))
    (sent,) = _writes(fake)
    assert "document_id" not in sent and "update_mode" not in sent
    assert out["visibility"] == "shared"


def test_explicit_retain_passes_the_callers_document_id() -> None:
    """A caller-named id is a plain upsert (no append): revising a fact replaces it."""
    provider, fake = _provider()
    provider.handle_tool_call("hindsight_retain", {"text": "v2", "document_id": "deploy-notes"})
    (sent,) = _writes(fake)
    assert sent["document_id"] == "deploy-notes"
    assert "update_mode" not in sent


def test_explicit_retain_cannot_replace_the_session_document() -> None:
    provider, fake = _provider()
    out = json.loads(
        provider.handle_tool_call(
            "hindsight_retain", {"text": "oops", "document_id": f"hermes-{SESSION}"}
        )
    )
    assert out["status"] == "error"
    fake.add.assert_not_called()


# ── the client puts the fields on the wire ─────────────────────────────────


class _Response:
    status_code = 200
    text = ""

    def json(self) -> dict[str, Any]:
        return {"id": "x", "timestamp": "t"}


class _Http:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, path: str, **kwargs: Any) -> _Response:
        self.calls.append({"method": method, "path": path, **kwargs})
        return _Response()


def test_client_add_sends_document_id_mode_and_scopes() -> None:
    http = _Http()
    client = Hal0MemoryClient(http_client=http)  # type: ignore[arg-type]
    client.add(
        "turn",
        document_id="hermes-s1",
        update_mode="append",
        observation_scopes="shared",
    )
    (call,) = http.calls
    assert (call["method"], call["path"]) == ("POST", "/api/memory/add")
    assert call["json"] == {
        "text": "turn",
        "document_id": "hermes-s1",
        "update_mode": "append",
        "observation_scopes": "shared",
    }
    # #317 still holds: the bank comes from the headers, never from the body.
    assert "dataset" not in call["json"]


def test_client_add_omits_the_fields_it_was_not_given() -> None:
    http = _Http()
    Hal0MemoryClient(http_client=http).add("fact", tags=["t"], private=False)  # type: ignore[arg-type]
    assert http.calls[0]["json"] == {"text": "fact", "tags": ["t"]}
