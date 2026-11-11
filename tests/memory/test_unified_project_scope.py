"""``project:<id>`` in the legacy layouts: a tag on write, a scope on read.

#1300 made the retired ``project:<id>`` namespace survive the unified-bank
collapse by stamping a ``project:<id>`` tag on the write. Reads then filtered
by that tag, so a ``shared`` read never saw a project's memory. ADR-0058
decision 7b removes that filter in every layout: ``project:`` is a scope a
read may narrow by (the ``project`` field), not a visibility wall. What these
tests pin, on a store still on its legacy layout:

  * the write still carries the project tag (unified) or its own bank (split);
  * a ``shared`` read sees project-tagged memory, and a read for one project
    sees that project plus global memory;
  * a project scope never overrides private visibility;
  * a delete reaches what the same caller could read.
"""

from __future__ import annotations

import pytest

from hal0.memory.hindsight_provider import HindsightProvider

from .test_unified_bank import RecordingClient, legacy_record


def _unified(client_id: str = "hermes") -> HindsightProvider:
    return HindsightProvider(
        client=RecordingClient(), client_id=client_id, **legacy_record(unified=True)
    )


def _legacy(client_id: str = "hermes") -> HindsightProvider:
    return HindsightProvider(
        client=RecordingClient(), client_id=client_id, **legacy_record(unified=False)
    )


def _texts(items: list[dict]) -> set[str]:
    return {i["text"] for i in items}


# ── Write path: the collapse must leave a marker ─────────────────────────────


@pytest.mark.asyncio
async def test_project_write_lands_in_shared_with_project_tag() -> None:
    p = _unified()
    await p.add("apollo fact", dataset="project:apollo", client_id="hermes")

    rec = p._client.retained[0]
    assert rec["bank_id"] == "shared"  # the collapse still happens
    assert "project:apollo" in rec["tags"]  # ...but it is no longer lossy
    assert "agent:hermes" in rec["tags"]


@pytest.mark.asyncio
async def test_shared_write_carries_no_project_tag() -> None:
    p = _unified()
    await p.add("plain fact", dataset="shared", client_id="hermes")
    assert not any(t.startswith("project:") for t in p._client.retained[0]["tags"])


@pytest.mark.asyncio
async def test_caller_supplied_project_tag_is_not_duplicated() -> None:
    p = _unified()
    await p.add(
        "apollo fact",
        dataset="project:apollo",
        tags=["project:apollo"],
        client_id="hermes",
    )
    tags = p._client.retained[0]["tags"]
    assert tags.count("project:apollo") == 1


@pytest.mark.asyncio
async def test_legacy_multi_bank_write_keeps_its_own_bank_and_no_tag() -> None:
    """The split layout isolates by bank; the tag is a unified-mode compensator
    and must not appear there (no behavior change for existing deployments)."""
    p = _legacy()
    await p.add("apollo fact", dataset="project:apollo", client_id="hermes")

    rec = p._client.retained[0]
    assert rec["bank_id"] == "project__apollo"
    assert not any(t.startswith("project:") for t in rec["tags"])


# ── Read path (ADR-0058 decision 7b) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_shared_recall_sees_project_docs() -> None:
    p = _unified()
    await p.add("plain note", dataset="shared", client_id="hermes")
    await p.add("apollo note", dataset="project:apollo", client_id="hermes")
    await p.add("zeus note", tags=["project:zeus"], client_id="hermes")

    out = await p.recall("note", dataset="shared", client_id="hermes")
    assert _texts(out) == {"plain note", "apollo note", "zeus note"}


@pytest.mark.asyncio
async def test_shared_list_and_search_see_project_docs() -> None:
    p = _unified()
    await p.add("plain note", dataset="shared", client_id="hermes")
    await p.add("apollo note", tags=["project:apollo"], client_id="hermes")

    page = await p.list_items(dataset="shared", client_id="hermes")
    assert _texts(page["items"]) == {"plain note", "apollo note"}
    found = await p.search("note", dataset="shared", client_id="hermes")
    assert _texts(found) == {"plain note", "apollo note"}


@pytest.mark.asyncio
async def test_project_scope_does_not_override_private_visibility() -> None:
    """Another agent's private doc stays private however it is tagged."""
    p = _unified(client_id="hermes")
    await p.add("hermes private", dataset="private:hermes", client_id="hermes")
    await p.add(
        "atlas private",
        dataset="private:atlas",
        tags=["project:apollo"],
        client_id="atlas",
    )
    await p.add("apollo shared note", dataset="project:apollo", client_id="hermes")

    out = await p.recall("x", dataset="shared", client_id="hermes")
    assert _texts(out) == {"hermes private", "apollo shared note"}


# ── Delete ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_shared_delete_reaches_a_project_doc() -> None:
    """Delete reaches what the caller could read, project-tagged memory too."""
    p = _unified()
    await p.add("apollo secret", dataset="project:apollo", client_id="hermes")
    apollo_id = p._client.retained[0]["document_id"]

    res = await p.delete([apollo_id], client_id="hermes", dataset="shared")
    assert res["deleted"] == 1


@pytest.mark.asyncio
async def test_a_delete_cannot_reach_another_agents_private_project_doc() -> None:
    p = _unified()
    await p.add(
        "atlas private", dataset="private:atlas", tags=["project:apollo"], client_id="atlas"
    )
    atlas_id = p._client.retained[0]["document_id"]

    res = await p.delete([atlas_id], client_id="hermes", dataset="shared")
    assert res["deleted"] == 0


# ── The split layout is untouched ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_legacy_multi_bank_project_isolation_still_by_bank() -> None:
    p = _legacy()
    await p.add("apollo secret", dataset="project:apollo", client_id="hermes")
    await p.add("zeus secret", dataset="project:zeus", client_id="hermes")

    out = await p.recall("secret", dataset="project:apollo", client_id="hermes")
    assert _texts(out) == {"apollo secret"}
