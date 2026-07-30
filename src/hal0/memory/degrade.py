"""The boot degrade ladder's missing rung: getting back UP it.

``provider_from_config`` probes Hindsight at boot and, when the daemon is
unreachable, falls back to the in-memory :class:`PgVectorProvider` so hal0
starts instead of crashing. That half works. The half that did not exist is
the return trip.

The failure it leaves behind is ordinary and permanent. A box reboots.
``hal0-api`` and ``hindsight-api`` race; hal0-api wins, probes a daemon that
is still coming up, degrades, and constructs a provider whose ``degraded`` is
a hard-coded ``True``. Hindsight finishes starting ten seconds later. Nothing
re-reads that decision, so the box runs on VOLATILE storage — every memory
lost on the next restart — for as long as the process lives, with a stuck
warning as its only signal. ``HindsightProvider.degraded`` became a live
property (#1301) so an outage *after* boot self-heals; a degrade *at* boot
still did not, which is the one case where nothing recovers on its own.

This module closes it with a self-promoting wrapper. It is deliberately a
ONE-WAY ratchet.

Why automatic promotion is safe here
------------------------------------
The two things that make an automatic failback dangerous are data loss and
flapping. Both are addressed structurally rather than by tuning:

*Data.* Writes accepted while degraded live in the fallback's in-memory rows.
Promoting without them would split the corpus: the data would still exist but
nothing would ever read it again — a silent partial loss, which is worse than
a loud outage. So promotion is gated on a successful DRAIN of those rows into
Hindsight, and it is the drain, not the probe, that decides. The drain is
idempotent twice over: each row is replayed under its original id as
``document_id`` (so Hindsight upserts rather than duplicates on a retry), and
successfully drained ids are remembered so a retry skips them. Rows are NOT
removed from the fallback until the whole drain succeeds, so a drain that
dies halfway leaves every read complete — the corpus is never split, only
temporarily duplicated in a place nothing is reading yet.

*Flapping.* There is exactly one transition, ever: degraded → promoted. The
wrapper never demotes. A daemon that dies after promotion is already handled
correctly one layer down by ``HindsightProvider.degraded``, which is live and
edge-triggered. Because the ratchet only turns once, no oscillation is
reachable no matter how the daemon behaves, and the probe cost is bounded to
one attempt per ``HAL0_MEMORY_REPROBE_INTERVAL_S`` regardless of call volume.

The probe is driven by the calls the process is already making rather than by
a background poller — same reasoning as ``HindsightProvider._call``: no task
to supervise, no lifecycle to get wrong on shutdown, and an idle process does
not need a memory engine anyway.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import structlog

from hal0.memory.pgvector_provider import PgVectorProvider
from hal0.memory.provider import MemoryProvider

log = structlog.get_logger(__name__)

#: How long to wait between re-probes of a daemon that was down at boot.
#: ``<= 0`` disables automatic promotion entirely (the operator escape hatch;
#: ``promote_now()`` still works).
_REPROBE_ENV = "HAL0_MEMORY_REPROBE_INTERVAL_S"
_DEFAULT_REPROBE_INTERVAL_S = 30.0


def reprobe_interval_s() -> float:
    raw = os.environ.get(_REPROBE_ENV, "").strip()
    if not raw:
        return _DEFAULT_REPROBE_INTERVAL_S
    try:
        return float(raw)
    except ValueError:
        log.warning("hal0.memory.bad_reprobe_interval", value=raw)
        return _DEFAULT_REPROBE_INTERVAL_S


class DegradedMemoryProvider(MemoryProvider):
    """In-memory fallback that promotes itself to Hindsight once it can.

    Returned by ``provider_from_config`` in place of a bare
    :class:`PgVectorProvider` when the boot probe fails. Presents a STABLE
    object identity: the MCP mounts, the admin dispatcher and
    ``app.state.memory_provider`` all capture the provider once at boot, so
    promotion has to happen behind a reference they already hold — swapping
    the object out from under them would leave half the process talking to
    the dead fallback forever.
    """

    def __init__(
        self,
        *,
        promote: Any,
        fallback: PgVectorProvider | None = None,
        interval_s: float | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        """``promote`` is a 0-arg SYNC callable returning a live provider, or
        raising if the daemon is still unreachable. It is sync because the
        probe underneath it is blocking httpx; it is run on a worker thread so
        a hung daemon cannot stall the event loop.
        """
        self._promote = promote
        self._fallback = fallback if fallback is not None else PgVectorProvider()
        self._active: MemoryProvider = self._fallback
        self._interval_s = reprobe_interval_s() if interval_s is None else float(interval_s)
        self._clock = clock
        self._promoted = False
        self._lock = asyncio.Lock()
        # Never probed yet → allow the first attempt immediately.
        self._last_attempt: float | None = None
        self._attempts = 0
        # Ids already accepted by Hindsight during a partial drain. Kept so a
        # retry cannot double-write, independent of the document_id upsert.
        self._drained: set[str] = set()
        # Runtime toggles applied while degraded. Re-applied on promotion so
        # an operator's `hal0 memory graph enable` does not silently revert
        # to the config value when the daemon shows up.
        self._pending_toggles: dict[str, Any] = {}

    # ── state ──────────────────────────────────────────────────────────

    @property
    def degraded(self) -> bool:
        """True while serving from the volatile fallback.

        Delegates once promoted, so this tracks the real engine's live health
        (#1301) rather than latching on the boot decision. Flows to
        ``/api/status.memory_degraded`` and ``hal0 memory status``.
        """
        if self._promoted:
            return bool(getattr(self._active, "degraded", False))
        return True

    @property
    def promoted(self) -> bool:
        return self._promoted

    @property
    def volatile_rows(self) -> int:
        """Rows sitting in volatile storage — how much is at risk right now."""
        return len(self._fallback._rows)

    def degrade_state(self) -> dict[str, Any]:
        """Operator-visible detail behind the ``degraded`` bit."""
        return {
            "degraded": self.degraded,
            "promoted": self._promoted,
            "volatile_rows": self.volatile_rows,
            "promotion_attempts": self._attempts,
            "reprobe_interval_s": self._interval_s,
            "auto_promote": self._interval_s > 0,
        }

    def __getattr__(self, name: str) -> Any:
        """Delegate anything not defined here to the active provider.

        Keeps engine-specific surfaces (``hindsight_client``, used by the
        memory-admin routes) working after promotion, while behaving exactly
        as the bare fallback did before it — an AttributeError.
        """
        return getattr(object.__getattribute__(self, "_active"), name)

    # ── promotion ──────────────────────────────────────────────────────

    async def _resolve(self) -> MemoryProvider:
        """Return the provider to serve this call, promoting first if due."""
        if self._promoted:  # steady state: no lock, no clock read
            return self._active
        if not self._due():
            return self._active
        async with self._lock:
            if self._promoted:  # another caller won the race
                return self._active
            if self._due():
                await self._attempt_promotion()
            return self._active

    def _due(self) -> bool:
        if self._interval_s <= 0:  # auto-promotion disabled
            return False
        if self._last_attempt is None:
            return True
        return (self._clock() - self._last_attempt) >= self._interval_s

    async def _attempt_promotion(self) -> bool:
        """One promotion attempt. Caller holds the lock. Never raises."""
        self._last_attempt = self._clock()
        self._attempts += 1
        try:
            candidate = await asyncio.to_thread(self._promote)
        except Exception as exc:
            # Expected while the daemon is still down. Debug, not warning:
            # the stuck `degraded` flag is already the operator signal, and
            # this fires on a timer for as long as the outage lasts.
            log.debug(
                "hal0.memory.promotion_probe_failed",
                attempt=self._attempts,
                error=str(exc),
            )
            return False

        try:
            drained = await self._drain(candidate)
        except Exception as exc:
            # The daemon answered the probe but would not take our writes.
            # Staying degraded is the safe call: the fallback still holds
            # every row, so nothing is lost and every read stays complete.
            log.warning(
                "hal0.memory.promotion_drain_failed",
                attempt=self._attempts,
                error=str(exc),
                volatile_rows=self.volatile_rows,
                detail=(
                    "Hindsight is reachable but rejected the replay of "
                    "volatile rows; staying on the in-memory fallback rather "
                    "than promoting and orphaning them. Will retry."
                ),
            )
            return False

        self._active = candidate
        self._promoted = True
        self._reapply_toggles(candidate)
        self._fallback._rows.clear()
        self._drained.clear()
        log.warning(
            "hal0.memory.promoted_to_hindsight",
            attempt=self._attempts,
            replayed_rows=drained,
            detail=(
                "Hindsight became reachable after a degraded boot; memory is "
                "durable again. Rows written while degraded were replayed "
                "into the engine."
            ),
        )
        return True

    async def _drain(self, target: MemoryProvider) -> int:
        """Replay volatile rows into ``target``. Raises if any row fails.

        Rows stay in the fallback until the caller confirms the whole drain
        succeeded, so a mid-drain failure never removes a readable row.
        """
        replayed = 0
        for row in list(self._fallback._rows):
            if row["id"] in self._drained:
                continue
            await target.add(
                row["text"],
                dataset=row["dataset"],
                tags=list(row["tags"] or []),
                source=row["source"],
                metadata=dict(row["metadata"] or {}),
                # Replay under the original id so a retry upserts the same
                # document instead of creating a second copy.
                document_id=row["id"],
            )
            self._drained.add(row["id"])
            replayed += 1
        return replayed

    def _reapply_toggles(self, target: MemoryProvider) -> None:
        if "graph" in self._pending_toggles:
            enabled, slot = self._pending_toggles["graph"]
            target.set_graph_enabled(enabled, slot)
        if "rerank" in self._pending_toggles:
            target.set_rerank_enabled(self._pending_toggles["rerank"])

    async def promote_now(self) -> bool:
        """Force a promotion attempt, ignoring the re-probe interval.

        The explicit operator path — reachable even when auto-promotion is
        disabled — so a box that must come back durable RIGHT NOW does not
        have to wait out the timer or restart hal0-api.
        """
        if self._promoted:
            return True
        async with self._lock:
            if self._promoted:
                return True
            return await self._attempt_promotion()

    # ── MemoryProvider surface (delegating) ────────────────────────────

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
        provider = await self._resolve()
        return await provider.add(
            text,
            dataset=dataset,
            tags=tags,
            source=source,
            metadata=metadata,
            client_id=client_id,
            document_id=document_id,
        )

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
        provider = await self._resolve()
        return await provider.search(
            query,
            limit=limit,
            dataset=dataset,
            tags=tags,
            before=before,
            after=after,
            mode=mode,
            client_id=client_id,
        )

    async def list_items(
        self,
        dataset: str = "shared",
        cursor: str | None = None,
        limit: int = 50,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        provider = await self._resolve()
        return await provider.list_items(
            dataset=dataset, cursor=cursor, limit=limit, client_id=client_id
        )

    async def delete(
        self,
        ids: list[str],
        *,
        client_id: str | None = None,
        dataset: str | list[str] | None = None,
    ) -> dict[str, int]:
        provider = await self._resolve()
        return await provider.delete(ids, client_id=client_id, dataset=dataset)

    async def recall(
        self,
        query: str,
        *,
        types: list[str] | None = None,
        max_tokens: int = 4096,
        dataset: str | list[str] = "shared",
        tags: list[str] | None = None,
        tags_match: str | None = None,
        client_id: str | None = None,
    ) -> list[dict[str, Any]]:
        provider = await self._resolve()
        return await provider.recall(
            query,
            types=types,
            max_tokens=max_tokens,
            dataset=dataset,
            tags=tags,
            tags_match=tags_match,
            client_id=client_id,
        )

    async def reflect(
        self, *, dataset: str = "shared", client_id: str | None = None
    ) -> dict[str, Any]:
        provider = await self._resolve()
        return await provider.reflect(dataset=dataset, client_id=client_id)

    async def consolidate(self, *, dataset: str = "shared") -> dict[str, Any]:
        provider = await self._resolve()
        return await provider.consolidate(dataset=dataset)

    # Sync surface — cannot await, so it delegates to whatever is active now
    # and records the intent for replay onto the promoted provider.

    def graph_status(self) -> dict[str, Any]:
        return self._active.graph_status()

    def set_graph_enabled(self, enabled: bool, extraction_slot: str | None = None) -> None:
        self._pending_toggles["graph"] = (bool(enabled), extraction_slot)
        self._active.set_graph_enabled(enabled, extraction_slot)

    def set_rerank_enabled(self, enabled: bool) -> None:
        self._pending_toggles["rerank"] = bool(enabled)
        self._active.set_rerank_enabled(enabled)

    def register_compiled(self, *args: Any, **kwargs: Any) -> None:
        return self._active.register_compiled(*args, **kwargs)


__all__ = ["DegradedMemoryProvider", "reprobe_interval_s"]
