"""ABC shape tests — the explicit MemoryProvider contract (P0)."""

from __future__ import annotations

import inspect

from hal0.memory.provider import (
    AddResult,  # noqa: F401 — import-smoke: verify public surface exists
    DeleteResult,  # noqa: F401
    GraphStatus,  # noqa: F401
    ListPage,  # noqa: F401
    MemoryItem,  # used in test_memory_record_is_alias_of_memory_item
    MemoryProvider,
    Mode,  # noqa: F401
)


def test_abc_declares_core_five_plus_status():
    # The methods every engine MUST implement (the call sites in
    # routes/memory.py + mcp/memory.py depend on these exact names).
    required = {
        "add",
        "search",
        "list_items",
        "delete",
        "graph_status",
        "set_graph_enabled",
        "set_rerank_enabled",
    }
    assert required <= set(MemoryProvider.__abstractmethods__)


def test_abc_optional_methods_have_safe_defaults():
    # Optional capability methods are concrete (NOT abstract) so an engine
    # that lacks consolidation still satisfies the ABC.
    optional = {"recall", "reflect", "consolidate", "register_compiled"}
    assert not (optional & set(MemoryProvider.__abstractmethods__))
    for name in optional:
        assert callable(getattr(MemoryProvider, name))


def test_add_signature_matches_call_sites():
    sig = inspect.signature(MemoryProvider.add)
    params = list(sig.parameters)
    # Mirrors the REST/MCP callers (routes/memory.py + mcp/memory.py).
    # entities/observation_scopes/strategy/update_mode/sync mirror
    # Hindsight's RetainRequest item shape (retain modernization) — additive,
    # keyword-only-by-convention params after document_id.
    assert params == [
        "self",
        "text",
        "dataset",
        "tags",
        "source",
        "metadata",
        "client_id",
        "document_id",
        "entities",
        "observation_scopes",
        "strategy",
        "update_mode",
        "sync",
    ]


def test_memory_record_is_alias_of_memory_item():
    # ADR-0023: the cognee-era ``MemoryRecord`` name survives as an alias of
    # ``MemoryItem`` so existing importers keep working after the wrapper removal.
    from hal0.memory import MemoryRecord as MemoryRecordFromPkg
    from hal0.memory.provider import MemoryRecord

    assert MemoryRecord is MemoryItem
    assert MemoryRecordFromPkg is MemoryItem


def test_concrete_providers_are_memory_providers():
    # ADR-0023: the cognee wrapper is gone; the shipped engines are Hindsight
    # (primary) + PgVector (boot-degrade fallback). Both must satisfy the ABC.
    from hal0.memory.hindsight_provider import HindsightProvider
    from hal0.memory.pgvector_provider import PgVectorProvider
    from hal0.memory.provider import MemoryProvider

    assert issubclass(HindsightProvider, MemoryProvider)
    assert issubclass(PgVectorProvider, MemoryProvider)
