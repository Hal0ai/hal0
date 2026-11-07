"""Per-slot serial admission scheduler (serial-backend admission, see ADR-NNNN).

FastFlowLM — and in general any backend bound to a single NPU/accelerator
context — executes exactly one request at a time internally ("the NPU runs
one request at a time"). hal0 used to forward concurrent requests straight
to such a slot; they queued *inside* the backend where the dispatcher's read
timeout (``[dispatcher].direct_read_timeout_s``) could not see them, so a
long non-streaming call (e.g. Hindsight memory extraction, ~90s compute)
stuck behind another request would blow its timeout budget before it ever
started and get retried, wasting NPU work.

This module gives slots with ``[slot].max_inflight`` set a tiny FIFO-by-
priority ticket queue *in front of* the forward. A request acquires a
ticket before it is forwarded and releases it when the forward completes,
errors, the client disconnects, or (for streaming) when the response
stream ends. The read-timeout clock for the forward itself only starts
after the ticket is granted, so queue wait is no longer charged against it.

Slots without ``max_inflight`` never touch this module — :func:`acquire`
returns an always-already-granted no-op ticket for them, so unlimited
concurrency behavior is unchanged.

Priority classes (lower number = served first): ``PRIORITY_DECISION`` (0,
``POST /v1/systemone``) always jumps ahead of ``PRIORITY_DEFAULT`` (1,
everything else). FIFO within a class. Aging is not implemented — a steady
stream of decision calls could in principle starve a queued chat request;
operationally FLM decision calls are short (~5-12s) and infrequent enough
that this has not been worth the complexity. Document, don't fix, unless
it becomes a real problem.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

PRIORITY_DECISION = 0
"""POST /v1/systemone — always served ahead of ordinary traffic."""

PRIORITY_DEFAULT = 1
"""Everything else (chat, embeddings, memory extraction, ...)."""


class AdmissionTimeout(Exception):
    """Raised when a request waits longer than admission_wait_timeout_s.

    The caller (dispatcher) must translate this into an HTTP 503 with a
    Retry-After header — this module stays HTTP-agnostic.
    """

    def __init__(self, slot_name: str, waited_s: float, queue_depth: int) -> None:
        self.slot_name = slot_name
        self.waited_s = waited_s
        self.queue_depth = queue_depth
        super().__init__(
            f"admission wait timed out for slot {slot_name!r} after {waited_s:.1f}s "
            f"(queue depth {queue_depth})"
        )


@dataclass(order=True)
class _Ticket:
    sort_key: tuple[int, int] = field(compare=True)
    priority: int = field(compare=False)
    event: asyncio.Event = field(compare=False, default_factory=asyncio.Event)
    enqueued_at: float = field(compare=False, default=0.0)


class SlotAdmission:
    """One priority FIFO queue + inflight gate for a single slot.

    Not a semaphore: a semaphore wakes whichever waiter asyncio happens to
    pick, which is not priority-ordered. Instead we keep an explicit heap of
    waiters and hand the gate to the best-priority one on every release.
    """

    def __init__(self, slot_name: str, max_inflight: int) -> None:
        if max_inflight < 1:
            raise ValueError("max_inflight must be >= 1")
        self.slot_name = slot_name
        self.max_inflight = max_inflight
        self._inflight = 0
        self._waiters: list[_Ticket] = []
        self._seq = itertools.count()
        self._lock = asyncio.Lock()

    @property
    def queue_depth(self) -> int:
        return len(self._waiters)

    def queue_depth_by_priority(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for t in self._waiters:
            out[t.priority] = out.get(t.priority, 0) + 1
        return out

    async def acquire(self, priority: int, timeout_s: float) -> None:
        """Block until this request may proceed, or raise AdmissionTimeout."""
        async with self._lock:
            if self._inflight < self.max_inflight and not self._waiters:
                self._inflight += 1
                return
            ticket = _Ticket(sort_key=(priority, next(self._seq)), priority=priority)
            ticket.enqueued_at = time.monotonic()
            self._waiters.append(ticket)
            self._waiters.sort(key=lambda t: t.sort_key)

        try:
            await asyncio.wait_for(ticket.event.wait(), timeout=timeout_s)
        except TimeoutError as exc:
            async with self._lock:
                if ticket in self._waiters:
                    self._waiters.remove(ticket)
                    waited = time.monotonic() - ticket.enqueued_at
                    raise AdmissionTimeout(self.slot_name, waited, len(self._waiters)) from exc
            # Ticket was granted in the race between our timeout firing and
            # the lock being acquired by release(): release() already handed
            # the gate to us (inflight count unchanged, one left one
            # entered), so just proceed as admitted.
            return

    def release(self) -> None:
        """Release one inflight slot and grant it to the next-best waiter, if any."""
        # Synchronous on purpose: called from finally blocks that may not be
        # able to await (and doesn't need to be — no I/O here).
        if self._waiters:
            ticket = self._waiters.pop(0)
            ticket.event.set()  # hands the gate straight to this waiter
            return
        self._inflight = max(0, self._inflight - 1)


class AdmissionRegistry:
    """Owns one :class:`SlotAdmission` per slot that has ``max_inflight`` set.

    Slots without ``max_inflight`` never get an entry here, so
    :meth:`acquire` is a no-op async contextmanager for them — zero
    behavior change for the common case.
    """

    def __init__(self) -> None:
        self._slots: dict[str, SlotAdmission] = {}
        self._lock = asyncio.Lock()

    def configure(self, slot_name: str, max_inflight: int | None) -> None:
        """Declare (or clear) the admission limit for a slot.

        Idempotent; safe to call on every config reload. Shrinking/growing
        ``max_inflight`` on a live slot just changes the gate size — in
        flight requests are unaffected, only future acquires see the new cap.
        """
        if max_inflight is None:
            self._slots.pop(slot_name, None)
            return
        existing = self._slots.get(slot_name)
        if existing is not None and existing.max_inflight == max_inflight:
            return
        self._slots[slot_name] = SlotAdmission(slot_name, max_inflight)

    def status(self) -> dict[str, dict[str, object]]:
        """Small dict suitable for exposing under /api/slots/{name}."""
        return {
            name: {
                "max_inflight": adm.max_inflight,
                "queue_depth": adm.queue_depth,
                "queue_depth_by_priority": adm.queue_depth_by_priority(),
            }
            for name, adm in self._slots.items()
        }

    @asynccontextmanager
    async def acquire(self, slot_name: str, priority: int, timeout_s: float) -> AsyncIterator[None]:
        """Admit this request to ``slot_name``, released on context exit.

        No-op (immediate admission, nothing to release) when the slot has no
        configured ``max_inflight`` — the hot path for every slot that isn't
        opted into serial admission.
        """
        adm = self._slots.get(slot_name)
        if adm is None:
            yield
            return
        await adm.acquire(priority, timeout_s)
        try:
            yield
        finally:
            adm.release()


#: Process-wide singleton. The dispatcher and the slot-status route both
#: import this rather than threading a registry instance through every call
#: site — mirrors how ``hal0.slots.state`` is used elsewhere in this tree.
REGISTRY = AdmissionRegistry()
