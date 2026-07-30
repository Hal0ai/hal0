"""Issue #1456: one id contract across list, recall and delete.

``MemoryProvider`` pins ``MemoryItem.id`` as "the document_id (idempotent,
recall-visible, delete-addressable) — NOT a per-fact id". Hindsight 0.8.x
returns *both* on a ``/memories/list`` item and they are different UUIDs:

    {"id": "6310e93a-…", "document_id": "19248371-…", …}

``_fact_to_item`` (recall) honoured the contract — ``document_id or id``.
``_list_fact_to_item`` (list) inverted it — ``id or document_id`` — so the
advertised list→delete round trip handed ``delete_document`` a fact id and
404-swept to ``{"deleted": 0}``. Unified mode made it worse in both
directions: ``_deletable_ids`` matched caller ids against the same fact-id
field, so a *correct* document_id never matched and was fail-closed
withheld — ``POST /api/memory/delete`` deleted nothing at all.

Every existing test masked this by faking ``list_memories`` items with
``{"id": <document_id>}``, i.e. fact id == document id, a shape the real
engine never returns. The client here deliberately returns distinct
values, which is the whole point of the file.

The contract this pins: **``id`` is always the document_id**, on every
surface. The per-fact id is still surfaced (``metadata.fact_id``) because
it is the only handle on an individual extracted fact, and delete accepts
it as an alias in unified mode — where the resolution scan already runs, so
it is free — but it is never what ``id`` means.
"""

from __future__ import annotations

import pytest

from hal0.memory.hindsight_provider import HindsightProvider

#: The real 0.8.x shape: a per-fact UUID and a distinct document UUID.
FACT_ID = "6310e93a-8030-4b0b-8702-1d57c8a0b01f"
DOC_ID = "19248371-e27e-44f4-b0fd-065f9870bc2b"


class RealShapeClient:
    """A Hindsight fake whose ``list_memories`` items carry ``id`` !=
    ``document_id``, mirroring the live engine."""

    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []
        self.facts: dict[str, list[dict]] = {
            "shared": [
                {
                    "id": FACT_ID,
                    "document_id": DOC_ID,
                    "text": "the user prefers dark mode",
                    "tags": ["agent:hermes"],
                    "fact_type": "observation",
                }
            ]
        }

    async def list_memories(self, *, bank_id, limit=50, offset=0, types=None, query=None):
        return {"items": list(self.facts.get(bank_id, []))[offset : offset + limit]}

    async def recall(self, *, bank_id, query, types=None, max_tokens=4096, tags=None, **kw):
        return {"results": list(self.facts.get(bank_id, []))}

    async def delete_document(self, *, bank_id, document_id):
        self.deleted.append((bank_id, document_id))
        rows = self.facts.get(bank_id, [])
        # The engine addresses documents, never facts: a fact id 404s.
        kept = [f for f in rows if f["document_id"] != document_id]
        removed = len(rows) - len(kept)
        if removed == 0:
            raise _NotFound(404)
        self.facts[bank_id] = kept
        return {"memory_units_deleted": removed}


class _NotFound(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status


def _provider(client: RealShapeClient, *, unified: bool = True) -> HindsightProvider:
    return HindsightProvider(client=client, client_id="hermes", unified_bank=unified)


# ── 1. list speaks the same id as recall and the ABC ─────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("unified", [True, False])
async def test_list_surfaces_the_document_id_as_id(unified: bool) -> None:
    p = _provider(RealShapeClient(), unified=unified)
    items = (await p.list_items(dataset="shared", client_id="hermes"))["items"]
    assert [i["id"] for i in items] == [DOC_ID]


@pytest.mark.asyncio
async def test_list_still_surfaces_the_per_fact_id_out_of_band() -> None:
    """Dropping the fact id entirely would lose the only handle on an
    individual extracted fact — it moves, it does not disappear."""
    p = _provider(RealShapeClient())
    items = (await p.list_items(dataset="shared", client_id="hermes"))["items"]
    assert items[0]["metadata"]["fact_id"] == FACT_ID


@pytest.mark.asyncio
async def test_list_and_recall_agree_on_the_id_for_the_same_fact() -> None:
    """The two read surfaces disagreeing is the defect; assert they can't."""
    p = _provider(RealShapeClient())
    listed = (await p.list_items(dataset="shared", client_id="hermes"))["items"]
    recalled = await p.recall("dark mode", dataset="shared", client_id="hermes")
    assert listed[0]["id"] == recalled[0]["id"] == DOC_ID


@pytest.mark.asyncio
async def test_list_falls_back_to_the_fact_id_when_no_document_id() -> None:
    """Back-compat: an engine (or fixture) that only returns ``id`` must keep
    working — the fallback is the same one recall has always had."""
    client = RealShapeClient()
    client.facts["shared"] = [{"id": FACT_ID, "text": "x", "tags": []}]
    p = _provider(client)
    items = (await p.list_items(dataset="shared", client_id="hermes"))["items"]
    assert items[0]["id"] == FACT_ID


# ── 2. the round trip the API advertises actually deletes ────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("unified", [True, False])
async def test_list_then_delete_round_trip_removes_the_item(unified: bool) -> None:
    """The headline repro: ``{deleted: 0}`` before the fix, in both modes."""
    client = RealShapeClient()
    p = _provider(client, unified=unified)
    listed = (await p.list_items(dataset="shared", client_id="hermes"))["items"]
    result = await p.delete([listed[0]["id"]], client_id="hermes", dataset="shared")
    assert result == {"deleted": 1}
    assert client.facts["shared"] == []


@pytest.mark.asyncio
async def test_unified_delete_accepts_a_correct_document_id() -> None:
    """``_deletable_ids`` matched on the fact-id field, so even a caller who
    already held the right document_id was fail-closed withheld."""
    client = RealShapeClient()
    p = _provider(client, unified=True)
    assert await p.delete([DOC_ID], client_id="hermes", dataset="shared") == {"deleted": 1}
    assert client.deleted == [("shared", DOC_ID)]


@pytest.mark.asyncio
async def test_unified_delete_accepts_the_per_fact_id_as_an_alias() -> None:
    """Callers holding a pre-fix fact id (or one read from
    ``metadata.fact_id``) resolve to the owning document rather than
    silently no-op'ing. The engine is still addressed by document_id."""
    client = RealShapeClient()
    p = _provider(client, unified=True)
    assert await p.delete([FACT_ID], client_id="hermes", dataset="shared") == {"deleted": 1}
    assert client.deleted == [("shared", DOC_ID)]


@pytest.mark.asyncio
async def test_unified_delete_still_withholds_another_agents_private_doc() -> None:
    """Negative control: widening the id match must not widen the ACL."""
    client = RealShapeClient()
    client.facts["shared"] = [
        {
            "id": FACT_ID,
            "document_id": DOC_ID,
            "text": "someone else's secret",
            "tags": ["visibility:private", "agent:other-agent"],
        }
    ]
    p = _provider(client, unified=True)
    assert await p.delete([DOC_ID], client_id="hermes", dataset="shared") == {"deleted": 0}
    assert client.deleted == []


@pytest.mark.asyncio
async def test_unified_delete_still_withholds_an_unresolvable_id() -> None:
    client = RealShapeClient()
    p = _provider(client, unified=True)
    assert await p.delete(["no-such-id"], client_id="hermes", dataset="shared") == {"deleted": 0}
    assert client.deleted == []
