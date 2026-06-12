"""The explicit MemoryProvider contract (brain-redesign P0).

Promotes the implicit five-method ``CogneeWrapper`` surface into an ABC so
hal0 can swap engines (Cognee → Hindsight, fallback Mem0/PgVector) without
touching a single call site. The ABC is the anti-lock-in seam (spec §1).

The *core five* (``add/search/list_items/delete`` + the three runtime
toggles ``graph_status/set_graph_enabled/set_rerank_enabled``) are abstract:
every engine must implement them, byte-compatible in **signature + return
shape** with ``CogneeWrapper`` so the REST shims + MCP dispatcher need no
changes. The *optional* methods (``recall/reflect/consolidate/
register_compiled``) ship concrete safe defaults so an engine without
consolidation still satisfies the contract.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class Mode(enum.StrEnum):
    """Search mode — mirrors CogneeWrapper's accepted ``mode`` values."""

    VECTOR = "vector"
    GRAPH = "graph"
    HYBRID = "hybrid"


@dataclass
class MemoryItem:
    """One stored memory. ``id`` is the engine's join key.

    For Hindsight this is the ``document_id`` (idempotent, recall-visible,
    delete-addressable) — NOT a per-fact id. For Cognee it is the sidecar
    uuid. The wire shape matches ``CogneeWrapper.MemoryRecord``.
    """

    id: str
    text: str
    timestamp: str  # ISO-8601 UTC
    dataset: str
    tags: list[str] = field(default_factory=list)
    source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "timestamp": self.timestamp,
            "dataset": self.dataset,
            "tags": list(self.tags),
            "source": self.source,
            "metadata": dict(self.metadata),
            "score": self.score,
        }


@dataclass
class AddResult:
    """Return shape of ``add`` — matches ``CogneeWrapper.add``."""

    id: str
    timestamp: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "timestamp": self.timestamp}


@dataclass
class ListPage:
    """Return shape of ``list_items`` — matches ``CogneeWrapper.list_items``."""

    items: list[dict[str, Any]]
    next_cursor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"items": list(self.items), "next_cursor": self.next_cursor}


@dataclass
class DeleteResult:
    """Return shape of ``delete`` — matches ``CogneeWrapper.delete``."""

    deleted: int

    def to_dict(self) -> dict[str, int]:
        return {"deleted": self.deleted}


@dataclass
class GraphStatus:
    """Return shape of ``graph_status`` — matches the CogneeWrapper payload."""

    enabled: bool
    route: str
    in_flight: int = 0
    builds_ok: int = 0
    errors: int = 0
    last_built_at: str | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "route": self.route,
            "in_flight": self.in_flight,
            "builds_ok": self.builds_ok,
            "errors": self.errors,
            "last_built_at": self.last_built_at,
            "last_error": self.last_error,
        }


class MemoryProvider(ABC):
    """Engine-neutral memory contract. See module docstring."""

    # ── Core five (abstract) ───────────────────────────────────────────

    @abstractmethod
    async def add(
        self,
        text: str,
        dataset: str = "shared",
        tags: list[str] | None = None,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
        client_id: str | None = None,
        document_id: str | None = None,
    ) -> dict[str, str]:
        """Add a memory item. Returns ``{id, timestamp}`` plus
        ``operation_id`` when the engine processes asynchronously (poll
        it via the engine-admin operations surface).

        ``document_id`` lets a caller pin the engine's grouping key —
        re-using one id across adds upserts the same logical document
        (conversation evolution). ``None`` → a fresh id per call.
        Engines without document semantics ignore it."""

    @abstractmethod
    async def search(
        self,
        query: str,
        limit: int = 10,
        dataset: str | list[str] = "shared",
        tags: list[str] | None = None,
        before: str | None = None,
        after: str | None = None,
        mode: str = "vector",
        client_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Vector + filter search. Returns a list of MemoryItem dicts."""

    @abstractmethod
    async def list_items(
        self,
        dataset: str = "shared",
        cursor: str | None = None,
        limit: int = 50,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        """Paginated list. Returns ``{items, next_cursor}``."""

    @abstractmethod
    async def delete(
        self,
        ids: list[str],
        *,
        client_id: str | None = None,
        dataset: str | list[str] | None = None,
    ) -> dict[str, int]:
        """Delete by id. Returns ``{deleted: int}``.

        ``dataset`` optionally narrows (or widens, e.g. ``project:<id>``)
        the namespaces swept for each id; ``None`` keeps the default
        sweep of ``shared`` + the caller's own private namespace."""

    @abstractmethod
    def graph_status(self) -> dict[str, Any]:
        """Return the graph-extraction status payload (GraphStatus shape)."""

    @abstractmethod
    def set_graph_enabled(self, enabled: bool, route: str | None = None) -> None:
        """Flip the graph-extraction gate at runtime."""

    @abstractmethod
    def set_rerank_enabled(self, enabled: bool) -> None:
        """Flip the rerank gate at runtime."""

    # ── Optional capability methods (concrete safe defaults) ───────────

    async def recall(
        self,
        query: str,
        *,
        types: list[str] | None = None,
        max_tokens: int = 4096,
        dataset: str | list[str] = "shared",
        tags: list[str] | None = None,
        client_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Token-budgeted recall. Default delegates to ``search`` so an
        engine without a richer recall surface still answers the route."""
        return await self.search(
            query=query,
            limit=max(1, max_tokens // 256),
            dataset=dataset,
            tags=tags,
            client_id=client_id,
        )

    async def reflect(
        self, *, dataset: str = "shared", client_id: str | None = None
    ) -> dict[str, Any]:
        """Trigger consolidation/reflection. No-op default."""
        return {"status": "unsupported"}

    async def consolidate(self, *, dataset: str = "shared") -> dict[str, Any]:
        """Trigger background consolidation. No-op default."""
        return {"status": "unsupported"}

    def register_compiled(self, *args: Any, **kwargs: Any) -> None:
        """Register a compiled artifact (directive/mental model). No-op default."""
        return None
