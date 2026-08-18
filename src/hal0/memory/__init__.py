"""hal0 memory subsystem (brain-redesign P0-P2).

Public contract for ``/mcp/memory`` + ``/api/memory/*``. Exposes the
engine-neutral :class:`MemoryProvider` ABC and the ``provider_from_config``
factory that the one construction site in ``api/__init__.py`` calls.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
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

    Rebinding the delegate does NOT recover the writes that were accepted by
    the volatile fallback while it was live (#1897): they are gone, and the
    engine that just came up has never seen them. Callers that issued such
    writes — hal0-api's own boot phases are the ones that always do, because
    they run inside the degrade window by construction — register a replay
    with :meth:`add_heal_hook`; the lifespan's re-probe loop drains them via
    :meth:`run_heal_hooks` right after a successful swap.
    """

    def __init__(self, provider: MemoryProvider, cfg: Any) -> None:
        self._target = provider
        self._cfg = cfg
        self._heal_hooks: list[Callable[[], Any]] = []
        self._healed = False
        self._last_drain_attempted = True

    @property
    def target(self) -> Any:
        """The current delegate (tests introspect which side is live)."""
        return self._target

    @property
    def healed(self) -> bool:
        """True once the durable engine has been swapped in."""
        return self._healed

    def add_heal_hook(self, hook: Callable[[], Any]) -> bool:
        """Register a replay to run once the durable engine is swapped in.

        The hook may be sync or return an awaitable; it runs on the event
        loop in :meth:`run_heal_hooks`. A hook that reports success (any
        return value other than ``False``) is dropped after one run, so a
        replay never re-arms itself.

        Returns ``False`` — and registers nothing — when the provider has
        ALREADY healed, because that heal's hooks were drained before this
        call. The caller owns running its own replay in that case; a hook
        queued after the drain would never fire.
        """
        if self._healed:
            return False
        self._heal_hooks.append(hook)
        return True

    @property
    def pending_heal_hooks(self) -> int:
        """Replays still owed — non-zero after a drain means retry later."""
        return len(self._heal_hooks)

    @property
    def last_drain_attempted(self) -> bool:
        """Whether the previous :meth:`run_heal_hooks` actually ran a hook.

        ``False`` means every armed hook was still waiting on its arming boot
        phase (``hook.ready`` was ``False``), so nothing was tried — the
        caller's retry budget should not be spent on that drain (#1912
        review: a short reprobe interval must not exhaust the cap before the
        boot lane can hand its replay over).
        """
        return self._last_drain_attempted

    async def run_heal_hooks(self) -> bool:
        """Run every hook armed for this heal; True when all of them landed.

        A hook that returns ``False`` (its writes did not land — a healthy
        engine can still fail an individual retain) or raises is KEPT armed
        so the caller's retry loop drains it again on the next tick;
        otherwise a transient failure on the first post-heal attempt would
        leave the data lost until the next restart, which is the very
        failure this machinery exists to prevent. Never propagates: memory
        recovery must not take down the process.

        A hook whose ``ready`` attribute is ``False`` (its arming boot phase
        has not finished handing it work) is kept armed WITHOUT being run:
        it has nothing to try yet, and it must not read as a failed replay
        attempt — see :attr:`last_drain_attempted`.
        """
        hooks, self._heal_hooks = self._heal_hooks, []
        ok = True
        attempted = False
        for hook in hooks:
            if getattr(hook, "ready", True) is False:
                ok = False
                self._heal_hooks.append(hook)
                continue
            attempted = True
            try:
                result = hook()
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                log.warning("hal0.memory.heal_hook_failed", error=str(exc))
                result = False
            if result is False:
                ok = False
                self._heal_hooks.append(hook)
        self._last_drain_attempted = attempted
        return ok

    def try_heal(self) -> bool:
        """One re-probe attempt; True once the durable engine is live.

        Runs the same construction path as boot (probe included, so it is
        bounded by ``HAL0_HINDSIGHT_PROBE_TIMEOUT_S``). Call off-loop —
        the probe is synchronous I/O.
        """
        if not getattr(self._target, "degraded", False):
            self._healed = True
            return True
        try:
            candidate = provider_from_config(self._cfg)
        except Exception as exc:  # pragma: no cover — defensive
            log.debug("hal0.memory.reprobe_failed", error=str(exc))
            return False
        if getattr(candidate, "degraded", False):
            return False
        self._target = candidate
        self._healed = True
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
