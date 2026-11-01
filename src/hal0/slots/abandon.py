"""Tie a lazy slot load to the request that asked for it (#2944).

A request for a model whose slot is not loaded makes the API drive
``SlotManager.load`` and wait for it. When the client goes away (its own
timeout, a closed connection) nothing used to notice: the wait ran to the
slot's ready timeout and the dispatcher's own load, entered next, started a
second cold load from the ``WARMING`` the first one parked the slot in.

Callers with a request publish a probe, a coroutine function that answers
"has the client gone?", in :data:`abandon_probe`. The slot manager reads it,
so no signature on the path from route to manager changes and callers
without a request (reaper, reconciler, CLI, bridge) see no difference.

What an abandoned request changes, and what it does not:

- It stops waiting: :func:`wait_for_load` returns when the probe says the client
  left, without cancelling the load.
- It starts nothing: ``SlotManager.load`` returns the slot's current status at once
  when the probe says so after it has the slot lock, so a request queued behind
  another's load does not begin a load of its own, and neither does the
  stale-``WARMING`` recovery.
- It never tears down a load that is running. That load holds the slot lock,
  other requests may be waiting on it, and cancelling a start mid-flight is
  unsafe (see the shield in ``hal0.providers.flm``). It runs to completion
  and parks the slot in whatever state it ends in; the next live request
  recovers from that state as usual.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

#: How often :func:`wait_for_load` asks the probe while the load runs.
POLL_INTERVAL_S = 0.25

#: How long :class:`ClientGoneProbe` waits on the request's next ASGI message.
_RECEIVE_PROBE_S = 0.05

AbandonProbe = Callable[[], Awaitable[bool]]

#: The probe of the request this task works for; ``None`` for internal callers.
abandon_probe: contextvars.ContextVar[AbandonProbe | None] = contextvars.ContextVar(
    "hal0_abandon_probe", default=None
)

#: Loads whose requester left. The task keeps running; this holds it so it is
#: not collected mid-flight and its outcome is consumed, never "never retrieved".
_detached: set[asyncio.Task[Any]] = set()


class _HasReceive(Protocol):
    async def receive(self) -> Any: ...


class ClientGoneProbe:
    """Whether a request's client has disconnected, as a callable probe.

    Listens for the next ASGI message rather than calling
    ``Request.is_disconnected()``, which reads under an already-cancelled
    scope and can miss a disconnect the server answers late (see
    ``hal0.api.live_sockets``). ``http.disconnect`` means the client is gone;
    a timeout means it is still there; an ``http.request`` chunk (an unread
    body) is not an answer. Once true the answer sticks. A lock keeps the
    handler's polling and the load task's check from reading ``receive``
    at once.

    :meth:`close` ends it when the request is done: a background task spawned
    during the request inherits this probe through its context, and a finished
    request reads as disconnected, which would make that task's own load a no-op.
    """

    def __init__(self, request: _HasReceive) -> None:
        self._request = request
        self._gone = False
        self._closed = False
        self._lock = asyncio.Lock()

    def close(self) -> None:
        self._closed = True

    async def __call__(self) -> bool:
        if self._closed:
            return False
        if self._gone:
            return True
        async with self._lock:
            if self._gone:
                return True
            try:
                message = await asyncio.wait_for(self._request.receive(), timeout=_RECEIVE_PROBE_S)
            except TimeoutError:
                return False
            except Exception:
                return False
            if isinstance(message, dict) and message.get("type") == "http.disconnect":
                self._gone = True
        return self._gone


async def is_abandoned() -> bool:
    """True when the current request published a probe and its client has gone."""
    probe = abandon_probe.get()
    if probe is None:
        return False
    try:
        return bool(await probe())
    except Exception:
        return False


def _consume(task: asyncio.Task[Any]) -> None:
    _detached.discard(task)
    if not task.cancelled():
        task.exception()


async def wait_for_load[T](task: asyncio.Task[T], probe: AbandonProbe) -> tuple[bool, T | None]:
    """Wait for *task*, giving up the wait (not the task) when *probe* turns true.

    Returns ``(True, result)`` when the task finished, re-raising what it raised;
    ``(False, None)`` when the client left first. The task is then left running to
    completion, referenced here until it ends, including when the waiter itself
    is cancelled.
    """
    _detached.add(task)
    task.add_done_callback(_consume)
    while True:
        done, _ = await asyncio.wait({task}, timeout=POLL_INTERVAL_S)
        if done:
            return True, task.result()
        try:
            gone = bool(await probe())
        except Exception:
            gone = False
        if gone:
            return False, None
