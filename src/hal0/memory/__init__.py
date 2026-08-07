"""hal0 memory subsystem (brain-redesign P0-P2).

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
    "SelfHealingMemoryProvider",
    "_build_hindsight_client",
    "provider_from_config",
]


class SelfHealingMemoryProvider:
    """Delegating shell around a boot-degraded memory provider (#1613).

    ``provider_from_config`` hands back the in-memory ``PgVectorProvider``
    when the Hindsight daemon loses the boot race (a cold engine start —
    embedded postgres init + model load — outlasts the 2s boot probe).
    Every consumer captures the provider object at ``create_app`` time: the
    REST routes read it off ``app.state``, but both MCP mounts and the
    in-process dispatcher close over it. That made the fallback PERMANENT
    until an operator restarted hal0-api — which is also the default end
    state of a fresh install (#1543), because the installer starts both
    units in the same pass.

    ``create_app`` wraps a *degraded* boot result in this shell (a healthy
    boot keeps today's exact object graph — no shell), and the lifespan
    polls :meth:`try_heal` until the durable engine answers. The swap is a
    single attribute rebind, so every captured reference — closures
    included — recovers at once.
    """

    def __init__(self, provider: MemoryProvider, cfg: Any) -> None:
        self._target = provider
        self._cfg = cfg

    @property
    def target(self) -> Any:
        """The current delegate (tests introspect which side is live)."""
        return self._target

    def try_heal(self) -> bool:
        """One re-probe attempt; True once the durable engine is live.

        Runs the same construction path as boot (probe included, so it is
        bounded by ``HAL0_HINDSIGHT_PROBE_TIMEOUT_S``). Call off-loop —
        the probe is synchronous I/O.
        """
        if not getattr(self._target, "degraded", False):
            return True
        try:
            candidate = provider_from_config(self._cfg)
        except Exception as exc:  # pragma: no cover — defensive
            log.debug("hal0.memory.reprobe_failed", error=str(exc))
            return False
        if getattr(candidate, "degraded", False):
            return False
        self._target = candidate
        log.warning(
            "hal0.memory.provider_healed",
            detail="hindsight engine recovered — swapped out the degraded pgvector fallback",
        )
        return True

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_target"), name)


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
