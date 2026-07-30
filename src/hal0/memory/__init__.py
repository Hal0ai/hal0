"""hal0 memory subsystem (brain-redesign P0-P2).

Public contract for ``/mcp/memory`` + ``/api/memory/*``. Exposes the
engine-neutral :class:`MemoryProvider` ABC and the ``provider_from_config``
factory that the one construction site in ``api/__init__.py`` calls.
"""

from __future__ import annotations

from typing import Any

import structlog

from hal0.memory.degrade import DegradedMemoryProvider
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
    "DegradedMemoryProvider",
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
    "build_hindsight_provider",
    "provider_from_config",
]


def _build_hindsight_client(cfg: Any) -> Any:
    """Construct the Hindsight REST client from config + env, or raise.

    ``from_env()`` only builds the httpx client — it does NO I/O, so on its
    own it cannot tell a live daemon from a dead one. We therefore probe
    ``/health`` before handing the client back (#1301, closing the P1-6
    TODO): an unreachable daemon raises HERE, at boot, where
    ``provider_from_config``'s degrade ladder can catch it and swap in
    ``PgVectorProvider`` with ``degraded=True``. Without the probe the
    ladder was inert — callers got a live-but-broken HindsightProvider that
    reported healthy and returned empty recalls.

    The probe is timeout-bounded (``HAL0_HINDSIGHT_PROBE_TIMEOUT_S``, default
    2s) so a hung daemon delays boot by that much and no more. This
    indirection also keeps the degrade path unit-testable (tests patch this
    function or ``probe_health``).
    """
    from hal0.memory import hindsight_client as _hc

    client = _hc.HindsightRestClient.from_env()
    _hc.probe_health(base_url=client.base_url, api_key=client.api_key)
    return client


def build_hindsight_provider(cfg: Any) -> HindsightProvider:
    """Build a live HindsightProvider, or raise if the daemon is unreachable.

    Split out of ``provider_from_config`` so the degrade ladder has one
    reusable "try to get the real engine" step: it is called once at boot and
    again by :class:`~hal0.memory.degrade.DegradedMemoryProvider` on every
    re-promotion attempt. Two copies of this wiring would drift, and a
    re-promoted provider configured differently from a booted one is exactly
    the kind of bug nobody finds for months.
    """
    embed = cfg.memory.embedding
    graph = cfg.memory.graph
    client = _build_hindsight_client(cfg)  # probes /health; raises when down
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


def provider_from_config(cfg: Any) -> MemoryProvider:
    """Construct the active MemoryProvider from the loaded hal0 config.

    ADR-0023: Hindsight is the platform engine and the default. The cognee
    wrapper was removed; any non-pgvector engine (including unknown/mem0) resolves
    to Hindsight, with a boot-time degrade to the in-memory PgVectorProvider when
    the Hindsight daemon is unreachable. ``cfg`` is the object returned by
    ``hal0.config.loader.load_hal0_config``.

    Callers can check ``getattr(provider, "degraded", False)`` to detect when the
    returned provider is a volatile fallback rather than a real durable engine.

    The boot degrade is no longer permanent. A daemon that is down at boot —
    the ordinary hal0-api/hindsight-api startup race — used to strand the
    process on volatile storage for its whole lifetime, because nothing ever
    re-read the boot decision. The fallback is now wrapped in
    :class:`~hal0.memory.degrade.DegradedMemoryProvider`, which re-probes on a
    timer, replays anything written while degraded, and promotes itself to
    Hindsight once (and only once). See that module for why automatic
    promotion is safe here and why the ratchet only turns one way.

    ``engine = "pgvector"`` is left alone: that one is a deliberate operator
    choice, not a failure, and promoting away from a configured engine would
    be overriding the config rather than recovering from an outage.
    """
    engine = str(getattr(cfg.memory, "engine", "hindsight") or "hindsight").lower()

    if engine == "pgvector":
        return PgVectorProvider()

    if engine == "mem0":  # documented fallback (spec §2) — not yet implemented
        log.warning("hal0.memory.mem0_not_implemented", fallback="hindsight")
    elif engine not in ("hindsight", ""):
        log.warning("hal0.memory.unknown_engine", engine=engine, fallback="hindsight")

    try:
        return build_hindsight_provider(cfg)
    except Exception as exc:  # daemon down at boot → degrade ladder
        from hal0.memory.degrade import DegradedMemoryProvider, reprobe_interval_s

        interval = reprobe_interval_s()
        log.warning(
            "hal0.memory.hindsight_unavailable",
            error=str(exc),
            fallback="pgvector",
            reprobe_interval_s=interval,
            auto_promote=interval > 0,
            detail=(
                "Memory degraded to volatile in-memory storage at boot. hal0 "
                "will re-probe Hindsight and promote itself once the daemon "
                "answers, replaying anything written in the meantime."
            ),
        )
        return DegradedMemoryProvider(promote=lambda: build_hindsight_provider(cfg))
