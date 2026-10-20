"""Close open WebSockets whose credential stopped granting access (#2801).

Every WebSocket gate in hal0 checks the caller's credential once, at the
upgrade: :class:`hal0.api.auth.AuthEnforcementMiddleware` for every
classified route, and :func:`hal0.api.agents._auth.check_ws_origin_and_cookie`
for the agent chat proxy. A socket then lives for hours. Without a second
look, replacing a credential (an admin key rotation, which also replaces the
session-signing secret) left every already-open socket working until it
dropped on its own, so the operator's response to a compromise did not reach
an attacker who already held a socket.

This module is the second look. Each gate registers the sockets it admits
here together with a ``still_authorized`` predicate that re-runs its own
check against the credential the socket was opened with (the upgrade's
cookie, bearer or ``api_key``, which a WebSocket cannot change later).
:func:`revalidate` runs every predicate and signals the sockets that fail;
``POST /api/auth/rotate`` calls it once the new key and secret are live.
Each gate closes a signalled socket with :data:`CLOSE_CREDENTIAL_REVOKED`.

:func:`guard_websocket` is the generic ASGI-level close the middleware uses,
so routes behind it need no code of their own.

The registry is process-local, like the credentials it re-checks: a rotation
that bypasses the running API (``hal0 auth reset-key`` writing ``api.env``
directly while the API is down) is applied by the restart it needs anyway.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Awaitable, Callable, Iterator, MutableMapping
from dataclasses import dataclass, field
from typing import Any, Final

import structlog

log = structlog.get_logger(__name__)

#: Close code for a socket whose credential no longer verifies. Private-use
#: range, beside the 4403 "you may not upgrade" rejection the gates send.
CLOSE_CREDENTIAL_REVOKED: Final[int] = 4401
CLOSE_REASON: Final[str] = "credential revoked"

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


@dataclass(eq=False)
class _Entry:
    still_authorized: Callable[[], bool]
    loop: asyncio.AbstractEventLoop
    revoked: asyncio.Event = field(default_factory=asyncio.Event)


_lock = threading.Lock()
_entries: set[_Entry] = set()


@contextlib.contextmanager
def track(still_authorized: Callable[[], bool]) -> Iterator[asyncio.Event]:
    """Register one open socket for the life of the ``with`` block.

    Yields an :class:`asyncio.Event` that :func:`revalidate` sets once
    ``still_authorized()`` returns false (or raises: deny by default). Must be
    entered from the event loop serving the socket.
    """
    entry = _Entry(still_authorized=still_authorized, loop=asyncio.get_running_loop())
    with _lock:
        _entries.add(entry)
    try:
        yield entry.revoked
    finally:
        with _lock:
            _entries.discard(entry)


def _check(entry: _Entry) -> bool:
    try:
        return bool(entry.still_authorized())
    except Exception as exc:  # deny by default: a broken check revokes
        log.warning("hal0.live_sockets.check_failed", error=str(exc))
        return False


def _signal(entry: _Entry) -> None:
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is entry.loop:
        entry.revoked.set()
    else:
        with contextlib.suppress(RuntimeError):  # loop already closed
            entry.loop.call_soon_threadsafe(entry.revoked.set)


def revalidate() -> int:
    """Re-check every open socket; signal the ones that fail. Returns that count.

    Call it after a credential changes in this process (the key in
    ``os.environ``, the secret on disk), never before: each predicate reads
    the live credential state.
    """
    with _lock:
        entries = [e for e in _entries if not e.revoked.is_set()]
    revoked = 0
    for entry in entries:
        if not _check(entry):
            _signal(entry)
            revoked += 1
    if revoked:
        log.info("hal0.live_sockets.revoked", count=revoked)
    return revoked


def open_count() -> int:
    """Number of sockets currently registered (for tests and diagnostics)."""
    with _lock:
        return len(_entries)


class _Revoked(ConnectionError):
    """Raised to the app when it sends on a socket this module closed.

    A :class:`ConnectionError` is an :class:`OSError`, which Starlette's
    ``WebSocket.send`` turns into ``WebSocketDisconnect`` the same way it
    handles a peer that went away.
    """


async def guard_websocket(
    app: Callable[[Scope, Receive, Send], Awaitable[None]],
    scope: Scope,
    receive: Receive,
    send: Send,
    still_authorized: Callable[[], bool],
) -> None:
    """Run ``app`` for one WebSocket, closing it once :func:`revalidate` revokes it.

    On revocation the client gets a :data:`CLOSE_CREDENTIAL_REVOKED` close
    right away, the app's pending ``receive()`` returns a
    ``websocket.disconnect``, and any frame the client sent after the
    revocation is dropped rather than delivered. A later send from the app
    raises (see :class:`_Revoked`); a later close is a no-op.
    """
    closed = False

    async def close_revoked() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        with contextlib.suppress(Exception):
            await send(
                {
                    "type": "websocket.close",
                    "code": CLOSE_CREDENTIAL_REVOKED,
                    "reason": CLOSE_REASON,
                }
            )

    with track(still_authorized) as revoked:

        async def guarded_send(message: Message) -> None:
            nonlocal closed
            if revoked.is_set():
                await close_revoked()
                if message.get("type") == "websocket.close":
                    return
                raise _Revoked("websocket credential revoked")
            if message.get("type") == "websocket.close":
                closed = True
            await send(message)

        async def guarded_receive() -> Message:
            if not revoked.is_set():
                incoming = asyncio.ensure_future(receive())
                waiter = asyncio.ensure_future(revoked.wait())
                try:
                    await asyncio.wait({incoming, waiter}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    waiter.cancel()
                    if not incoming.done():
                        incoming.cancel()
                if not revoked.is_set():
                    return incoming.result()
                if (
                    incoming.done()
                    and not incoming.cancelled()
                    and incoming.exception() is None
                    and incoming.result().get("type") == "websocket.disconnect"
                ):
                    return incoming.result()  # the peer left on its own
            await close_revoked()
            return {"type": "websocket.disconnect", "code": CLOSE_CREDENTIAL_REVOKED}

        async def close_on_revoke() -> None:
            await revoked.wait()
            await close_revoked()

        watcher = asyncio.ensure_future(close_on_revoke())
        try:
            await app(scope, guarded_receive, guarded_send)
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher


__all__ = [
    "CLOSE_CREDENTIAL_REVOKED",
    "guard_websocket",
    "open_count",
    "revalidate",
    "track",
]
