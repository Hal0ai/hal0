"""HindsightProvider — the platform memory engine (brain-redesign P1).

Maps hal0's engine-neutral MemoryProvider contract onto the shared
``hindsight-api`` over REST. Key design points (spec §3, §4b, P1):

* **Bank mapping** lives HERE (not namespace.py, which is unchanged): hal0
  namespace ``private:<agent>`` → Hindsight bank ``private__<agent>`` (``:``:
  ``__``); ``project:<id>`` → ``project__<id>``; ``shared``/``agents`` pass
  through.
* ``MemoryItem.id`` is the Hindsight **document_id** — idempotent on retain,
  recall-visible, delete-addressable. NOT a per-fact id (those are async +
  many-per-add).
* ``add`` routes to Hindsight **retain** so background consolidation fires.
* **Multi-bank recall fan-out** (Task P1-3): Hindsight recall is per-bank,
  client-orchestrated; we fan out to the caller's allowed banks and merge
  under one reranked token budget (recall returns NO numeric score, so the
  union is re-ranked via the :8086 reranker; §4b precedence is the tiebreak).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from hal0.memory.provider import MemoryProvider

_SHARED = "shared"
_PRIVATE = "private:"


def namespace_to_bank(namespace: str) -> str:
    """Map a hal0 namespace to a Hindsight bank id (spec §3 table)."""
    return namespace.replace(":", "__")


class Hal0Reranker:
    """Async reranker over hal0-api's OpenAI surface (Cohere-style ``/v1/rerankings``).

    POSTs {model, query, documents} to ``{base_url}/v1/rerankings`` (served by
    the rerank container slot via the dispatcher) and returns the raw
    ``results`` list (``[{"index", "relevance_score"}, ...]``, NOT pre-sorted)
    that HindsightProvider._rerank_union maps onto the cross-bank union.
    Fail-soft: returns [] on any error (gateway down, model load fail, bad
    shape, timeout) so recall falls back to fused order.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8080",
        model: str = "builtin.jina-reranker-v1-tiny-en-q8",
        connect_timeout_s: float = 1.0,
        read_timeout_s: float = 8.0,
    ) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        self._model = model
        self._connect_timeout_s = float(connect_timeout_s)
        self._read_timeout_s = float(read_timeout_s)

    async def rerank(self, query: str, documents: list[str]) -> list[dict[str, Any]]:
        if not self._base_url or not documents:
            return []
        import httpx

        payload = {"model": self._model, "query": query, "documents": list(documents)}
        timeout = httpx.Timeout(
            connect=self._connect_timeout_s, read=self._read_timeout_s, write=2.0, pool=None
        )
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{self._base_url}/v1/rerankings", json=payload)
                resp.raise_for_status()
                body = resp.json()
        except Exception:
            return []
        results = body.get("results") if isinstance(body, dict) else None
        return results if isinstance(results, list) else []


def _now() -> str:
    return datetime.now(UTC).isoformat()


class HindsightProvider(MemoryProvider):
    def __init__(
        self,
        *,
        client: Any,
        client_id: str = "anonymous",
        reranker: Any = None,
    ) -> None:
        self._client = client
        self._client_id = client_id
        self._reranker = reranker
        self._graph_enabled = False
        self._graph_route = "upstream"
        self._rerank_enabled = reranker is not None

    # ── ACL: the caller's allowed namespaces → banks ───────────────────

    def _allowed_namespaces(self, requested: str | list[str], client_id: str | None) -> list[str]:
        cid = client_id or self._client_id
        own = f"{_PRIVATE}{cid}"
        reqs = [requested] if isinstance(requested, str) else list(requested or [_SHARED])
        out: list[str] = []
        for ds in reqs:
            if ds == _SHARED:
                out += [d for d in (_SHARED, own) if d not in out]
            elif ds == own and own not in out:
                out.append(own)
            elif ds.startswith(_PRIVATE):
                continue  # foreign private — dropped (fail-open-empty)
            elif ds not in out:
                out.append(ds)
        return out

    def _write_namespace(self, requested: str, client_id: str | None) -> str:
        # The REST/MCP front door already resolved the write namespace via
        # namespace.resolve_write_dataset; trust it verbatim here.
        return requested or _SHARED

    # ── Core five ──────────────────────────────────────────────────────

    async def add(
        self,
        text: str,
        dataset: str = _SHARED,
        tags: list[str] | None = None,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
        client_id: str | None = None,
    ) -> dict[str, str]:
        ns = self._write_namespace(dataset, client_id)
        bank = namespace_to_bank(ns)
        document_id = str(uuid.uuid4())  # the join key
        meta = dict(metadata or {})
        if source:
            meta["source"] = source
        await self._client.retain(
            bank_id=bank,
            content=text,
            document_id=document_id,
            context=meta.get("source"),
            metadata={k: str(v) for k, v in meta.items()},
            tags=list(tags or []),
            timestamp=None,
        )
        return {"id": document_id, "timestamp": _now()}

    async def search(
        self,
        query: str,
        limit: int = 10,
        dataset: str | list[str] = _SHARED,
        tags: list[str] | None = None,
        before: str | None = None,
        after: str | None = None,
        mode: str = "vector",
        client_id: str | None = None,
    ) -> list[dict[str, Any]]:
        # search delegates to recall (back-compat surface); the fan-out lives
        # in recall (Task P1-3). limit is honored after the merge.
        out = await self.recall(
            query=query,
            max_tokens=max(256, limit * 256),
            dataset=dataset,
            tags=tags,
            client_id=client_id,
        )
        return out[:limit]

    async def list_items(
        self,
        dataset: str = _SHARED,
        cursor: str | None = None,
        limit: int = 50,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        banks = [namespace_to_bank(ns) for ns in self._allowed_namespaces(dataset, client_id)]
        items: list[dict[str, Any]] = []
        for bank in banks:
            if len(items) >= limit:
                break
            try:
                resp = await self._client.list_memories(bank_id=bank, limit=limit, offset=0)
            except Exception:
                continue  # fail-soft per bank
            for fact in resp.get("items", []):
                items.append(self._list_fact_to_item(fact, bank))
        return {"items": items[:limit], "next_cursor": None}

    @staticmethod
    def _list_fact_to_item(fact: dict[str, Any], bank: str) -> dict[str, Any]:
        """Map a Hindsight /memories/list item to the MemoryItem wire shape."""
        return {
            "id": fact.get("id") or fact.get("document_id"),
            "text": fact.get("text", ""),
            "timestamp": fact.get("mentioned_at") or fact.get("date") or _now(),
            "dataset": bank.replace("__", ":"),
            "tags": list(fact.get("tags") or []),
            "source": None,
            "metadata": {},
            "score": None,
            "type": fact.get("fact_type"),
        }

    async def delete(self, ids: list[str], *, client_id: str | None = None) -> dict[str, int]:
        deleted = 0
        # We don't know which bank each document_id lives in without a lookup;
        # try the caller's allowed banks. delete_document is idempotent.
        banks = [namespace_to_bank(ns) for ns in self._allowed_namespaces(_SHARED, client_id)]
        for document_id in ids:
            for bank in banks:
                res = await self._client.delete_document(bank_id=bank, document_id=document_id)
                if int(res.get("memory_units_deleted", 0)) > 0:
                    deleted += 1
                    break
        return {"deleted": deleted}

    # ── recall (fan-out added in Task P1-3) ────────────────────────────

    async def recall(
        self,
        query: str,
        *,
        types: list[str] | None = None,
        max_tokens: int = 4096,
        dataset: str | list[str] = _SHARED,
        tags: list[str] | None = None,
        client_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fan out per-bank recall to the caller's allowed banks, merge under
        one token budget. Hindsight has no server-side cross-bank query and
        returns no numeric score, so we re-rank the union via the :8086
        reranker, with the §4b precedence ladder as the tiebreak.
        """
        import asyncio

        banks = [namespace_to_bank(ns) for ns in self._allowed_namespaces(dataset, client_id)]
        if not banks:
            return []

        async def _one(bank: str) -> list[dict[str, Any]]:
            resp = await self._client.recall(
                bank_id=bank, query=query, types=types, max_tokens=max_tokens, tags=tags
            )
            return [self._fact_to_item(f, bank) for f in resp.get("results", [])]

        per_bank = await asyncio.gather(*[_one(b) for b in banks])
        union: list[dict[str, Any]] = [item for bank_items in per_bank for item in bank_items]
        if not union:
            return []

        union = await self._rerank_union(query, union)
        union.sort(key=self._precedence_key)  # stable: precedence wins ties
        return self._apply_token_budget(union, max_tokens)

    @staticmethod
    def _precedence_key(item: dict[str, Any]) -> tuple[int, float]:
        """§4b ladder: shared/curated observations rank above raw private
        facts. Lower tuple sorts first. Second element is negative rerank
        score so higher score sorts earlier within the same tier.
        """
        is_observation = item.get("type") == "observation"
        is_shared = item.get("dataset") == _SHARED
        tier = 0 if (is_observation or is_shared) else 1
        return (tier, -float(item.get("score") or 0.0))

    async def _rerank_union(self, query: str, union: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._reranker is None or not self._rerank_enabled or len(union) < 2:
            return union
        try:
            ranked = await self._reranker.rerank(query, [u["text"] for u in union])
        except Exception:
            return union  # reranker down → keep fused order (fail-soft)
        for entry in ranked:
            idx = entry.get("index")
            if isinstance(idx, int) and 0 <= idx < len(union):
                union[idx]["score"] = float(entry.get("relevance_score", 0.0))
        return union

    @staticmethod
    def _apply_token_budget(items: list[dict[str, Any]], max_tokens: int) -> list[dict[str, Any]]:
        """Greedy fill by ~4 chars/token on the text field (Hindsight counts
        only fact text toward the budget)."""
        out: list[dict[str, Any]] = []
        spent = 0
        for item in items:
            cost = max(1, len(item.get("text", "")) // 4)
            if spent + cost > max_tokens and out:
                break
            out.append(item)
            spent += cost
        return out

    # ── Runtime toggles ────────────────────────────────────────────────

    def graph_status(self) -> dict[str, Any]:
        return {
            "enabled": self._graph_enabled,
            "route": self._graph_route,
            "in_flight": 0,
            "builds_ok": 0,
            "errors": 0,
            "last_built_at": None,
            "last_error": None,
        }

    def set_graph_enabled(self, enabled: bool, route: str | None = None) -> None:
        self._graph_enabled = bool(enabled)
        if route is not None:
            self._graph_route = route

    def set_rerank_enabled(self, enabled: bool) -> None:
        self._rerank_enabled = bool(enabled)

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _fact_to_item(fact: dict[str, Any], bank: str) -> dict[str, Any]:
        """Map a Hindsight RecallResult to the MemoryItem wire shape.

        ``score`` is always None — Hindsight recall returns no numeric score;
        ordering carries the relevance signal.
        """
        return {
            "id": fact.get("document_id") or fact.get("id"),
            "text": fact.get("text", ""),
            "timestamp": fact.get("mentioned_at") or _now(),
            "dataset": bank.replace("__", ":"),
            "tags": list(fact.get("tags") or []),
            "source": (fact.get("metadata") or {}).get("source"),
            "metadata": dict(fact.get("metadata") or {}),
            "score": None,
            "type": fact.get("type"),
        }
