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
        #: raw documents keyed by bank_id, seeded directly by a test (or via
        #: ``retain``, which also files the raw text here — #1794).
        self._documents_by_bank: dict[str, dict[str, dict]] = {}
        self.list_documents_calls: list[str] = []
        #: keyed by bank_id — response-level recall enrichment a test can seed.
        self._entities_by_bank: dict[str, dict] = {}
        self._chunks_by_bank: dict[str, dict] = {}
        self._source_facts_by_bank: dict[str, dict] = {}
        #: capability-surface call logs + canned stores, extended by test cases.
        self.reflected: list[dict] = []
        self.updated_memories: list[dict] = []
        self.mental_models: dict[str, dict] = {}
        self.directives: dict[str, dict] = {}
        self.operations: dict[str, dict] = {}
        self.consolidated: list[str] = []

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
        **kwargs,
    ):
        self.retained.append(
            {
                "bank_id": bank_id,
                "document_id": document_id,
                "content": content,
                "tags": list(tags or []),
                **kwargs,
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
        self._documents_by_bank.setdefault(bank_id, {})[document_id] = {
            "id": document_id,
            "bank_id": bank_id,
            "original_text": content,
            "tags": list(tags or []),
            "created_at": "2026-06-06T00:00:00+00:00",
        }
        return {
            "success": True,
            "bank_id": bank_id,
            "items_count": 1,
            "async": True,
            "operation_id": "op-test",
        }

    async def recall(self, *, bank_id, query, types=None, max_tokens=4096, tags=None, **kwargs):
        self.recalled.append({"bank_id": bank_id, "query": query, "types": types, **kwargs})
        return {
            "results": list(self._facts_by_bank.get(bank_id, [])),
            "entities": self._entities_by_bank.get(bank_id) or {},
            "chunks": self._chunks_by_bank.get(bank_id) or {},
            "source_facts": self._source_facts_by_bank.get(bank_id) or {},
        }

    # ── capability surface (reflect / curate / mental models / directives /
    #    operations / tags / stats / consolidate) ────────────────────────

    async def reflect(self, *, bank_id, query, **kwargs):
        self.reflected.append({"bank_id": bank_id, "query": query, **kwargs})
        return {"text": f"reflection on {query!r}"}

    async def get_memory(self, *, bank_id, memory_id):
        for fact in self._facts_by_bank.get(bank_id, []):
            if fact.get("document_id") == memory_id or fact.get("id") == memory_id:
                return dict(fact)
        return {"id": memory_id, "tags": []}

    async def update_memory(self, *, bank_id, memory_id, **kwargs):
        self.updated_memories.append({"bank_id": bank_id, "memory_id": memory_id, **kwargs})
        return {"id": memory_id, **{k: v for k, v in kwargs.items() if v is not None}}

    async def memory_history(self, *, bank_id, memory_id):
        return {"items": [{"memory_id": memory_id, "bank_id": bank_id}]}

    async def list_mental_models(self, *, bank_id, **kwargs):
        return {"items": [m for m in self.mental_models.values() if m["bank_id"] == bank_id]}

    async def get_mental_model(self, *, bank_id, mental_model_id):
        return self.mental_models.get(mental_model_id, {"id": mental_model_id, "bank_id": bank_id})

    async def create_mental_model(self, *, bank_id, name, source_query, id=None, **kwargs):
        mm_id = id or f"mm-{len(self.mental_models) + 1}"
        self.mental_models[mm_id] = {
            "id": mm_id,
            "bank_id": bank_id,
            "name": name,
            "source_query": source_query,
        }
        return {"mental_model_id": mm_id, "operation_id": "op-mm"}

    async def update_mental_model(self, *, bank_id, mental_model_id, **kwargs):
        existing = self.mental_models.setdefault(
            mental_model_id, {"id": mental_model_id, "bank_id": bank_id}
        )
        existing.update({k: v for k, v in kwargs.items() if v is not None})
        return dict(existing)

    async def delete_mental_model(self, *, bank_id, mental_model_id):
        self.mental_models.pop(mental_model_id, None)
        return {"success": True}

    async def refresh_mental_model(self, *, bank_id, mental_model_id):
        return {"operation_id": "op-refresh", "status": "queued"}

    async def list_directives(self, *, bank_id, **kwargs):
        return {"items": [d for d in self.directives.values() if d["bank_id"] == bank_id]}

    async def get_directive(self, *, bank_id, directive_id):
        return self.directives.get(directive_id, {"id": directive_id, "bank_id": bank_id})

    async def create_directive(self, *, bank_id, name, content, **kwargs):
        d_id = f"dir-{len(self.directives) + 1}"
        self.directives[d_id] = {"id": d_id, "bank_id": bank_id, "name": name, "content": content}
        return dict(self.directives[d_id])

    async def update_directive(self, *, bank_id, directive_id, **kwargs):
        existing = self.directives.setdefault(
            directive_id, {"id": directive_id, "bank_id": bank_id}
        )
        existing.update({k: v for k, v in kwargs.items() if v is not None})
        return dict(existing)

    async def delete_directive(self, *, bank_id, directive_id):
        self.directives.pop(directive_id, None)
        return {"success": True}

    async def list_operations(self, *, bank_id, **kwargs):
        return {"operations": [o for o in self.operations.values() if o["bank_id"] == bank_id]}

    async def get_operation(self, *, bank_id, operation_id, **kwargs):
        return self.operations.get(
            operation_id, {"operation_id": operation_id, "bank_id": bank_id, "status": "completed"}
        )

    async def cancel_operation(self, *, bank_id, operation_id):
        return {"success": True, "operation_id": operation_id}

    async def retry_operation(self, *, bank_id, operation_id):
        return {"success": True, "operation_id": operation_id}

    async def list_tags(self, *, bank_id, **kwargs):
        tags: dict[str, int] = {}
        for fact in self._facts_by_bank.get(bank_id, []):
            for t in fact.get("tags") or []:
                tags[t] = tags.get(t, 0) + 1
        items = [{"tag": t, "count": c} for t, c in tags.items()]
        return {"items": items, "total": len(items), "limit": 100, "offset": 0}

    async def bank_stats(self, *, bank_id, **kwargs):
        return {"bank_id": bank_id, "total_nodes": len(self._facts_by_bank.get(bank_id, []))}

    async def consolidate(self, *, bank_id):
        self.consolidated.append(bank_id)
        return {"operation_id": "op-consolidate", "deduplicated": False}

    async def delete_document(self, *, bank_id, document_id):
        self.deleted.append(document_id)
        facts = self._facts_by_bank.get(bank_id, [])
        before = len(facts)
        self._facts_by_bank[bank_id] = [f for f in facts if f["document_id"] != document_id]
        return {"memory_units_deleted": before - len(self._facts_by_bank[bank_id])}

    async def list_memories(self, *, bank_id, limit=50, offset=0, types=None, query=None):
        all_facts = list(self._facts_by_bank.get(bank_id, []))
        # Honour limit/offset the way Hindsight's /memories/list does — this
        # double used to ignore both and return the whole bank every time,
        # which made a paging bug in the provider untestable (#1471).
        raw = all_facts[offset : offset + limit]
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
        return {"items": items, "total": len(all_facts), "limit": limit, "offset": offset}

    # ── raw documents (#1794 fallback leg) ────────────────────────────────

    async def list_documents(
        self, *, bank_id, q=None, tags=None, tags_match=None, limit=100, offset=0
    ):
        self.list_documents_calls.append(bank_id)
        docs = list(self._documents_by_bank.get(bank_id, {}).values())
        page = docs[offset : offset + limit]
        items = [{"id": d["id"], "text_length": len(d.get("original_text") or "")} for d in page]
        return {"items": items, "total": len(docs), "limit": limit, "offset": offset}

    async def get_document(self, *, bank_id, document_id):
        doc = self._documents_by_bank.get(bank_id, {}).get(document_id)
        if doc is None:
            raise Fake404HindsightClient.NotFound()
        return dict(doc)


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
    # items_count/operation_ids are surfaced whenever the engine reports them
    # (RetainResponse.items_count is a required field) — no longer dropped.
    assert (
        {"id", "timestamp", "operation_id"}
        <= set(res)
        <= {
            "id",
            "timestamp",
            "operation_id",
            "operation_ids",
            "items_count",
        }
    )
    assert fake.retained[0]["bank_id"] == "private__hermes"
    # The returned id IS the document_id (the join key), not a fact id.
    assert fake.retained[0]["document_id"] == res["id"]
    # retain is async on this engine — the operation id surfaces for polling.
    assert res["operation_id"] == "op-test"


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


# ── PR: delete 404-sweep, add upsert/operation_id, recall type defaults ──────


class Fake404HindsightClient(FakeHindsightClient):
    """Models the REAL Hindsight delete contract: a document missing from a
    bank is a 404 (httpx raise_for_status), not an idempotent zero-count."""

    class _Resp:
        status_code = 404

    class NotFound(Exception):
        def __init__(self) -> None:
            super().__init__("Client error '404 Not Found' for url 'http://127.0.0.1:9177/...'")
            self.response = Fake404HindsightClient._Resp()

    async def delete_document(self, *, bank_id, document_id):
        facts = self._facts_by_bank.get(bank_id, [])
        if not any(f["document_id"] == document_id for f in facts):
            raise Fake404HindsightClient.NotFound()
        return await super().delete_document(bank_id=bank_id, document_id=document_id)


@pytest.mark.asyncio
async def test_delete_sweep_survives_404_and_reaches_private_bank():
    """The shared bank is probed first and 404s for a private-bank item —
    the sweep must continue, not abort (the abort made every private item
    undeletable in production)."""
    fake = Fake404HindsightClient()
    await fake.retain(bank_id="private__hermes", content="secret", document_id="d-priv", tags=[])

    p = HindsightProvider(client=fake, client_id="hermes")
    res = await p.delete(["d-priv"], client_id="hermes")
    assert res == {"deleted": 1}
    assert not fake._facts_by_bank["private__hermes"]


@pytest.mark.asyncio
async def test_delete_missing_everywhere_counts_zero():
    fake = Fake404HindsightClient()
    p = HindsightProvider(client=fake, client_id="hermes")
    res = await p.delete(["nope"], client_id="hermes")
    assert res == {"deleted": 0}


@pytest.mark.asyncio
async def test_delete_non_404_engine_errors_still_raise():
    """Only the missing-document 404 is swallowed; a real engine failure
    (5xx, connection refused) must propagate."""

    class _Resp:
        status_code = 503

    class _Boom(Exception):
        response = _Resp()

    class _BoomClient(FakeHindsightClient):
        async def delete_document(self, *, bank_id, document_id):
            raise _Boom()

    p = HindsightProvider(client=_BoomClient(), client_id="hermes")
    with pytest.raises(_Boom):
        await p.delete(["d1"], client_id="hermes")


@pytest.mark.asyncio
async def test_delete_dataset_directs_sweep_to_project_bank():
    """An explicit dataset reaches banks outside the default sweep."""
    fake = Fake404HindsightClient()
    await fake.retain(bank_id="project__apollo", content="x", document_id="d-proj", tags=[])

    p = HindsightProvider(client=fake, client_id="hermes")
    res = await p.delete(["d-proj"], client_id="hermes", dataset="project:apollo")
    assert res == {"deleted": 1}


@pytest.mark.asyncio
async def test_add_caller_document_id_upserts_same_document():
    """Reusing a document_id pins the engine join key (conversation upsert)."""
    fake = FakeHindsightClient()
    p = HindsightProvider(client=fake, client_id="hermes")
    r1 = await p.add("turn 1", dataset="shared", client_id="hermes", document_id="conv-1")
    r2 = await p.add("turn 1+2", dataset="shared", client_id="hermes", document_id="conv-1")
    assert r1["id"] == r2["id"] == "conv-1"
    assert [r["document_id"] for r in fake.retained] == ["conv-1", "conv-1"]


@pytest.mark.asyncio
async def test_recall_default_types_include_observations():
    """Hindsight's own default (world+experience) hides the consolidated
    observation layer; hal0's default must request it explicitly."""
    fake = FakeHindsightClient()
    await fake.retain(bank_id="shared", content="x", document_id="d1", tags=[])
    p = HindsightProvider(client=fake, client_id="hermes")

    await p.recall("anything", dataset="shared", client_id="hermes")
    assert fake.recalled[0]["types"] == ["world", "experience", "observation"]

    fake.recalled.clear()
    await p.recall("anything", dataset="shared", client_id="hermes", types=["world"])
    assert fake.recalled[0]["types"] == ["world"]


# ── #1471 item 2: search's time-window filters must not be silently dropped ───
#
# search() accepted before/after/mode and delegated to recall() without them,
# so a caller filtering by time window got UNFILTERED results with no error —
# while PgVectorProvider (the degrade fallback) honours them. Two engines, two
# behaviours, one advertised contract.


def _stamped(text: str, when: str) -> dict:
    return {"document_id": text, "text": text, "tags": [], "mentioned_at": when}


async def _seed_stamped(fake: FakeHindsightClient) -> None:
    for text, when in (
        ("old fact", "2026-01-01T00:00:00+00:00"),
        ("mid fact", "2026-06-01T00:00:00+00:00"),
        ("new fact", "2026-12-01T00:00:00+00:00"),
    ):
        await fake.retain(bank_id="shared", content=text, document_id=text, tags=[])
        fake._facts_by_bank["shared"][-1]["mentioned_at"] = when


@pytest.mark.asyncio
async def test_search_after_filters_out_older_items():
    fake = FakeHindsightClient()
    await _seed_stamped(fake)
    p = HindsightProvider(client=fake, client_id="hermes")

    out = await p.search("fact", dataset="shared", client_id="hermes", after="2026-05-01")
    texts = {r["text"] for r in out}
    assert texts == {"mid fact", "new fact"}


@pytest.mark.asyncio
async def test_search_before_filters_out_newer_items():
    fake = FakeHindsightClient()
    await _seed_stamped(fake)
    p = HindsightProvider(client=fake, client_id="hermes")

    out = await p.search("fact", dataset="shared", client_id="hermes", before="2026-07-01")
    texts = {r["text"] for r in out}
    assert texts == {"old fact", "mid fact"}


@pytest.mark.asyncio
async def test_search_window_combines_both_bounds():
    fake = FakeHindsightClient()
    await _seed_stamped(fake)
    p = HindsightProvider(client=fake, client_id="hermes")

    out = await p.search(
        "fact", dataset="shared", client_id="hermes", after="2026-03-01", before="2026-09-01"
    )
    assert {r["text"] for r in out} == {"mid fact"}


@pytest.mark.asyncio
async def test_search_without_a_window_is_unchanged():
    """The filter must be inert when no bounds are given — this is the path
    every existing caller takes."""
    fake = FakeHindsightClient()
    await _seed_stamped(fake)
    p = HindsightProvider(client=fake, client_id="hermes")

    out = await p.search("fact", dataset="shared", client_id="hermes")
    assert {r["text"] for r in out} == {"old fact", "mid fact", "new fact"}


@pytest.mark.asyncio
async def test_search_rejects_a_mode_the_engine_cannot_honour():
    """``mode`` was inert everywhere. Silently ignoring a caller's explicit
    request for graph traversal is the failure this item is about — say so."""
    fake = FakeHindsightClient()
    await _seed_stamped(fake)
    p = HindsightProvider(client=fake, client_id="hermes")

    with pytest.raises(ValueError) as exc:
        await p.search("fact", dataset="shared", client_id="hermes", mode="graph")
    assert "graph" in str(exc.value)


# ── #1471 item 3: list_items pagination is real ──────────────────────────────
#
# list_items() took a `cursor`, never used it, always called list_memories with
# offset=0 and returned next_cursor=None — so only the first page of a
# 1629-item bank was reachable. The per-bank loop also broke on the raw item
# count BEFORE the visibility filter, so a unified-mode reader could get fewer
# than `limit` items even when more visible ones existed.


@pytest.mark.asyncio
async def test_list_items_emits_a_cursor_when_more_remain():
    fake = FakeHindsightClient()
    for i in range(5):
        await fake.retain(bank_id="shared", content=f"f{i}", document_id=f"d{i}", tags=[])
    p = HindsightProvider(client=fake, client_id="hermes")

    page = await p.list_items(dataset="shared", limit=2, client_id="hermes")
    assert len(page["items"]) == 2
    assert page["next_cursor"], "a bank with more rows must hand back a cursor"


@pytest.mark.asyncio
async def test_list_items_cursor_walks_the_whole_bank():
    fake = FakeHindsightClient()
    for i in range(5):
        await fake.retain(bank_id="shared", content=f"f{i}", document_id=f"d{i}", tags=[])
    p = HindsightProvider(client=fake, client_id="hermes")

    seen: list[str] = []
    cursor = None
    for _ in range(10):  # generous bound; the walk must terminate well inside it
        page = await p.list_items(dataset="shared", limit=2, cursor=cursor, client_id="hermes")
        seen.extend(item["text"] for item in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert cursor is None, "the walk must terminate"
    assert seen == [f"f{i}" for i in range(5)], "every row reachable, exactly once, in order"


@pytest.mark.asyncio
async def test_list_items_last_page_has_no_cursor():
    fake = FakeHindsightClient()
    for i in range(2):
        await fake.retain(bank_id="shared", content=f"f{i}", document_id=f"d{i}", tags=[])
    p = HindsightProvider(client=fake, client_id="hermes")

    page = await p.list_items(dataset="shared", limit=10, client_id="hermes")
    assert len(page["items"]) == 2
    assert page["next_cursor"] is None


# ── #1667: list_items cursor math must not skip ACL-withheld rows ───────────
#
# ``consumed = bank_offset - max(0, len(items) - limit)`` corrects the raw
# offset by the number of *visible* items that overflowed the page. That only
# equals the number of raw rows to roll back when the ACL filter dropped
# nothing from the tail of the fetched chunk. In unified-bank mode the shared
# bank interleaves other agents' private docs, so a real page filters
# unevenly and the correction under-rewinds — the next cursor starts past raw
# rows that still held unreturned visible items, and those items become
# permanently unreachable through pagination.


async def _seed_acl_mixed_bank(fake: FakeHindsightClient, total: int, client_id: str) -> int:
    """Retain ``total`` shared-bank docs; every raw index with ``i % 5 in (0, 1)``
    belongs to another agent under ``visibility:private`` (invisible to
    ``client_id``); the rest are ordinary visible shared docs. Returns the
    number of visible docs seeded."""
    visible = 0
    for i in range(total):
        if i % 5 in (0, 1):
            await fake.retain(
                bank_id="shared",
                content=f"f{i}",
                document_id=f"d{i}",
                tags=["visibility:private", "agent:someone-else"],
            )
        else:
            await fake.retain(bank_id="shared", content=f"f{i}", document_id=f"d{i}", tags=[])
            visible += 1
    return visible


@pytest.mark.asyncio
async def test_list_items_cursor_survives_acl_filtering_mid_page():
    """Every visible item must be reachable across pages — none silently
    dropped by a mis-rewound cursor — when the ACL filter thins a page
    unevenly (#1667)."""
    fake = FakeHindsightClient()
    visible_total = await _seed_acl_mixed_bank(fake, total=100, client_id="hermes")
    assert visible_total == 60  # sanity: matches the issue's repro shape

    p = HindsightProvider(client=fake, client_id="hermes", unified_bank=True)

    seen: list[str] = []
    cursor = None
    for _ in range(20):  # generous bound; the walk must terminate well inside it
        page = await p.list_items(dataset="shared", limit=50, cursor=cursor, client_id="hermes")
        seen.extend(item["text"] for item in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break

    assert cursor is None, "the walk must terminate"
    assert len(seen) == len(set(seen)), "no item returned twice"
    expected = {f"f{i}" for i in range(100) if i % 5 in (2, 3, 4)}
    assert set(seen) == expected, "every ACL-visible item must be reachable via pagination"


@pytest.mark.asyncio
async def test_list_items_negative_control_still_withholds_foreign_private_docs():
    """The ACL itself must keep failing closed while the cursor math is
    fixed — a foreign agent's private doc must never surface, on any page,
    to a caller who cannot see it."""
    fake = FakeHindsightClient()
    await _seed_acl_mixed_bank(fake, total=20, client_id="hermes")
    p = HindsightProvider(client=fake, client_id="hermes", unified_bank=True)

    seen: list[str] = []
    cursor = None
    for _ in range(20):
        page = await p.list_items(dataset="shared", limit=5, cursor=cursor, client_id="hermes")
        seen.extend(item["text"] for item in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break

    withheld = {f"f{i}" for i in range(20) if i % 5 in (0, 1)}
    assert not (withheld & set(seen)), "another agent's private docs must never be returned"


# ── recall modernization: native scores, entities/chunks/source_facts ───────


@pytest.mark.asyncio
async def test_recall_uses_native_final_score_and_skips_fallback_rerank():
    """A stale comment used to claim Hindsight recall returns no numeric
    score. 0.8.x does (RecallScores.final) — use it, and skip the :8086
    fallback reranker entirely when the union already carries one."""

    class _RerankShouldNotRun:
        async def rerank(self, query, documents):
            raise AssertionError("fallback reranker must not run when native scores are present")

    fake = FakeHindsightClient()
    fake._facts_by_bank["shared"] = [
        {"document_id": "d1", "text": "low", "type": "world", "tags": [], "scores": {"final": 0.2}},
        {
            "document_id": "d2",
            "text": "high",
            "type": "world",
            "tags": [],
            "scores": {"final": 0.9},
        },
    ]
    p = HindsightProvider(client=fake, client_id="hermes", reranker=_RerankShouldNotRun())

    out = await p.recall("q", dataset="shared", client_id="hermes")
    assert [i["text"] for i in out] == ["high", "low"]
    assert [i["score"] for i in out] == [0.9, 0.2]


@pytest.mark.asyncio
async def test_recall_falls_back_to_reranker_when_engine_supplies_no_scores():
    """Older Hindsight (no ``scores`` field) — the :8086 fallback must still
    run, matching pre-modernization behaviour."""
    fake = FakeHindsightClient()
    fake._facts_by_bank["shared"] = [
        {"document_id": "d1", "text": "a", "type": "world", "tags": []},
        {"document_id": "d2", "text": "b", "type": "world", "tags": []},
    ]
    p = HindsightProvider(client=fake, client_id="hermes", reranker=FakeReranker())

    out = await p.recall("q", dataset="shared", client_id="hermes")
    # FakeReranker reverses order — proves the fallback ran.
    assert [i["score"] for i in out] == [2.0, 1.0]


@pytest.mark.asyncio
async def test_recall_returns_list_compatible_results_with_entities_chunks_attrs():
    """``recall()``'s return is a list byte-for-byte (every existing caller
    keeps working) AND carries the response-level enrichment as attributes."""
    fake = FakeHindsightClient()
    fake._facts_by_bank["shared"] = [
        {"document_id": "d1", "text": "Alice works at Google", "type": "world", "tags": []}
    ]
    fake._entities_by_bank["shared"] = {"Alice": {"entity_id": "e1", "canonical_name": "Alice"}}
    fake._chunks_by_bank["shared"] = {"c1": {"id": "c1", "text": "chunk text", "chunk_index": 0}}
    p = HindsightProvider(client=fake, client_id="hermes")

    out = await p.recall("Alice", dataset="shared", client_id="hermes")
    assert isinstance(out, list)
    assert out == [
        {
            "id": "d1",
            "text": "Alice works at Google",
            "timestamp": out[0]["timestamp"],
            "dataset": "shared",
            "tags": [],
            "source": None,
            "metadata": {},
            "score": None,
            "type": "world",
            "entities": None,
            "source_fact_ids": None,
            "kind": "fact",
        }
    ]
    assert out.entities == {"Alice": {"entity_id": "e1", "canonical_name": "Alice"}}
    assert out.chunks == {"c1": {"id": "c1", "text": "chunk text", "chunk_index": 0}}
    assert out.source_facts is None  # nothing seeded -> omitted, not an empty dict


@pytest.mark.asyncio
async def test_recall_merges_entities_across_fanned_out_banks():
    fake = FakeHindsightClient()
    fake._facts_by_bank["shared"] = [{"document_id": "d1", "text": "shared", "tags": []}]
    fake._facts_by_bank["private__hermes"] = [{"document_id": "d2", "text": "priv", "tags": []}]
    fake._entities_by_bank["shared"] = {"A": {"entity_id": "a"}}
    fake._entities_by_bank["private__hermes"] = {"B": {"entity_id": "b"}}
    p = HindsightProvider(client=fake, client_id="hermes")

    out = await p.recall("q", dataset="shared", client_id="hermes")
    assert out.entities == {"A": {"entity_id": "a"}, "B": {"entity_id": "b"}}


@pytest.mark.asyncio
async def test_recall_forwards_extended_knobs_only_when_set():
    """Mirrors the existing tags_match convention: unset knobs never reach
    the client, so a narrow fake (no **kwargs) stays compatible."""
    fake = FakeHindsightClient()
    p = HindsightProvider(client=fake, client_id="hermes")

    await p.recall("q", dataset="shared", client_id="hermes")
    call = fake.recalled[0]
    for key in (
        "tags_match",
        "tag_groups",
        "budget",
        "prefer_observations",
        "include",
        "query_timestamp",
        "min_scores",
    ):
        assert key not in call

    fake.recalled.clear()
    await p.recall(
        "q",
        dataset="shared",
        client_id="hermes",
        tag_groups=[{"tags": ["x"], "match": "any"}],
        budget="high",
        prefer_observations=True,
        include={"chunks": {}},
        query_timestamp="2026-01-01",
        min_scores={"final": 0.1},
    )
    call = fake.recalled[0]
    assert call["tag_groups"] == [{"tags": ["x"], "match": "any"}]
    assert call["budget"] == "high"
    assert call["prefer_observations"] is True
    assert call["include"] == {"chunks": {}}
    assert call["query_timestamp"] == "2026-01-01"
    assert call["min_scores"] == {"final": 0.1}


@pytest.mark.asyncio
async def test_search_forwards_tag_groups_and_min_scores_to_recall():
    fake = FakeHindsightClient()
    p = HindsightProvider(client=fake, client_id="hermes")

    await p.search(
        "q",
        dataset="shared",
        client_id="hermes",
        tag_groups=[{"tags": ["x"], "match": "any"}],
        min_scores={"final": 0.5},
    )
    assert fake.recalled[0]["tag_groups"] == [{"tags": ["x"], "match": "any"}]
    assert fake.recalled[0]["min_scores"] == {"final": 0.5}


# ── add() modernization: RetainRequest full shape + sync ────────────────────


@pytest.mark.asyncio
async def test_add_forwards_retain_shape_only_when_set():
    fake = FakeHindsightClient()
    p = HindsightProvider(client=fake, client_id="hermes")

    await p.add("plain", dataset="shared", client_id="hermes")
    rec = fake.retained[0]
    for key in ("entities", "observation_scopes", "strategy", "update_mode", "sync"):
        assert key not in rec

    await p.add(
        "rich",
        dataset="shared",
        client_id="hermes",
        entities=[{"text": "Alice"}],
        observation_scopes="shared",
        strategy="fast",
        update_mode="append",
        sync=True,
    )
    rec = fake.retained[1]
    assert rec["entities"] == [{"text": "Alice"}]
    assert rec["observation_scopes"] == "shared"
    assert rec["strategy"] == "fast"
    assert rec["update_mode"] == "append"
    assert rec["sync"] is True


@pytest.mark.asyncio
async def test_add_surfaces_items_count_and_operation_ids():
    class _MultiOpClient(FakeHindsightClient):
        async def retain(self, **kwargs):
            await super().retain(**kwargs)
            return {
                "success": True,
                "bank_id": kwargs["bank_id"],
                "items_count": 3,
                "async": True,
                "operation_ids": ["op-a", "op-b"],
            }

    p = HindsightProvider(client=_MultiOpClient(), client_id="hermes")
    res = await p.add("x", dataset="shared", client_id="hermes")
    assert res["items_count"] == 3
    assert res["operation_ids"] == ["op-a", "op-b"]
    # operation_id mirrors the first entry per RetainResponse's back-compat rule
    # only when the engine actually sets it — this fake doesn't, so it's absent.
    assert "operation_id" not in res


# ── reflect: single-bank resolution ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_reflect_resolves_single_bank():
    fake = FakeHindsightClient()
    p = HindsightProvider(client=fake, client_id="hermes")

    out = await p.reflect("what do you know?", dataset="private:hermes", client_id="hermes")
    assert out["text"] == "reflection on 'what do you know?'"
    assert fake.reflected[0]["bank_id"] == "private__hermes"
    assert fake.reflected[0]["query"] == "what do you know?"


@pytest.mark.asyncio
async def test_reflect_default_dataset_is_shared_bank():
    fake = FakeHindsightClient()
    p = HindsightProvider(client=fake, client_id="hermes")
    await p.reflect("q", client_id="hermes")
    assert fake.reflected[0]["bank_id"] == "shared"


# ── curate / memory_history: non-destructive correction path ────────────────


@pytest.mark.asyncio
async def test_curate_patches_the_resolved_bank():
    fake = FakeHindsightClient()
    p = HindsightProvider(client=fake, client_id="hermes")

    await p.curate(
        "fact-1", dataset="shared", client_id="hermes", state="invalidated", reason="wrong"
    )
    call = fake.updated_memories[0]
    assert call["bank_id"] == "shared"
    assert call["memory_id"] == "fact-1"
    assert call["state"] == "invalidated"
    assert call["reason"] == "wrong"


@pytest.mark.asyncio
async def test_curate_legacy_mode_skips_visibility_check():
    """Pre-unified multi-bank mode: bank isolation already protects — no
    extra GET-before-PATCH round trip."""
    fake = FakeHindsightClient()
    p = HindsightProvider(client=fake, client_id="hermes", unified_bank=False)
    await p.curate("fact-1", dataset="shared", client_id="hermes", text="fixed")
    assert fake.updated_memories  # succeeded without a get_memory call


@pytest.mark.asyncio
async def test_curate_unified_mode_rejects_foreign_private_memory():
    """A caller must not curate another agent's private memory by guessing
    its id — same fail-closed posture as delete's visibility gate."""
    fake = FakeHindsightClient()
    fake._facts_by_bank["shared"] = [
        {
            "document_id": "d1",
            "id": "fact-1",
            "text": "secret",
            "tags": ["visibility:private", "agent:other"],
        }
    ]
    p = HindsightProvider(client=fake, client_id="hermes", unified_bank=True)

    with pytest.raises(PermissionError):
        await p.curate("fact-1", dataset="shared", client_id="hermes", text="tampered")
    assert not fake.updated_memories


@pytest.mark.asyncio
async def test_curate_unified_mode_allows_own_private_memory():
    fake = FakeHindsightClient()
    fake._facts_by_bank["shared"] = [
        {
            "document_id": "d1",
            "id": "fact-1",
            "text": "secret",
            "tags": ["visibility:private", "agent:hermes"],
        }
    ]
    p = HindsightProvider(client=fake, client_id="hermes", unified_bank=True)

    await p.curate("fact-1", dataset="shared", client_id="hermes", text="corrected")
    assert fake.updated_memories[0]["memory_id"] == "fact-1"


@pytest.mark.asyncio
async def test_memory_history_returns_engine_payload():
    fake = FakeHindsightClient()
    p = HindsightProvider(client=fake, client_id="hermes")
    out = await p.memory_history("fact-1", dataset="shared", client_id="hermes")
    assert out["items"][0]["memory_id"] == "fact-1"


# ── mental models ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mental_model_create_get_update_delete_refresh_round_trip():
    fake = FakeHindsightClient()
    p = HindsightProvider(client=fake, client_id="hermes")

    created = await p.create_mental_model(
        name="Team prefs",
        source_query="How does the team communicate?",
        dataset="shared",
        client_id="hermes",
    )
    mm_id = created["mental_model_id"]
    assert fake.mental_models[mm_id]["bank_id"] == "shared"

    fetched = await p.get_mental_model(mm_id, dataset="shared", client_id="hermes")
    assert fetched["name"] == "Team prefs"

    updated = await p.update_mental_model(
        mm_id, dataset="shared", client_id="hermes", name="Renamed"
    )
    assert updated["name"] == "Renamed"

    refreshed = await p.refresh_mental_model(mm_id, dataset="shared", client_id="hermes")
    assert refreshed["operation_id"] == "op-refresh"

    listed = await p.list_mental_models(dataset="shared", client_id="hermes")
    assert any(m["id"] == mm_id for m in listed["items"])

    await p.delete_mental_model(mm_id, dataset="shared", client_id="hermes")
    assert mm_id not in fake.mental_models


# ── directives ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_directive_create_list_update_delete_round_trip():
    fake = FakeHindsightClient()
    p = HindsightProvider(client=fake, client_id="hermes")

    created = await p.create_directive(
        name="Be terse", content="Prefer short answers.", dataset="shared", client_id="hermes"
    )
    d_id = created["id"]

    listed = await p.list_directives(dataset="shared", client_id="hermes")
    assert any(d["id"] == d_id for d in listed["items"])

    fetched = await p.get_directive(d_id, dataset="shared", client_id="hermes")
    assert fetched["content"] == "Prefer short answers."

    updated = await p.update_directive(d_id, dataset="shared", client_id="hermes", priority=5)
    assert updated["priority"] == 5

    await p.delete_directive(d_id, dataset="shared", client_id="hermes")
    remaining = await p.list_directives(dataset="shared", client_id="hermes")
    assert not any(d["id"] == d_id for d in remaining["items"])


# ── async operations ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_operation_get_list_cancel_retry():
    fake = FakeHindsightClient()
    fake.operations["op-1"] = {"operation_id": "op-1", "bank_id": "shared", "status": "failed"}
    p = HindsightProvider(client=fake, client_id="hermes")

    got = await p.get_operation("op-1", dataset="shared", client_id="hermes")
    assert got["status"] == "failed"

    listed = await p.list_operations(dataset="shared", client_id="hermes")
    assert any(o["operation_id"] == "op-1" for o in listed["operations"])

    cancelled = await p.cancel_operation("op-1", dataset="shared", client_id="hermes")
    assert cancelled["success"] is True

    retried = await p.retry_operation("op-1", dataset="shared", client_id="hermes")
    assert retried["success"] is True


# ── tags / bank stats / consolidation ────────────────────────────────────


@pytest.mark.asyncio
async def test_list_tags_reflects_stored_facts():
    fake = FakeHindsightClient()
    await fake.retain(bank_id="shared", content="x", document_id="d1", tags=["alpha", "beta"])
    p = HindsightProvider(client=fake, client_id="hermes")

    out = await p.list_tags(dataset="shared", client_id="hermes")
    tags = {item["tag"] for item in out["items"]}
    assert {"alpha", "beta"} <= tags


@pytest.mark.asyncio
async def test_bank_stats_resolves_single_bank():
    fake = FakeHindsightClient()
    await fake.retain(bank_id="private__hermes", content="x", document_id="d1", tags=[])
    p = HindsightProvider(client=fake, client_id="hermes")

    out = await p.bank_stats(dataset="private:hermes", client_id="hermes")
    assert out["bank_id"] == "private__hermes"
    assert out["total_nodes"] == 1


@pytest.mark.asyncio
async def test_consolidate_real_implementation_hits_engine():
    """Overrides the ABC's ``{"status": "unsupported"}`` stub — this is the
    'real consolidate replacing stub' requirement."""
    fake = FakeHindsightClient()
    p = HindsightProvider(client=fake, client_id="hermes")

    out = await p.consolidate(dataset="shared", client_id="hermes")
    assert out == {"operation_id": "op-consolidate", "deduplicated": False}
    assert fake.consolidated == ["shared"]


# ── raw-document recall fallback (#1794) ──────────────────────────────────
#
# Fact extraction can rewrite a retained fact as a meta-observation and drop
# a literal value (marker/ID/key) entirely, even though the raw document
# text — filed alongside the fact — still holds it verbatim. These tests
# exercise HindsightProvider.recall()'s fallback: when the query names a
# literal-looking token no extracted fact's text contains, it scans a
# bounded page of the bank's raw documents and augments (never replaces)
# the fact results with any hit, carrying ``kind: "document"`` provenance.


def _seed_extraction_gap(fake: FakeHindsightClient, *, bank_id: str, marker: str) -> None:
    """Model the #1794 shape directly: the extracted fact is a meta-
    observation with NO literal value, but the raw document still has it —
    exactly what a real 0.8B-model extraction produces."""
    fake._facts_by_bank.setdefault(bank_id, []).append(
        {
            "document_id": "d-meta",
            "text": "User asks about the CT151 validation marker",
            "tags": [],
            "mentioned_at": "2026-08-09T00:00:00+00:00",
        }
    )
    fake._documents_by_bank.setdefault(bank_id, {})["d-raw"] = {
        "id": "d-raw",
        "bank_id": bank_id,
        "original_text": f"the CT151 validation marker is {marker}",
        "tags": [],
        "created_at": "2026-08-09T00:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_recall_falls_back_to_raw_document_for_dropped_literal():
    fake = FakeHindsightClient()
    _seed_extraction_gap(fake, bank_id="shared", marker="MARKER-7734")
    p = HindsightProvider(client=fake, client_id="hermes")

    out = await p.recall(
        "what is the validation marker MARKER-7734", dataset="shared", client_id="hermes"
    )

    kinds = {item["text"]: item.get("kind") for item in out}
    # The meta-fact is still present — augment, not displace.
    assert kinds["User asks about the CT151 validation marker"] == "fact"
    # The raw document carrying the literal is appended with document provenance.
    doc_hits = [item for item in out if item.get("kind") == "document"]
    assert len(doc_hits) == 1
    assert "MARKER-7734" in doc_hits[0]["text"]
    assert doc_hits[0]["source"] == "raw_document"
    assert doc_hits[0]["metadata"]["document_id"] == "d-raw"


@pytest.mark.asyncio
async def test_recall_skips_fallback_when_fact_already_has_the_literal():
    fake = FakeHindsightClient()
    fake._facts_by_bank.setdefault("shared", []).append(
        {
            "document_id": "d1",
            "text": "the validation marker is MARKER-7734",
            "tags": [],
            "mentioned_at": "2026-08-09T00:00:00+00:00",
        }
    )
    p = HindsightProvider(client=fake, client_id="hermes")

    out = await p.recall("MARKER-7734", dataset="shared", client_id="hermes")

    assert all(item.get("kind") == "fact" for item in out)
    assert fake.list_documents_calls == []  # no wasted scan — the fact already covers it


@pytest.mark.asyncio
async def test_recall_never_scans_documents_for_a_plain_query():
    fake = FakeHindsightClient()
    fake._facts_by_bank.setdefault("shared", []).append(
        {
            "document_id": "d1",
            "text": "Alice likes coffee",
            "tags": [],
            "mentioned_at": "2026-08-09T00:00:00+00:00",
        }
    )
    p = HindsightProvider(client=fake, client_id="hermes")

    out = await p.recall("what does Alice like", dataset="shared", client_id="hermes")

    assert [item.get("kind") for item in out] == ["fact"]
    assert fake.list_documents_calls == []


@pytest.mark.asyncio
async def test_document_fallback_withholds_foreign_private_doc_in_unified_mode():
    """Unified-bank ACL applies to the fallback too: a doc tagged private to
    another agent must not leak into the caller's recall just because it
    happens to contain the queried literal."""
    fake = FakeHindsightClient()
    fake._facts_by_bank.setdefault("shared", []).append(
        {
            "document_id": "d-meta",
            "text": "someone asked about a marker",
            "tags": [],
            "mentioned_at": "2026-08-09T00:00:00+00:00",
        }
    )
    fake._documents_by_bank.setdefault("shared", {})["d-raw"] = {
        "id": "d-raw",
        "bank_id": "shared",
        "original_text": "the secret marker is MARKER-9999",
        "tags": ["visibility:private", "agent:other-agent"],
        "created_at": "2026-08-09T00:00:00+00:00",
    }
    p = HindsightProvider(client=fake, client_id="hermes", unified_bank=True)

    out = await p.recall("MARKER-9999", dataset="shared", client_id="hermes")

    assert all(item.get("kind") != "document" for item in out)
