"""The provider owns ``agent:`` and ``visibility:`` tags on every write (#3136)."""

from __future__ import annotations

import pytest

from hal0.memory.hindsight_provider import HindsightProvider

from .test_unified_bank import RecordingClient


class _ScopeClient(RecordingClient):
    """A RecordingClient whose retain also records the full-surface extras."""

    async def retain(self, *, observation_scopes=None, **kw):
        await super().retain(**kw)
        if observation_scopes is not None:
            self.retained[-1]["observation_scopes"] = observation_scopes
        return {}


def _provider() -> HindsightProvider:
    return HindsightProvider(client=_ScopeClient(), unified_bank=True)


@pytest.mark.asyncio
async def test_add_overwrites_caller_agent_tag():
    p = _provider()
    await p.add("x", tags=["agent:brain", "visibility:private"], client_id="openwebui")
    tags = p._client.retained[-1]["tags"]
    assert "agent:openwebui" in tags
    assert "agent:brain" not in tags
    assert "visibility:private" not in tags


@pytest.mark.asyncio
async def test_add_keeps_private_when_requested():
    p = _provider()
    await p.add("x", dataset="private:openwebui", tags=["agent:brain"], client_id="openwebui")
    tags = p._client.retained[-1]["tags"]
    assert tags.count("visibility:private") == 1
    assert "agent:openwebui" in tags
    assert "agent:brain" not in tags


@pytest.mark.asyncio
async def test_add_anonymous_gets_unknown_agent():
    p = _provider()
    await p.add("x", tags=["agent:brain"], client_id=None)
    tags = p._client.retained[-1]["tags"]
    assert "agent:brain" not in tags
    assert tags.count("agent:unknown") == 1


@pytest.mark.asyncio
async def test_add_keeps_unrelated_and_project_tags():
    p = _provider()
    await p.add(
        "x",
        dataset="project:hal0",
        tags=["topic:cats", "user:alexander", "agent:other"],
        client_id="openwebui",
    )
    tags = p._client.retained[-1]["tags"]
    assert {"topic:cats", "user:alexander", "project:hal0", "agent:openwebui"} <= set(tags)
    assert [t for t in tags if t.startswith("agent:")] == ["agent:openwebui"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forged",
    [
        "AGENT:brain",
        " agent:brain",
        "Agent:brain",
        "VISIBILITY:PRIVATE",
        " visibility:private ",
        "visibility:shared",
        "visibility:anything",
    ],
)
async def test_add_drops_reserved_tags_whatever_their_case_or_padding(forged):
    p = _provider()
    await p.add("x", tags=[forged, "topic:cats"], client_id="openwebui")
    tags = p._client.retained[-1]["tags"]
    assert tags == ["topic:cats", "agent:openwebui"]


@pytest.mark.asyncio
async def test_add_keeps_a_requested_private_tag_once_despite_forged_ones():
    p = _provider()
    await p.add(
        "x", dataset="private:openwebui", tags=["VISIBILITY:PRIVATE"], client_id="openwebui"
    )
    tags = p._client.retained[-1]["tags"]
    assert tags.count("visibility:private") == 1
    assert [t for t in tags if t.lower().strip().startswith("visibility:")] == [
        "visibility:private"
    ]


@pytest.mark.asyncio
async def test_list_observation_scopes_cannot_name_reserved_tags():
    p = _provider()
    await p.add(
        "x",
        client_id="openwebui",
        observation_scopes=[
            ["agent:victim", "visibility:private", "topic:cats"],
            [" AGENT:victim", "Visibility:Shared"],
            ["topic:dogs"],
        ],
    )
    assert p._client.retained[-1]["observation_scopes"] == [["topic:cats"], ["topic:dogs"]]


@pytest.mark.asyncio
async def test_observation_scopes_reduced_to_nothing_fall_back_to_the_default():
    p = _provider()
    await p.add(
        "x", client_id="openwebui", observation_scopes=[["agent:victim", "visibility:private"]]
    )
    assert "observation_scopes" not in p._client.retained[-1]


@pytest.mark.asyncio
async def test_string_observation_scopes_pass_through():
    p = _provider()
    await p.add("x", client_id="openwebui", observation_scopes="per_tag")
    assert p._client.retained[-1]["observation_scopes"] == "per_tag"


# ── private writes in unified mode: document ids and observation scopes ─────


@pytest.mark.asyncio
async def test_private_write_namespaces_a_caller_document_id():
    p = _provider()
    out = await p.add("x", dataset="private:alice", document_id="notes", client_id="alice")
    assert out["id"] == "private:alice:notes"
    assert p._client.retained[-1]["document_id"] == "private:alice:notes"


@pytest.mark.asyncio
async def test_shared_and_private_writes_with_one_document_id_do_not_collide():
    p = _provider()
    await p.add("secret", dataset="private:alice", document_id="notes", client_id="alice")
    await p.add("public", document_id="notes", client_id="bob")
    ids = [r["document_id"] for r in p._client.retained]
    assert ids == ["private:alice:notes", "notes"]


@pytest.mark.asyncio
async def test_private_write_namespaces_the_session_derived_id():
    p = _provider()
    await p.add("a", dataset="private:alice", client_id="alice", metadata={"session_id": "s1"})
    await p.add("b", client_id="alice", metadata={"session_id": "s1"})
    ids = [r["document_id"] for r in p._client.retained]
    assert ids == ["private:alice:s1", "alice:s1"]


@pytest.mark.asyncio
async def test_private_write_without_an_id_keeps_its_fresh_uuid():
    p = _provider()
    out = await p.add("x", dataset="private:alice", client_id="alice")
    assert not out["id"].startswith("private:")


@pytest.mark.asyncio
async def test_a_shared_write_cannot_claim_a_private_document_id():
    p = _provider()
    with pytest.raises(ValueError, match="private:"):
        await p.add("x", document_id="private:alice:notes", client_id="mallory")
    assert p._client.retained == []


@pytest.mark.asyncio
async def test_private_write_ignores_caller_observation_scopes():
    p = _provider()
    for scopes in (["per_tag"][0], [["topic:cats"]], "combined"):
        await p.add("x", dataset="private:alice", client_id="alice", observation_scopes=scopes)
        assert "observation_scopes" not in p._client.retained[-1]


@pytest.mark.asyncio
async def test_shared_write_still_forwards_observation_scopes():
    p = _provider()
    await p.add("x", client_id="alice", observation_scopes=[["topic:cats"]])
    assert p._client.retained[-1]["observation_scopes"] == [["topic:cats"]]


@pytest.mark.asyncio
async def test_a_private_document_can_be_deleted_by_the_id_add_returned():
    p = _provider()
    out = await p.add("x", dataset="private:alice", document_id="notes", client_id="alice")
    res = await p.delete([out["id"]], client_id="alice")
    assert res["deleted"] == 1


@pytest.mark.asyncio
async def test_pre_unified_private_writes_keep_their_ids():
    p = HindsightProvider(client=_ScopeClient(), unified_bank=False)
    out = await p.add("x", dataset="private:alice", document_id="notes", client_id="alice")
    assert out["id"] == "notes"


@pytest.mark.asyncio
async def test_resending_the_returned_private_id_upserts_the_same_document():
    p = _provider()
    first = await p.add("a", dataset="private:alice", document_id="notes", client_id="alice")
    again = await p.add("b", dataset="private:alice", document_id=first["id"], client_id="alice")
    assert again["id"] == first["id"] == "private:alice:notes"


@pytest.mark.asyncio
async def test_a_private_write_cannot_land_on_another_agents_private_id():
    p = _provider()
    out = await p.add(
        "x", dataset="private:alice", document_id="private:bob:notes", client_id="alice"
    )
    assert out["id"] == "private:alice:private:bob:notes"
