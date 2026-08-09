"""The explicit MemoryProvider contract (brain-redesign P0).

Promotes the implicit five-method memory surface into an ABC so hal0 can
swap engines (Hindsight, with Mem0/PgVector fallbacks) without touching a
single call site. The ABC is the anti-lock-in seam (spec §1).

The *core five* (``add/search/list_items/delete`` + the three runtime
toggles ``graph_status/set_graph_enabled/set_rerank_enabled``) are abstract:
every engine must implement them, byte-compatible in **signature + return
shape** with ``MemoryProvider`` so the REST shims + MCP dispatcher need no
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
    """Search mode — mirrors the ``MemoryProvider`` accepted ``mode`` values."""

    VECTOR = "vector"
    GRAPH = "graph"
    HYBRID = "hybrid"


@dataclass
class MemoryItem:
    """One stored memory. ``id`` is the engine's join key.

    For Hindsight this is the ``document_id`` (idempotent, recall-visible,
    delete-addressable) — NOT a per-fact id.
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


# Back-compat alias: ``MemoryRecord`` was the original name for this wire
# shape. Kept as an alias so existing importers keep working after the legacy
# memory wrapper was removed (ADR-0023).
MemoryRecord = MemoryItem


@dataclass
class AddResult:
    """Return shape of ``add`` — matches ``MemoryProvider.add``."""

    id: str
    timestamp: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "timestamp": self.timestamp}


@dataclass
class ListPage:
    """Return shape of ``list_items`` — matches ``MemoryProvider.list_items``."""

    items: list[dict[str, Any]]
    next_cursor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"items": list(self.items), "next_cursor": self.next_cursor}


@dataclass
class DeleteResult:
    """Return shape of ``delete`` — matches ``MemoryProvider.delete``."""

    deleted: int

    def to_dict(self) -> dict[str, int]:
        return {"deleted": self.deleted}


@dataclass
class GraphStatus:
    """Return shape of ``graph_status``.

    ADR-0023: ``extraction_slot`` is the local llm slot the engine uses for graph
    extraction. ``to_dict`` also emits a deprecated ``route`` mirror so the existing
    dashboard (separate repo) keeps rendering during the cutover.
    """

    enabled: bool
    extraction_slot: str
    in_flight: int = 0
    builds_ok: int = 0
    errors: int = 0
    last_built_at: str | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "extraction_slot": self.extraction_slot,
            "route": self.extraction_slot,  # deprecated mirror (ADR-0023)
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
        entities: list[dict[str, Any]] | None = None,
        observation_scopes: Any = None,
        strategy: str | None = None,
        update_mode: str | None = None,
        sync: bool = False,
    ) -> dict[str, Any]:
        """Add a memory item. Returns ``{id, timestamp}`` plus
        ``operation_id``/``operation_ids``/``items_count`` when the engine
        reports them (poll async operations via the engine-admin operations
        surface).

        ``document_id`` lets a caller pin the engine's grouping key —
        re-using one id across adds upserts the same logical document
        (conversation evolution). ``None`` → a fresh id per call.
        Engines without document semantics ignore it.

        The rest mirror Hindsight's ``RetainRequest`` item shape (engines
        without the concept ignore them):

        ``entities``
            Extra entities to combine with auto-extracted ones —
            ``[{"text": ..., "type": ...}, ...]``.
        ``observation_scopes``
            How to scope observations during consolidation
            (``"per_tag"|"combined"|"all_combinations"|"shared"`` or a list
            of tag-lists).
        ``strategy``
            Named retain strategy overriding the bank default for this item.
        ``update_mode``
            ``"replace"`` (default) or ``"append"`` when ``document_id``
            names an existing document.
        ``sync``
            ``True`` waits for extraction to complete before returning
            (no ``operation_id``); ``False`` (default) ingests in the
            background."""

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
        tag_groups: list[dict[str, Any]] | None = None,
        min_scores: dict[str, float] | None = None,
    ) -> list[dict[str, Any]]:
        """Vector + filter search. Returns a list of MemoryItem dicts.

        ``tag_groups``/``min_scores`` are optional engine-native recall
        filters (boolean tag expressions / per-stage score floors); an
        engine without them ignores both."""

    @abstractmethod
    async def list_items(
        self,
        dataset: str | list[str] = "shared",
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
    def set_graph_enabled(self, enabled: bool, extraction_slot: str | None = None) -> None:
        """Flip the graph-extraction gate at runtime (ADR-0023).

        ``extraction_slot`` updates the reported slot; ``None`` leaves it unchanged.
        """

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
        tags_match: str | None = None,
        tag_groups: list[dict[str, Any]] | None = None,
        budget: str = "mid",
        prefer_observations: bool = False,
        include: dict[str, Any] | None = None,
        query_timestamp: str | None = None,
        min_scores: dict[str, float] | None = None,
        client_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Token-budgeted recall. Default delegates to ``search`` so an
        engine without a richer recall surface still answers the route.

        ``tags_match``/``tag_groups``/``budget``/``prefer_observations``/
        ``include``/``query_timestamp``/``min_scores`` are Hindsight-specific
        knobs; the search-delegating default ignores every one of them —
        a plain result list is still returned rather than erroring."""
        return await self.search(
            query=query,
            limit=max(1, max_tokens // 256),
            dataset=dataset,
            tags=tags,
            client_id=client_id,
        )

    async def reflect(
        self,
        query: str,
        *,
        dataset: str = "shared",
        client_id: str | None = None,
        budget: str = "low",
        max_tokens: int = 4096,
        fact_types: list[str] | None = None,
        tags: list[str] | None = None,
        tags_match: str | None = None,
        tag_groups: list[dict[str, Any]] | None = None,
        exclude_mental_models: bool = False,
        exclude_mental_model_ids: list[str] | None = None,
        include: dict[str, Any] | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Trigger LLM-backed reflection/synthesis over memory. No-op default
        (``{"status": "unsupported"}``) for engines without the capability."""
        return {"status": "unsupported"}

    async def consolidate(
        self, *, dataset: str = "shared", client_id: str | None = None
    ) -> dict[str, Any]:
        """Trigger background consolidation. No-op default."""
        return {"status": "unsupported"}

    def register_compiled(self, *args: Any, **kwargs: Any) -> None:
        """Register a compiled artifact (directive/mental model). No-op default."""
        return None
