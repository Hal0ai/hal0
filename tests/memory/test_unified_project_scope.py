"""#1300 — ``project:<id>`` isolation must survive the unified-bank collapse.

Under the default config (``[memory].unified_bank=true``) every namespace
except ``agents`` collapses onto the single ``shared`` bank. ``private:``
survives that collapse because ``add`` stamps a ``visibility:private`` +
``agent:<id>`` tag pair that the read paths enforce. ``project:<id>`` got no
such marker: a ``project:foo`` write became an ordinary shared write, and a
``project:bar`` recall returned it. Project scoping did not exist.

The fix mirrors the ``visibility:private`` mechanism — stamp a ``project:<id>``
tag on collapse and filter reads by it. The contract these tests pin is the
one legacy multi-bank mode already had (where ``project:foo`` is its own bank):

  * a ``project:foo`` read sees ``project:foo`` docs and nothing else;
  * a ``shared`` read does NOT see project docs (they are scoped out, exactly
    as a separate bank would be);
  * project scope composes with private visibility rather than overriding it.
"""

from __future__ import annotations

import pytest

from hal0.memory.hindsight_provider import HindsightProvider

from .test_unified_bank import RecordingClient


def _unified(client_id: str = "hermes") -> HindsightProvider:
    return HindsightProvider(client=RecordingClient(), client_id=client_id, unified_bank=True)


def _legacy(client_id: str = "hermes") -> HindsightProvider:
    return HindsightProvider(client=RecordingClient(), client_id=client_id, unified_bank=False)


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
    """Legacy mode isolates by bank; the tag is a unified-mode compensator
    and must not appear there (no behavior change for existing deployments)."""
    p = _legacy()
    await p.add("apollo fact", dataset="project:apollo", client_id="hermes")

    rec = p._client.retained[0]
    assert rec["bank_id"] == "project__apollo"
    assert not any(t.startswith("project:") for t in rec["tags"])


# ── Read path: recall ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recall_in_one_project_does_not_see_another(monkeypatch) -> None:
    """The headline bug: project:foo write → project:bar recall returned it."""
    p = _unified()
    await p.add("apollo secret", dataset="project:apollo", client_id="hermes")
    await p.add("zeus secret", dataset="project:zeus", client_id="hermes")

    out = await p.recall("secret", dataset="project:apollo", client_id="hermes")
    assert _texts(out) == {"apollo secret"}


@pytest.mark.asyncio
async def test_shared_recall_does_not_see_project_docs() -> None:
    """Mirrors legacy bank isolation: shared is not a superset of projects."""
    p = _unified()
    await p.add("apollo secret", dataset="project:apollo", client_id="hermes")
    await p.add("everyone knows", dataset="shared", client_id="hermes")

    out = await p.recall("secret", dataset="shared", client_id="hermes")
    assert _texts(out) == {"everyone knows"}


@pytest.mark.asyncio
async def test_project_recall_does_not_see_plain_shared_docs() -> None:
    p = _unified()
    await p.add("apollo secret", dataset="project:apollo", client_id="hermes")
    await p.add("everyone knows", dataset="shared", client_id="hermes")

    out = await p.recall("x", dataset="project:apollo", client_id="hermes")
    assert _texts(out) == {"apollo secret"}


@pytest.mark.asyncio
async def test_multi_project_read_unions_the_requested_scopes() -> None:
    p = _unified()
    await p.add("apollo secret", dataset="project:apollo", client_id="hermes")
    await p.add("zeus secret", dataset="project:zeus", client_id="hermes")
    await p.add("hera secret", dataset="project:hera", client_id="hermes")

    out = await p.recall("secret", dataset=["project:apollo", "project:zeus"], client_id="hermes")
    assert _texts(out) == {"apollo secret", "zeus secret"}


# ── #1668: a duplicate project entry must not widen the scope ────────────────
#
# ``_requested_scope`` computed ``wants_unscoped`` by comparing the size of a
# *set* of project namespaces against the size of the *list* of non-empty
# requests. A repeated project entry shrinks the set relative to the list even
# though every entry named the same project, flipping ``wants_unscoped`` True
# and admitting unscoped shared docs into a call that only ever asked for one
# project.


@pytest.mark.asyncio
async def test_duplicate_project_entry_does_not_widen_recall_to_unscoped() -> None:
    p = _unified()
    await p.add("apollo secret", dataset="project:apollo", client_id="hermes")
    await p.add("everyone knows", dataset="shared", client_id="hermes")

    out = await p.recall(
        "x", dataset=["project:apollo", "project:apollo"], client_id="hermes"
    )
    assert _texts(out) == {"apollo secret"}, "duplicate entry must not leak unscoped shared docs"


@pytest.mark.asyncio
async def test_duplicate_project_entry_does_not_widen_list_to_unscoped() -> None:
    p = _unified()
    await p.add("apollo secret", dataset="project:apollo", client_id="hermes")
    await p.add("everyone knows", dataset="shared", client_id="hermes")

    page = await p.list_items(dataset=["project:apollo", "project:apollo"], client_id="hermes")
    assert _texts(page["items"]) == {"apollo secret"}


@pytest.mark.asyncio
async def test_duplicate_project_entry_does_not_widen_delete_to_unscoped() -> None:
    """Negative control: a delete scoped (with a duplicate) to one project
    must not be able to reach an unscoped shared doc (fail-closed, #1451
    lineage) — mirrors test_delete_scoped_to_a_project_cannot_reach_another."""
    p = _unified()
    await p.add("apollo secret", dataset="project:apollo", client_id="hermes")
    await p.add("everyone knows", dataset="shared", client_id="hermes")
    shared_id = p._client.retained[1]["document_id"]

    res = await p.delete(
        [shared_id], client_id="hermes", dataset=["project:apollo", "project:apollo"]
    )
    assert res["deleted"] == 0

    remaining = await p.recall("knows", dataset="shared", client_id="hermes")
    assert _texts(remaining) == {"everyone knows"}


@pytest.mark.asyncio
async def test_distinct_multi_project_entries_do_not_widen_to_unscoped() -> None:
    """A caller who legitimately named several distinct projects (no
    duplicates, no non-project entry) must still see ONLY those projects —
    covers the case the naive set-size fix could get backwards."""
    p = _unified()
    await p.add("apollo secret", dataset="project:apollo", client_id="hermes")
    await p.add("zeus secret", dataset="project:zeus", client_id="hermes")
    await p.add("everyone knows", dataset="shared", client_id="hermes")

    out = await p.recall("x", dataset=["project:apollo", "project:zeus"], client_id="hermes")
    assert _texts(out) == {"apollo secret", "zeus secret"}


# ── Read path: list ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_items_honours_project_scope() -> None:
    p = _unified()
    await p.add("apollo secret", dataset="project:apollo", client_id="hermes")
    await p.add("everyone knows", dataset="shared", client_id="hermes")

    page = await p.list_items(dataset="project:apollo", client_id="hermes")
    assert _texts(page["items"]) == {"apollo secret"}

    page = await p.list_items(dataset="shared", client_id="hermes")
    assert _texts(page["items"]) == {"everyone knows"}


# ── Composition with private visibility ──────────────────────────────────────


@pytest.mark.asyncio
async def test_project_scope_does_not_override_private_visibility() -> None:
    """A private doc in a project is still private to its owner — the two
    filters compose (both must pass), they don't shadow each other."""
    p = _unified(client_id="hermes")
    await p.add("hermes private", dataset="private:hermes", client_id="hermes")
    # Same bank, another agent's private doc, tagged into the same project.
    await p.add(
        "atlas private",
        dataset="private:atlas",
        tags=["project:apollo"],
        client_id="atlas",
    )
    await p.add("apollo shared note", dataset="project:apollo", client_id="hermes")

    out = await p.recall("x", dataset="project:apollo", client_id="hermes")
    assert _texts(out) == {"apollo shared note"}


# ── Delete ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_scoped_to_a_project_cannot_reach_another() -> None:
    """Delete enforces the same scope the read paths do (fail-closed)."""
    p = _unified()
    await p.add("apollo secret", dataset="project:apollo", client_id="hermes")
    await p.add("zeus secret", dataset="project:zeus", client_id="hermes")
    zeus_id = p._client.retained[1]["document_id"]

    res = await p.delete([zeus_id], client_id="hermes", dataset="project:apollo")
    assert res["deleted"] == 0

    remaining = await p.recall("secret", dataset="project:zeus", client_id="hermes")
    assert _texts(remaining) == {"zeus secret"}


@pytest.mark.asyncio
async def test_delete_within_the_right_project_succeeds() -> None:
    p = _unified()
    await p.add("apollo secret", dataset="project:apollo", client_id="hermes")
    apollo_id = p._client.retained[0]["document_id"]

    res = await p.delete([apollo_id], client_id="hermes", dataset="project:apollo")
    assert res["deleted"] == 1


# ── Legacy mode is untouched ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_legacy_multi_bank_project_isolation_still_by_bank() -> None:
    p = _legacy()
    await p.add("apollo secret", dataset="project:apollo", client_id="hermes")
    await p.add("zeus secret", dataset="project:zeus", client_id="hermes")

    out = await p.recall("secret", dataset="project:apollo", client_id="hermes")
    assert _texts(out) == {"apollo secret"}
