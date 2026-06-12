"""HindsightProvider unit tests — bank mapping + fan-out (P1)."""

from __future__ import annotations

import pytest

from hal0.memory.hindsight_provider import HindsightProvider, namespace_to_bank


class FakeHindsightClient:
    """Records calls; returns canned recall/retain/delete results."""

    def __init__(self) -> None:
        self.retained: list[dict] = []
        self.recalled: list[dict] = []
        self.deleted: list[str] = []
        self._facts_by_bank: dict[str, list[dict]] = {}

    async def retain(
        self,
        *,
        bank_id,
        content,
        document_id,
        context=None,
        metadata=None,
        tags=None,
        timestamp=None,
    ):
        self.retained.append(
            {
                "bank_id": bank_id,
                "document_id": document_id,
                "content": content,
                "tags": list(tags or []),
            }
        )
        self._facts_by_bank.setdefault(bank_id, []).append(
            {
                "document_id": document_id,
                "text": content,
                "tags": list(tags or []),
                "mentioned_at": "2026-06-06T00:00:00+00:00",
            }
        )
        return {"success": True, "bank_id": bank_id, "items_count": 1}

    async def recall(self, *, bank_id, query, types=None, max_tokens=4096, tags=None):
        self.recalled.append({"bank_id": bank_id, "query": query})
        return {"results": list(self._facts_by_bank.get(bank_id, []))}

    async def delete_document(self, *, bank_id, document_id):
        self.deleted.append(document_id)
        facts = self._facts_by_bank.get(bank_id, [])
        before = len(facts)
        self._facts_by_bank[bank_id] = [f for f in facts if f["document_id"] != document_id]
        return {"memory_units_deleted": before - len(self._facts_by_bank[bank_id])}

    async def list_memories(self, *, bank_id, limit=50, offset=0, types=None, query=None):
        raw = list(self._facts_by_bank.get(bank_id, []))
        # Expose stored facts in the list-endpoint shape: id falls back to document_id.
        items = [
            {
                "id": f.get("id") or f.get("document_id"),
                "text": f.get("text", ""),
                "fact_type": f.get("fact_type", "observation"),
                "mentioned_at": f.get("mentioned_at"),
                "tags": list(f.get("tags") or []),
            }
            for f in raw
        ]
        return {"items": items, "total": len(items), "limit": limit, "offset": offset}


def test_namespace_to_bank_mapping():
    assert namespace_to_bank("shared") == "shared"
    assert namespace_to_bank("private:hermes") == "private__hermes"
    assert namespace_to_bank("project:42") == "project__42"
    assert namespace_to_bank("agents") == "agents"


@pytest.mark.asyncio
async def test_add_routes_to_retain_under_mapped_bank():
    fake = FakeHindsightClient()
    p = HindsightProvider(client=fake, client_id="hermes")
    res = await p.add("Alice works at Google", dataset="private:hermes", client_id="hermes")
    assert set(res) == {"id", "timestamp"}
    assert fake.retained[0]["bank_id"] == "private__hermes"
    # The returned id IS the document_id (the join key), not a fact id.
    assert fake.retained[0]["document_id"] == res["id"]


class FakeReranker:
    """Reverses input order so we can prove the merge re-ranked the union."""

    async def rerank(self, query: str, documents: list[str]) -> list[dict]:
        n = len(documents)
        return [{"index": i, "relevance_score": float(n - i)} for i in range(n)]


@pytest.mark.asyncio
async def test_recall_fans_out_across_allowed_banks_and_merges():
    fake = FakeHindsightClient()
    await fake.retain(bank_id="shared", content="shared fact", document_id="d-shared", tags=[])
    await fake.retain(
        bank_id="private__hermes", content="private fact", document_id="d-priv", tags=[]
    )
    await fake.retain(
        bank_id="private__other", content="other private", document_id="d-other", tags=[]
    )

    p = HindsightProvider(client=fake, client_id="hermes", reranker=FakeReranker())
    out = await p.recall("fact", dataset="shared", client_id="hermes")

    banks_queried = {c["bank_id"] for c in fake.recalled}
    # Fans out to own-private + shared; NEVER another agent's private.
    assert banks_queried == {"shared", "private__hermes"}
    texts = {r["text"] for r in out}
    assert texts == {"shared fact", "private fact"}
    assert "other private" not in texts


@pytest.mark.asyncio
async def test_recall_merge_precedence_tier_overrides_bank_order_and_score():
    # §4b: a tier-0 item (shared/curated) must rank above a tier-1 raw fact
    # EVEN WHEN the tier-1 fact iterates first AND scores higher in the
    # reranker. This is the adversarial layout that isolates _precedence_key:
    #   - dataset=["agents","shared"] → banks iterate [agents, shared, ...],
    #     so the tier-1 "agents" fact is fanned in BEFORE the tier-0 shared one.
    #   - FakeReranker reverses, giving index-0 ("raw", agents) the HIGHER
    #     score, so a score-only sort would also keep "raw" first.
    # Only the tier key flips it. A broken impl (bank-order-only OR score-only)
    # yields out[0]=="raw" and fails this test.
    fake = FakeHindsightClient()
    fake._facts_by_bank["agents"] = [
        {"document_id": "a1", "text": "raw", "type": "experience", "tags": []}
    ]
    fake._facts_by_bank["shared"] = [
        {"document_id": "o1", "text": "win", "type": "observation", "tags": []}
    ]

    p = HindsightProvider(client=fake, client_id="hermes", reranker=FakeReranker())
    out = await p.recall("anything", dataset=["agents", "shared"], client_id="hermes")
    assert [r["text"] for r in out][:2] == ["win", "raw"]  # tier-0 first despite order+score


@pytest.mark.asyncio
async def test_hal0_reranker_posts_rerank_and_parses_results():
    import httpx

    from hal0.memory.hindsight_provider import Hal0Reranker

    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.9}]})

    transport = httpx.MockTransport(handler)
    rr = Hal0Reranker(base_url="http://127.0.0.1:8080")
    orig = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **k: orig(transport=transport)
    try:
        out = await rr.rerank("q", ["doc a", "doc b"])
    finally:
        httpx.AsyncClient = orig
    assert seen["path"] == "/v1/rerankings"
    assert out == [{"index": 0, "relevance_score": 0.9}]


@pytest.mark.asyncio
async def test_hal0_reranker_failsoft_returns_empty_on_error():
    from hal0.memory.hindsight_provider import Hal0Reranker

    rr = Hal0Reranker(base_url="http://127.0.0.1:59999")  # nothing listening
    out = await rr.rerank("q", ["a", "b"])
    assert out == []


@pytest.mark.asyncio
async def test_list_items_fans_out_real_endpoint():
    """list_items fans out to shared + own private; excludes foreign private."""
    fake = FakeHindsightClient()
    # Retain into shared and private__hermes (allowed for client_id=hermes + dataset=shared)
    await fake.retain(bank_id="shared", content="shared fact", document_id="d-shared", tags=["s"])
    await fake.retain(
        bank_id="private__hermes",
        content="hermes private fact",
        document_id="d-hermes",
        tags=["h"],
    )
    # Retain into a foreign private bank — must be excluded
    await fake.retain(
        bank_id="private__other", content="other agent fact", document_id="d-other", tags=[]
    )

    p = HindsightProvider(client=fake, client_id="hermes")
    result = await p.list_items(dataset="shared", client_id="hermes")

    texts = {item["text"] for item in result["items"]}
    assert "shared fact" in texts
    assert "hermes private fact" in texts
    assert "other agent fact" not in texts
    assert result["next_cursor"] is None
    # ids come from the list-endpoint shape (id field, not document_id)
    ids = {item["id"] for item in result["items"]}
    assert "d-shared" in ids
    assert "d-hermes" in ids
