"""Background (``hal0/memory``) requests yield to foreground user chat (#3333).

On a CPU-only box a single slot (e.g. ``brain``) runs ``llama-server
--parallel 1``. Hindsight's background LLM traffic (extraction, reflect,
consolidation) enters hal0 through the ONE fixed virtual model id
``hal0/memory`` (``MEMORY_ALIAS``, :mod:`hal0.memory.extraction_env`) and,
once resolved, targets the same slot a user's chat turn needs. hal0-api is
the only thing feeding ``llama-server``, so a background call that is held
here before dispatch is a background call that never occupies the slot's one
sequence ahead of a waiting user (measured 146s-280s queues, #3333).

Design (operator-ruled, see issue #3333):

1. A request is BACKGROUND iff its ``hal0/memory``-ness was recorded on
   ``request.state`` by the chat normalization gate
   (:data:`hal0.dispatcher.lane_pin.MEMORY_REQUEST_ATTR`); everything else is
   FOREGROUND.
2. Per-slot gate: a BACKGROUND request waits, before dispatch, while any
   FOREGROUND request for the same slot is registered (waiting to dispatch OR
   in flight — the route layer brackets both with :meth:`BackgroundGate.foreground`).
   FOREGROUND requests never wait on this gate. FIFO within a class falls out
   of :class:`asyncio.Condition`'s wait queue.
3. The caller decides whether to gate at all — this module has no opinion on
   slot parallelism. The route layer only calls :meth:`wait_background` when
   the target slot's parallelism is known and ``<= 1``
   (:func:`hal0.memory.extraction_env.extraction_slot_parallel`); unknown or
   ``> 1`` means no local single-sequence slot is being starved, so the route
   layer skips the wait entirely (safe default: no gating).
4. Bounded wait: ``max_wait_s`` (route layer passes 900s) caps how long a
   background request yields; it is also released early if the caller's
   ``is_disconnected`` callback reports the client gone.
5. Out of scope: preempting an in-flight ``llama-server`` call. This gate
   only stops a NEW chunk of background work from starting while a user
   request is pending or running; it cannot interrupt a background call
   already inside the slot.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import structlog

log = structlog.get_logger(__name__)

#: Hard cap on how long a background request yields to foreground traffic on
#: the same slot (#3333 point 4). Chosen well above the measured 146s-280s
#: queues this gate exists to avoid, so a background call only ever hits the
#: cap under genuinely pathological foreground load.
DEFAULT_MAX_WAIT_S = 900.0

#: How often :meth:`BackgroundGate.wait_background` re-checks the cap and the
#: disconnect callback while parked on the condition variable. A background
#: request can otherwise sleep past a client disconnect or the cap by up to
#: one notify cycle, which never arrives if foreground traffic is steady.
_POLL_INTERVAL_S = 1.0


class _SlotGate:
    """One slot's foreground head-count and the condition background waits on."""

    __slots__ = ("condition", "foreground_count")

    def __init__(self) -> None:
        self.condition = asyncio.Condition()
        self.foreground_count = 0


class BackgroundGate:
    """Per-slot-name gate. One instance lives on ``app.state`` (lazily created).

    Not process-global by design: a bare-router test or a second app
    instance gets its own gate instead of silently sharing state with
    another test's slots.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._gates: dict[str, _SlotGate] = {}
        self._clock = clock

    def _gate(self, slot_name: str) -> _SlotGate:
        gate = self._gates.get(slot_name)
        if gate is None:
            gate = _SlotGate()
            self._gates[slot_name] = gate
        return gate

    @contextlib.asynccontextmanager
    async def foreground(self, slot_name: str) -> AsyncIterator[None]:
        """Bracket a FOREGROUND request's wait-to-dispatch + in-flight window.

        Held for the whole dispatch (#3333 point 2): a background request on
        the same slot must not slip in between a foreground request queuing
        for the slot and that request actually finishing.
        """
        gate = self._gate(slot_name)
        async with gate.condition:
            gate.foreground_count += 1
        try:
            yield
        finally:
            async with gate.condition:
                gate.foreground_count -= 1
                if gate.foreground_count <= 0:
                    gate.foreground_count = 0
                    gate.condition.notify_all()

    async def wait_background(
        self,
        slot_name: str,
        *,
        max_wait_s: float = DEFAULT_MAX_WAIT_S,
        is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    ) -> float:
        """Block while ``slot_name`` has a registered FOREGROUND request.

        Returns the number of seconds actually waited (``0.0`` when the slot
        was already clear). Never raises: a disconnect check that itself
        raises is treated as "still connected" so a flaky probe cannot
        release the wait early and race a slot it shouldn't.
        """
        gate = self._gate(slot_name)
        start = self._clock()
        async with gate.condition:
            while gate.foreground_count > 0:
                elapsed = self._clock() - start
                remaining = max_wait_s - elapsed
                if remaining <= 0:
                    break
                if is_disconnected is not None:
                    with contextlib.suppress(Exception):
                        if await is_disconnected():
                            break
                try:
                    await asyncio.wait_for(
                        gate.condition.wait(), timeout=min(remaining, _POLL_INTERVAL_S)
                    )
                except TimeoutError:
                    continue
        return self._clock() - start


def get_background_gate(app_state: object) -> BackgroundGate:
    """The :class:`BackgroundGate` for ``app.state``, created on first use.

    Lazy + cached on ``app.state`` rather than built in the lifespan: every
    caller (route handler, test harness with a bare router) gets a working
    gate without threading another lifespan dependency through.
    """
    gate = getattr(app_state, "background_gate", None)
    if isinstance(gate, BackgroundGate):
        return gate
    gate = BackgroundGate()
    with contextlib.suppress(Exception):
        app_state.background_gate = gate  # type: ignore[attr-defined]
    return gate
