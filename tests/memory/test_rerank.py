"""Issue #116 G4 — rerank slot integration tests.

These tests do NOT run the real Cognee retrieval — that's already
covered by ``test_cognee_wrapper.py``. Here we monkeypatch
``cognee.search`` so we can stage a deterministic candidate set and
assert on the wrapper's rerank wiring:

  - rerank_enabled=False: vector ordering is preserved + no HTTP call
    fires.
  - rerank_enabled=True with a live (mock) rerank slot: candidates get
    reordered by the relevance scores the slot returns.
  - rerank_enabled=True but slot is unreachable / 5xx / malformed:
    wrapper falls through silently, vector ordering is preserved, no
    exception escapes search().

The config schema check is plain pydantic — no Cognee install needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("cognee")


# ── Schema defaults (no Cognee needed) ────────────────────────────────────


def test_memory_embedding_config_defaults() -> None:
    """Issue #116 G3 — embedding section must round-trip empty TOML.

    Default behavior must be byte-identical to v0.3.0: rerank off, model
    pinned to the existing Cognee stock value so an upgrade does NOT
    silently re-embed an existing LanceDB index.
    """
    from hal0.config.schema import MemoryConfig, MemoryEmbeddingConfig

    cfg = MemoryEmbeddingConfig()
    assert cfg.model == "BAAI/bge-small-en-v1.5"
    assert cfg.rerank_enabled is False
    assert cfg.rerank_url == "http://127.0.0.1:8083"

    # Nested under MemoryConfig the field must default-construct.
    mem = MemoryConfig()
    assert mem.embedding.rerank_enabled is False


def test_memory_embedding_config_rejects_empty_model() -> None:
    """Empty model is a config error — a blank field would silently let
    Cognee fall back to its hard-coded default, defeating the pin."""
    from hal0.config.schema import MemoryEmbeddingConfig

    with pytest.raises(ValueError):
        MemoryEmbeddingConfig(model="")
    with pytest.raises(ValueError):
        MemoryEmbeddingConfig(rerank_url="   ")


# ── Wrapper rerank wiring ─────────────────────────────────────────────────

# pyproject's ``asyncio_mode = "auto"`` picks up async test functions
# without an explicit ``@pytest.mark.asyncio`` decorator — keeping the
# module mark off avoids warning on the two sync schema tests above.


@pytest.fixture
def stub_cognee_search(monkeypatch: pytest.MonkeyPatch):
    """Replace ``cognee.search`` with a 3-chunk staged result.

    The wrapper text-matches its sidecar against the chunks' text, so
    we also need to seed three sidecar rows whose text matches the
    stub's chunk texts. The fixture returns a helper that takes the
    wrapper instance and seeds the sidecar rows.
    """
    import cognee

    # Three fake chunks. Note: the wrapper expects an attribute-style
    # ``text`` / ``score`` (see ``_chunk_text`` + ``_chunk_score`` in
    # cognee_wrapper.py) — MagicMock auto-creates the attrs.
    chunk_a = MagicMock()
    chunk_a.text = "alpha document"
    chunk_a.score = 0.9
    chunk_b = MagicMock()
    chunk_b.text = "beta document"
    chunk_b.score = 0.8
    chunk_c = MagicMock()
    chunk_c.text = "gamma document"
    chunk_c.score = 0.7

    async def fake_search(**kwargs: Any) -> list[Any]:
        return [chunk_a, chunk_b, chunk_c]

    monkeypatch.setattr(cognee, "search", fake_search)

    def seed(wrapper: Any) -> None:
        """Drop three rows into the wrapper's sidecar so text-match hits."""
        import json
        import uuid
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        with wrapper._sidecar_conn() as conn:
            for text in ("alpha document", "beta document", "gamma document"):
                conn.execute(
                    """
                    INSERT INTO hal0_memory_items
                        (id, text, timestamp, dataset, tags, source, metadata,
                         cognee_data_id, cognee_dataset_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        text,
                        now,
                        "shared",
                        json.dumps([]),
                        "test",
                        json.dumps({}),
                        None,
                        None,
                    ),
                )
            conn.commit()

    return seed


@pytest.fixture
def wrapper_factory(cognee_dir: Path):
    from hal0.memory import CogneeWrapper

    def _build(**kwargs: Any) -> Any:
        return CogneeWrapper(cognee_dir=cognee_dir, **kwargs)

    return _build


async def test_rerank_disabled_preserves_vector_order_and_skips_http(
    wrapper_factory,
    stub_cognee_search,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default config = rerank off = no HTTP call, vector order preserved."""
    w = wrapper_factory(rerank_enabled=False)
    stub_cognee_search(w)

    # Spy on httpx.AsyncClient — if anyone constructs one, the test
    # explodes. The rerank path is the only consumer in this module,
    # so an off-default constructing httpx would be a bug.
    import httpx

    def _explode(*a: Any, **kw: Any) -> Any:
        raise AssertionError("rerank disabled must not open an httpx client")

    monkeypatch.setattr(httpx, "AsyncClient", _explode)

    out = await w.search(query="anything", limit=3)
    assert [r["text"] for r in out] == [
        "alpha document",
        "beta document",
        "gamma document",
    ]

    # Audit tail must record reranked=False so dashboard traces can
    # distinguish "vector-only" from "rerank applied".
    search_events = [e for e in w.audit_tail if e["op"] == "search"]
    assert search_events, w.audit_tail
    assert search_events[-1]["reranked"] is False


async def test_rerank_enabled_reorders_by_relevance_score(
    wrapper_factory,
    stub_cognee_search,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rerank_enabled=True flips alpha (idx 0) → gamma (idx 2) when the
    rerank slot scores gamma highest. Asserts the score override too —
    the post-rerank record carries the rerank's relevance_score, not
    Cognee's cosine."""

    captured: dict[str, Any] = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            # Index 2 = gamma is the winner; alpha drops to last.
            return {
                "results": [
                    {"index": 2, "relevance_score": 0.95},
                    {"index": 1, "relevance_score": 0.50},
                    {"index": 0, "relevance_score": 0.10},
                ]
            }

    class _FakeClient:
        def __init__(self, *a: Any, **kw: Any) -> None:
            captured["init_kwargs"] = kw

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any]) -> _Resp:
            captured["url"] = url
            captured["payload"] = json
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    w = wrapper_factory(rerank_enabled=True, rerank_url="http://rr.test:9000")
    stub_cognee_search(w)

    out = await w.search(query="which doc?", limit=3)
    assert [r["text"] for r in out] == [
        "gamma document",
        "beta document",
        "alpha document",
    ]
    # Score on the top hit must be the rerank's relevance_score, not
    # Cognee's cosine (0.7) — that's the whole point of the second pass.
    assert out[0]["score"] == pytest.approx(0.95)

    # The wrapper must hit ``{rerank_url}/rerank`` with the documents
    # in candidate order.
    assert captured["url"] == "http://rr.test:9000/rerank"
    assert captured["payload"]["query"] == "which doc?"
    assert captured["payload"]["documents"] == [
        "alpha document",
        "beta document",
        "gamma document",
    ]

    # Audit tail flags this search as reranked.
    search_events = [e for e in w.audit_tail if e["op"] == "search"]
    assert search_events[-1]["reranked"] is True


async def test_rerank_unreachable_falls_through_to_vector(
    wrapper_factory,
    stub_cognee_search,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the rerank slot is down (ConnectError), search() returns
    vector ordering and DOES NOT raise. Audit tail records reranked=False.
    """
    import httpx

    class _DownClient:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def __aenter__(self) -> _DownClient:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any]) -> Any:
            raise httpx.ConnectError("rerank slot offline")

    monkeypatch.setattr(httpx, "AsyncClient", _DownClient)

    w = wrapper_factory(rerank_enabled=True)
    stub_cognee_search(w)

    out = await w.search(query="anything", limit=3)
    # Vector order preserved exactly.
    assert [r["text"] for r in out] == [
        "alpha document",
        "beta document",
        "gamma document",
    ]
    search_events = [e for e in w.audit_tail if e["op"] == "search"]
    assert search_events[-1]["reranked"] is False


async def test_rerank_malformed_response_falls_through(
    wrapper_factory,
    stub_cognee_search,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the rerank slot returns 200 but with junk, fall through too.

    Defends against a non-llama.cpp deployment behind the same URL
    (e.g. a misconfigured proxy returning HTML).
    """

    class _BadResp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> Any:
            return {"unexpected": "shape"}

    class _BadClient:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def __aenter__(self) -> _BadClient:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def post(self, *a: Any, **kw: Any) -> _BadResp:
            return _BadResp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _BadClient)

    w = wrapper_factory(rerank_enabled=True)
    stub_cognee_search(w)

    out = await w.search(query="anything", limit=3)
    assert [r["text"] for r in out] == [
        "alpha document",
        "beta document",
        "gamma document",
    ]


async def test_set_rerank_enabled_flips_at_runtime(
    wrapper_factory,
    stub_cognee_search,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``set_rerank_enabled`` toggles the gate without rebuilding Cognee."""
    posted: list[str] = []

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 1, "relevance_score": 0.5},
                    {"index": 2, "relevance_score": 0.1},
                ]
            }

    class _FakeClient:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any]) -> _Resp:
            posted.append(url)
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    w = wrapper_factory(rerank_enabled=False)
    stub_cognee_search(w)

    # Off — no HTTP.
    await w.search(query="q", limit=3)
    assert posted == []

    # Flip on — next search hits the slot.
    w.set_rerank_enabled(True)
    await w.search(query="q", limit=3)
    assert len(posted) == 1


# ── Candidate cap + timeout config (reviewer findings, PR #365) ───────────


def test_memory_embedding_config_rerank_tunables_defaults() -> None:
    """New ``rerank_*`` tunables ship with the documented defaults."""
    from hal0.config.schema import MemoryEmbeddingConfig

    cfg = MemoryEmbeddingConfig()
    assert cfg.rerank_over_fetch_factor == 5
    assert cfg.rerank_max_candidates == 500
    assert cfg.rerank_connect_timeout_s == pytest.approx(1.0)
    assert cfg.rerank_read_timeout_s == pytest.approx(8.0)


def test_memory_embedding_config_rerank_tunables_bounds() -> None:
    """Bounds enforced by pydantic ``Field(ge/le)``."""
    from hal0.config.schema import MemoryEmbeddingConfig

    with pytest.raises(ValueError):
        MemoryEmbeddingConfig(rerank_over_fetch_factor=0)
    with pytest.raises(ValueError):
        MemoryEmbeddingConfig(rerank_over_fetch_factor=21)
    with pytest.raises(ValueError):
        MemoryEmbeddingConfig(rerank_max_candidates=5)
    with pytest.raises(ValueError):
        MemoryEmbeddingConfig(rerank_max_candidates=5000)
    with pytest.raises(ValueError):
        MemoryEmbeddingConfig(rerank_connect_timeout_s=0.0)
    with pytest.raises(ValueError):
        MemoryEmbeddingConfig(rerank_read_timeout_s=120.0)


# Shared candidate-cap stub. Replaces cognee.search with N synthetic chunks
# AND seeds matching sidecar rows so the wrapper's text-match path keeps
# all of them. The wrapper's pre-rerank candidate accumulator is the
# observable: we assert how many candidates it kept.


def _seed_n_candidates(monkeypatch: pytest.MonkeyPatch, wrapper: Any, n: int) -> None:
    """Stub cognee + sidecar with ``n`` candidates, one per chunk."""
    import json
    import uuid
    from datetime import UTC, datetime

    import cognee

    texts = [f"doc {i}" for i in range(n)]
    chunks: list[Any] = []
    for i, text in enumerate(texts):
        ch = MagicMock()
        ch.text = text
        # Strictly descending vector score so the unranked order matches
        # insertion order.
        ch.score = 1.0 - (i / max(1, n))
        chunks.append(ch)

    async def fake_search(**kwargs: Any) -> list[Any]:
        return chunks

    monkeypatch.setattr(cognee, "search", fake_search)

    now = datetime.now(UTC).isoformat()
    with wrapper._sidecar_conn() as conn:
        for text in texts:
            conn.execute(
                """
                INSERT INTO hal0_memory_items
                    (id, text, timestamp, dataset, tags, source, metadata,
                     cognee_data_id, cognee_dataset_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    text,
                    now,
                    "shared",
                    json.dumps([]),
                    "test",
                    json.dumps({}),
                    None,
                    None,
                ),
            )
        conn.commit()


async def test_candidate_cap_respects_over_fetch_factor(
    wrapper_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``limit=25`` with default factor=5 → cap=125, well below the 500
    absolute cap, so the rerank pass should receive 125 candidates (or
    all available, whichever is smaller).

    We stage 200 cognee chunks and inspect the document list the rerank
    slot receives — that's the candidate set in candidate order.
    """
    captured: dict[str, Any] = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            # Identity rerank — keep input order.
            return {
                "results": [
                    {"index": i, "relevance_score": 1.0 - (i / 1000)}
                    for i in range(len(captured.get("payload", {}).get("documents", [])))
                ]
            }

    class _FakeClient:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any]) -> _Resp:
            captured["url"] = url
            captured["payload"] = json
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    w = wrapper_factory(rerank_enabled=True)
    _seed_n_candidates(monkeypatch, w, 200)

    await w.search(query="q", limit=25)
    # 25 * 5 = 125, < max_candidates (500), and < seeded 200 → exactly 125.
    assert len(captured["payload"]["documents"]) == 125


async def test_candidate_cap_respects_max_candidates(
    wrapper_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``max_candidates=50`` clamps below ``limit * factor``.

    limit=20, factor=5 → naive cap would be 100; with max=50 the cap is 50.
    """
    captured: dict[str, Any] = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "results": [
                    {"index": i, "relevance_score": 1.0 - (i / 1000)}
                    for i in range(len(captured.get("payload", {}).get("documents", [])))
                ]
            }

    class _FakeClient:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any]) -> _Resp:
            captured["payload"] = json
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    w = wrapper_factory(rerank_enabled=True, rerank_max_candidates=50)
    _seed_n_candidates(monkeypatch, w, 200)

    await w.search(query="q", limit=20)
    assert len(captured["payload"]["documents"]) == 50


async def test_rerank_handles_read_timeout(
    wrapper_factory,
    stub_cognee_search,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``httpx.ReadTimeout`` raised by the rerank client must fall
    through silently — vector ordering preserved, no exception escapes,
    audit tail records reranked=False."""
    import httpx

    class _SlowClient:
        def __init__(self, *a: Any, **kw: Any) -> None:
            # The constructor must accept the split-timeout we now pass.
            assert isinstance(kw.get("timeout"), httpx.Timeout)

        async def __aenter__(self) -> _SlowClient:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any]) -> Any:
            raise httpx.ReadTimeout("rerank slot slow")

    monkeypatch.setattr(httpx, "AsyncClient", _SlowClient)

    w = wrapper_factory(rerank_enabled=True)
    stub_cognee_search(w)

    out = await w.search(query="q", limit=3)
    assert [r["text"] for r in out] == [
        "alpha document",
        "beta document",
        "gamma document",
    ]
    search_events = [e for e in w.audit_tail if e["op"] == "search"]
    assert search_events[-1]["reranked"] is False


async def test_rerank_handles_duplicate_index_in_response(
    wrapper_factory,
    stub_cognee_search,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed rerank slot that returns the same index twice must
    not double-emit the candidate. seen_idx dedup keeps the first hit;
    later duplicates are skipped."""

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            # idx=0 listed twice with different scores. After sort DESC
            # the first entry is the 0.9 one (idx 0). The 0.8 idx=0
            # entry must be skipped.
            return {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.8},
                    {"index": 1, "relevance_score": 0.7},
                ]
            }

    class _FakeClient:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def post(self, *a: Any, **kw: Any) -> _Resp:
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    w = wrapper_factory(rerank_enabled=True)
    stub_cognee_search(w)

    out = await w.search(query="q", limit=3)
    # No duplicates: alpha (idx 0) appears exactly once.
    texts = [r["text"] for r in out]
    assert texts.count("alpha document") == 1
    # The 3-text candidate set should be fully represented (the unranked
    # tail re-appends idx=2 after the dedup'd ranked pass).
    assert set(texts) == {"alpha document", "beta document", "gamma document"}


async def test_rerank_skipped_when_single_candidate(
    wrapper_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``limit=1`` and a single candidate → the rerank pass is skipped
    entirely (``len(candidates) > 1`` is the guard). No HTTP call,
    audit tail records reranked=False."""
    import httpx

    def _explode(*a: Any, **kw: Any) -> Any:
        raise AssertionError("rerank must be skipped for single-candidate searches")

    monkeypatch.setattr(httpx, "AsyncClient", _explode)

    w = wrapper_factory(rerank_enabled=True)
    _seed_n_candidates(monkeypatch, w, 1)

    out = await w.search(query="q", limit=1)
    assert len(out) == 1
    search_events = [e for e in w.audit_tail if e["op"] == "search"]
    assert search_events[-1]["reranked"] is False
