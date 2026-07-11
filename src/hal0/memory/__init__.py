"""hal0 memory subsystem (ADR-0005 + brain-redesign P0-P2).

Public contract for ``/mcp/memory`` + ``/api/memory/*``. Exposes the
engine-neutral :class:`MemoryProvider` ABC and the ``provider_from_config``
factory that the one construction site in ``api/__init__.py`` calls.
"""

from __future__ import annotations

from typing import Any

import structlog

from hal0.memory.hindsight_provider import HindsightProvider
from hal0.memory.pgvector_provider import PgVectorProvider
from hal0.memory.provider import (
    AddResult,
    DeleteResult,
    GraphStatus,
    ListPage,
    MemoryItem,
    MemoryProvider,
    MemoryRecord,
    Mode,
)

log = structlog.get_logger(__name__)

__all__ = [
    "AddResult",
    "DeleteResult",
    "GraphStatus",
    "HindsightProvider",
    "ListPage",
    "MemoryItem",
    "MemoryProvider",
    "MemoryRecord",
    "Mode",
    "PgVectorProvider",
    "_build_hindsight_client",
    "provider_from_config",
]


def _build_hindsight_client(cfg: Any) -> Any:
    """Construct the Hindsight REST client from config + env.

    NOTE (P1): ``from_env()`` only builds the httpx client — it does NO I/O,
    so it does not yet raise when the daemon is down. The boot-degrade ladder
    (hindsight -> pgvector) only fires once P1-6 adds a connectivity probe
    here (e.g. a ``/health`` call) so an unreachable daemon raises at boot
    rather than at first recall. This indirection exists so that degrade path
    is unit-testable (tests patch this function).
    """
    from hal0.memory.hindsight_client import HindsightRestClient

    return HindsightRestClient.from_env()


def provider_from_config(cfg: Any) -> MemoryProvider:
    """Construct the active MemoryProvider from the loaded hal0 config.

    ADR-0023: Hindsight is the platform engine and the default. The cognee
    wrapper was removed; any non-pgvector engine (including unknown/mem0) resolves
    to Hindsight, with a boot-time degrade to the in-memory PgVectorProvider when
    the Hindsight daemon is unreachable. ``cfg`` is the object returned by
    ``hal0.config.loader.load_hal0_config``.

    Callers can check ``getattr(provider, "degraded", False)`` to detect when the
    returned provider is the in-memory PgVectorProvider fallback rather than a real
    durable engine. ``PgVectorProvider.degraded`` is always ``True``; all other
    providers default to ``False`` (absent attribute → ``getattr`` fallback).
    """
    engine = str(getattr(cfg.memory, "engine", "hindsight") or "hindsight").lower()
    embed = cfg.memory.embedding
    graph = cfg.memory.graph

    if engine == "pgvector":
        return PgVectorProvider()

    if engine == "mem0":  # documented fallback (spec §2) — not yet implemented
        log.warning("hal0.memory.mem0_not_implemented", fallback="hindsight")
    elif engine not in ("hindsight", ""):
        log.warning("hal0.memory.unknown_engine", engine=engine, fallback="hindsight")

    try:
        client = _build_hindsight_client(cfg)
    except Exception as exc:  # daemon down at boot → degrade ladder
        log.warning("hal0.memory.hindsight_unavailable", error=str(exc), fallback="pgvector")
        return PgVectorProvider()
    from hal0.memory.hindsight_provider import Hal0Reranker

    reranker = Hal0Reranker(
        base_url=str(embed.rerank_gateway_url),
        model=str(embed.rerank_model),
        connect_timeout_s=float(embed.rerank_connect_timeout_s),
        read_timeout_s=float(embed.rerank_read_timeout_s),
    )
    return HindsightProvider(
        client=client,
        reranker=reranker,
        graph_enabled=bool(graph.enabled),
        extraction_slot=str(getattr(graph, "extraction_slot", "utility")),
        unified_bank=bool(getattr(cfg.memory, "unified_bank", True)),
    )
