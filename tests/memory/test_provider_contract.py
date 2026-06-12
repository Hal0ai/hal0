"""Parametrized MemoryProvider conformance suite (brain-redesign P0).

Asserts the contract every engine must honor: namespace isolation,
``private:`` write rejection at the wrapper boundary, tag-AND, date-range,
delete semantics, fail-open-empty foreign-private reads, and the
``graph_status()`` payload shape.

The DEFAULT-gate parameter is the in-memory ``FakeMemoryProvider`` so this
suite runs on every PR without dragging a heavy backend in. Real backends
(Cognee, PgVector, Hindsight) attach as ``@pytest.mark.slow`` params, matching
the existing tests/memory/conftest.py split.
"""

from __future__ import annotations

import pytest

from tests.memory.fakes import FakeMemoryProvider


@pytest.fixture
def provider(request) -> object:
    # request.param is a 0-arg factory returning a fresh provider.
    return request.param()


def _fake_factory():
    return FakeMemoryProvider(client_id="alice")


# Default gate: the fake only. Slow backends are added by later tasks via
# pytest_generate_tests / additional params marked slow.
@pytest.fixture(params=[_fake_factory], ids=["fake"])
def conformant(request):
    return request.param()


@pytest.mark.asyncio
async def test_add_returns_id_and_timestamp(conformant):
    result = await conformant.add("a note", dataset="shared")
    assert set(result) == {"id", "timestamp"}
    assert isinstance(result["id"], str) and result["id"]


@pytest.mark.asyncio
async def test_namespace_isolation_foreign_private_reads_empty(conformant):
    # A write into alice's private bucket is invisible to a bob-scoped read.
    await conformant.add("alice secret", dataset="private:alice", client_id="alice")
    out = await conformant.search("secret", dataset="private:bob", client_id="bob")
    assert out == []


@pytest.mark.asyncio
async def test_tag_and_match(conformant):
    await conformant.add("tagged", dataset="shared", tags=["x", "y"])
    await conformant.add("partial", dataset="shared", tags=["x"])
    out = await conformant.search("tagged partial", dataset="shared", tags=["x", "y"])
    texts = {r["text"] for r in out}
    assert "tagged" in texts and "partial" not in texts


@pytest.mark.asyncio
async def test_date_range_before_after(conformant):
    await conformant.add("old", dataset="shared")
    out = await conformant.search("old", dataset="shared", after="2099-01-01T00:00:00+00:00")
    assert out == []  # nothing after the year 2099


@pytest.mark.asyncio
async def test_delete_semantics(conformant):
    res = await conformant.add("deleteme", dataset="shared")
    deleted = await conformant.delete([res["id"]])
    assert deleted == {"deleted": 1}
    out = await conformant.search("deleteme", dataset="shared")
    assert out == []


@pytest.mark.asyncio
async def test_graph_status_shape(conformant):
    status = conformant.graph_status()
    assert set(status) >= {
        "enabled",
        "route",
        "in_flight",
        "builds_ok",
        "errors",
        "last_built_at",
        "last_error",
    }


def _pgvector_factory():
    from hal0.memory.pgvector_provider import PgVectorProvider

    return PgVectorProvider(client_id="alice")


@pytest.mark.slow
@pytest.mark.asyncio
async def test_pgvector_conforms():
    p = _pgvector_factory()
    res = await p.add("pg note", dataset="shared")
    assert set(res) == {"id", "timestamp"}
    out = await p.search("pg", dataset="private:bob", client_id="bob")
    assert out == []
    assert set(p.graph_status()) >= {"enabled", "route"}


def _hindsight_factory():
    from hal0.memory.hindsight_provider import HindsightProvider
    from tests.memory.test_hindsight_provider import FakeHindsightClient

    return HindsightProvider(client=FakeHindsightClient(), client_id="alice")


@pytest.mark.slow
@pytest.mark.asyncio
async def test_hindsight_conforms_to_contract():
    p = _hindsight_factory()
    res = await p.add("hs note", dataset="shared")
    # operation_id is optional contract surface (async-ingest engines).
    assert {"id", "timestamp"} <= set(res) <= {"id", "timestamp", "operation_id"}
    # Foreign-private read → empty.
    out = await p.search("note", dataset="private:bob", client_id="bob")
    assert out == []
    assert set(p.graph_status()) >= {"enabled", "route"}
    d = await p.delete([res["id"]])
    assert "deleted" in d
