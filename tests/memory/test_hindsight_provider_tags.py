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
