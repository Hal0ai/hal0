"""ABC shape tests — the explicit MemoryProvider contract (P0)."""

from __future__ import annotations

import inspect

from hal0.memory.provider import (
    AddResult,  # noqa: F401 — import-smoke: verify public surface exists
    DeleteResult,  # noqa: F401
    GraphStatus,  # noqa: F401
    ListPage,  # noqa: F401
    MemoryItem,  # noqa: F401
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


def test_add_signature_matches_cognee_call_sites():
    sig = inspect.signature(MemoryProvider.add)
    params = list(sig.parameters)
    # Mirrors CogneeWrapper.add + the REST/MCP callers.
    assert params == [
        "self",
        "text",
        "dataset",
        "tags",
        "source",
        "metadata",
        "client_id",
        "document_id",
    ]


def test_cognee_wrapper_is_a_memory_provider():
    from hal0.memory.cognee_wrapper import CogneeWrapper
    from hal0.memory.provider import MemoryProvider

    assert issubclass(CogneeWrapper, MemoryProvider)
