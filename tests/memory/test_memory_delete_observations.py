"""Issue #2747: deleting an observation unit id must not silently no-op.

Consolidation derives *observations* from one or more source facts. An
observation has no document (``document_id`` is null on a real Hindsight
1.0 ``/memories/list`` row), so every read surface hands out its own unit id
as ``id`` — and ``POST /api/memory/delete`` used to pass that id straight to
``DELETE /documents/{id}``, get a 404, swallow it as "not in this bank" and
answer ``{"deleted": 0}``, even for the owning agent.

The engine has no per-observation delete, but deleting a source fact's
document cascades every observation built on it. The fix resolves an
observation id to the documents of all its ``source_memory_ids`` and runs
the visibility/project ACL against the observation and every source.

The fake below mirrors the engine shape that matters: distinct fact and
document ids, ``document_id: null`` plus ``source_memory_ids`` on the
observation, a 404 for a non-document id, and the observation cascade.
"""

from __future__ import annotations

import pytest

from hal0.memory import hindsight_provider as hp
from hal0.memory.hindsight_provider import HindsightProvider

DOC_A = "3c533a7a-0000-4000-8000-00000000000a"
DOC_B = "3c533a7a-0000-4000-8000-00000000000b"
WORLD_A = "060ad4de-0000-4000-8000-00000000000a"
WORLD_B = "060ad4de-0000-4000-8000-00000000000b"
OBS = "53ec96b7-0000-4000-8000-000000000001"

OWNER_PRIVATE = ["visibility:private", "agent:rvskeptic"]
OTHER_PRIVATE = ["visibility:private", "agent:someone-else"]


class _Response:
    status_code = 404


class _NotFound(Exception):
    """httpx.HTTPStatusError-shaped: the provider reads ``exc.response``."""

    def __init__(self) -> None:
        super().__init__("HTTP 404")
        self.response = _Response()


def _world(fact_id: str, doc_id: str, tags: list[str]) -> dict:
    return {
        "id": fact_id,
        "document_id": doc_id,
        "fact_type": "world",
        "text": f"fact from {doc_id}",
        "tags": list(tags),
        "source_memory_ids": [],
    }


def _observation(sources: list[str], tags: list[str]) -> dict:
    return {
        "id": OBS,
        "document_id": None,
        "fact_type": "observation",
        "text": "the rvskeptic probe owns a green bicycle",
        "tags": list(tags),
        "source_memory_ids": list(sources),
    }


class EngineShapeClient:
    def __init__(self, units: list[dict], bank: str = "shared") -> None:
        self.bank = bank
        self.units = units
        self.deleted: list[str] = []

    async def list_memories(self, *, bank_id, limit=50, offset=0, types=None, query=None):
        rows = self.units if bank_id == self.bank else []
        return {"items": list(rows)[offset : offset + limit]}

    async def delete_document(self, *, bank_id, document_id):
        if bank_id != self.bank:
            raise _NotFound()
        doomed = {u["id"] for u in self.units if u["document_id"] == document_id}
        if not doomed:
            raise _NotFound()  # an observation id is not a document
        self.deleted.append(document_id)
        # Cascade: any observation touching a deleted fact goes too.
        self.units = [
            u
            for u in self.units
            if u["id"] not in doomed and not doomed.intersection(u["source_memory_ids"])
        ]
        return {"memory_units_deleted": len(doomed)}

    def ids(self) -> set[str]:
        return {u["id"] for u in self.units}


def _provider(client, *, unified: bool = True, client_id: str = "rvskeptic"):
    return HindsightProvider(client=client, client_id=client_id, unified_bank=unified)


@pytest.mark.asyncio
async def test_owner_deletes_observation_by_its_unit_id() -> None:
    """The headline repro: ``{deleted: 0}`` and the unit still listed."""
    client = EngineShapeClient(
        [_world(WORLD_A, DOC_A, OWNER_PRIVATE), _observation([WORLD_A], OWNER_PRIVATE)]
    )
    p = _provider(client)
    assert await p.delete([OBS], client_id="rvskeptic", dataset="shared") == {"deleted": 1}
    assert client.deleted == [DOC_A]
    assert OBS not in client.ids()


@pytest.mark.asyncio
async def test_observation_with_several_sources_deletes_every_source_document() -> None:
    """Deleting only one source would let consolidation re-derive the
    observation from the rest."""
    client = EngineShapeClient(
        [
            _world(WORLD_A, DOC_A, OWNER_PRIVATE),
            _world(WORLD_B, DOC_B, OWNER_PRIVATE),
            _observation([WORLD_A, WORLD_B], OWNER_PRIVATE),
        ]
    )
    p = _provider(client)
    assert await p.delete([OBS], client_id="rvskeptic", dataset="shared") == {"deleted": 1}
    assert sorted(client.deleted) == sorted([DOC_A, DOC_B])
    assert client.ids() == set()


@pytest.mark.asyncio
async def test_source_on_an_earlier_scan_page_than_the_observation(monkeypatch) -> None:
    """The scan stops once every wanted id resolves; an observation's sources
    can sit on a page already passed, so they must still resolve."""
    monkeypatch.setattr(hp, "_DELETE_SCAN_PAGE", 1)
    client = EngineShapeClient(
        [_world(WORLD_A, DOC_A, OWNER_PRIVATE), _observation([WORLD_A], OWNER_PRIVATE)]
    )
    p = _provider(client)
    assert await p.delete([OBS], client_id="rvskeptic", dataset="shared") == {"deleted": 1}
    assert client.deleted == [DOC_A]


@pytest.mark.asyncio
async def test_source_on_a_later_scan_page_than_the_observation(monkeypatch) -> None:
    monkeypatch.setattr(hp, "_DELETE_SCAN_PAGE", 1)
    client = EngineShapeClient(
        [_observation([WORLD_A], OWNER_PRIVATE), _world(WORLD_A, DOC_A, OWNER_PRIVATE)]
    )
    p = _provider(client)
    assert await p.delete([OBS], client_id="rvskeptic", dataset="shared") == {"deleted": 1}
    assert client.deleted == [DOC_A]


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["someone-else", "anonymous"])
async def test_non_owner_and_anonymous_stay_fail_closed(caller: str) -> None:
    client = EngineShapeClient(
        [_world(WORLD_A, DOC_A, OWNER_PRIVATE), _observation([WORLD_A], OWNER_PRIVATE)]
    )
    p = _provider(client, client_id=caller)
    assert await p.delete([OBS], client_id=caller, dataset="shared") == {"deleted": 0}
    assert client.deleted == []
    assert OBS in client.ids()


@pytest.mark.asyncio
async def test_untagged_observation_over_a_foreign_private_source_is_withheld() -> None:
    """The ACL runs on the source documents, not just the observation: a
    shared-looking observation must not become a handle on another agent's
    private document."""
    client = EngineShapeClient([_world(WORLD_A, DOC_A, OTHER_PRIVATE), _observation([WORLD_A], [])])
    p = _provider(client)
    assert await p.delete([OBS], client_id="rvskeptic", dataset="shared") == {"deleted": 0}
    assert client.deleted == []


@pytest.mark.asyncio
async def test_observation_mixing_own_and_foreign_sources_is_withheld_whole() -> None:
    client = EngineShapeClient(
        [
            _world(WORLD_A, DOC_A, OWNER_PRIVATE),
            _world(WORLD_B, DOC_B, OTHER_PRIVATE),
            _observation([WORLD_A, WORLD_B], []),
        ]
    )
    p = _provider(client)
    assert await p.delete([OBS], client_id="rvskeptic", dataset="shared") == {"deleted": 0}
    assert client.deleted == []


@pytest.mark.asyncio
async def test_observation_whose_source_is_missing_is_withheld() -> None:
    client = EngineShapeClient([_observation(["gone-0000"], OWNER_PRIVATE)])
    p = _provider(client)
    assert await p.delete([OBS], client_id="rvskeptic", dataset="shared") == {"deleted": 0}
    assert client.deleted == []


@pytest.mark.asyncio
async def test_project_scoped_delete_cannot_reach_a_source_outside_the_project() -> None:
    client = EngineShapeClient(
        [
            _world(WORLD_A, DOC_A, ["project:alpha"]),
            _world(WORLD_B, DOC_B, ["project:beta"]),
            _observation([WORLD_A, WORLD_B], ["project:alpha"]),
        ]
    )
    p = _provider(client)
    assert await p.delete([OBS], client_id="rvskeptic", dataset="project:alpha") == {"deleted": 0}
    assert client.deleted == []


@pytest.mark.asyncio
async def test_legacy_multibank_observation_id_deletes_its_source() -> None:
    """Legacy multi-bank mode skips the ACL but had the same document-only
    delete, so an observation id 404'd there too."""
    client = EngineShapeClient(
        [_world(WORLD_A, DOC_A, []), _observation([WORLD_A], [])], bank="shared"
    )
    p = _provider(client, unified=False)
    assert await p.delete([OBS], client_id="rvskeptic", dataset="shared") == {"deleted": 1}
    assert client.deleted == [DOC_A]
    assert client.ids() == set()


@pytest.mark.asyncio
async def test_world_unit_delete_still_works_and_cascades() -> None:
    """Control from the issue repro: the document path is unchanged."""
    client = EngineShapeClient(
        [_world(WORLD_A, DOC_A, OWNER_PRIVATE), _observation([WORLD_A], OWNER_PRIVATE)]
    )
    p = _provider(client)
    assert await p.delete([DOC_A], client_id="rvskeptic", dataset="shared") == {"deleted": 1}
    assert client.ids() == set()
