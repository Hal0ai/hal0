"""End open WebSockets and streams whose credential stopped granting access.

The enforcement middleware (:class:`hal0.api.auth.AuthEnforcementMiddleware`)
checks a caller's credential once: at a WebSocket's upgrade (the board feed,
``/v1/realtime``), or when a streaming HTTP response (a ``text/event-stream``
feed such as ``/api/events/stream`` or ``/api/journal/stream``, a streamed
``/v1/chat/completions``) is requested. Either then lives for hours. Without a
second look, replacing a credential (an admin key rotation, which also
replaces the session-signing secret) left every already-open socket and stream
working until it dropped on its own, so the operator's response to a
compromise did not reach an attacker who already held one (#2801, #2816).

This module is the second look. The middleware registers each socket it
admits, and each ``text/event-stream`` response it lets through, here together with a
``still_authorized`` predicate that re-runs its own gate against the
credential the request carried (the cookie, bearer or ``api_key``, which a
request cannot change later). :func:`revalidate` runs every predicate and
signals the ones that fail. ``POST /api/auth/rotate`` calls it once the new
key and secret are live, and a single process-wide ticker calls it every
:data:`REVALIDATE_INTERVAL_S`, so a session cookie that *expires* ends its
sockets and streams too, without a rotation.

:func:`guard_websocket` closes a signalled socket with
:data:`CLOSE_CREDENTIAL_REVOKED`. :func:`guard_http_stream` ends a signalled
response: see its docstring for exactly what the client and the app see. Both
are generic ASGI wrappers, so routes behind the middleware need no code of
their own and a new stream is covered by default.

The registry is process-local, like the credentials it re-checks: a rotation
that bypasses the running API (``hal0 auth reset-key`` falling back to writing
the secret and ``api.env`` itself when its API call fails) is not seen here,
and the CLI tells the operator to restart hal0-api, which closes every socket
and stream.
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

#: Last frame of a revoked ``text/event-stream`` response, so a client can
#: tell "signed out" from a dropped connection. A named event: an
#: ``EventSource`` ``onmessage`` handler ignores it, and the stream then ends.
REVOKED_SSE_EVENT: Final[bytes] = b'event: revoked\ndata: {"reason": "credential revoked"}\n\n'

#: How long a revoked stream's app may take to wind down (release its
#: subscriptions, run its ``finally`` blocks) before it is cancelled.
STREAM_DRAIN_TIMEOUT_S: float = 5.0

#: Body chunks a revoked stream may still send, and have dropped, while it
#: winds down through the disconnect (see :func:`guard_http_stream`) before it
#: is stopped with :class:`_StreamRevoked` instead. Starlette's
#: ``StreamingResponse`` sends one or two; the cap stops an app that sends
#: without ever yielding to the loop from spinning on dropped chunks.
REVOKED_CHUNKS_DROPPED_MAX: Final[int] = 32

#: How often the process-wide ticker re-checks every open socket and stream.
#: Bounds how long one outlives the expiry of the session cookie it was
#: opened with; a rotation does not wait for it.
REVALIDATE_INTERVAL_S: float = 30.0

#: Consecutive ticker passes whose check *raised* before an entry is revoked.
#: One transient error (an ``OSError`` reading ``secret.bin``) must not end
#: every cookie connection at once. A check that returns false, and every
#: check a rotation runs, still revokes on the first failure.
TICK_ERRORS_TO_REVOKE: Final[int] = 2

#: Scope key a route sets (:func:`mark_long_lived`) to have its streaming
#: response registered whatever its content type says.
LONG_LIVED_SCOPE_KEY: Final[str] = "hal0.long_lived_stream"

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


@dataclass(eq=False)
class _Entry:
    still_authorized: Callable[[], bool]
    loop: asyncio.AbstractEventLoop
    revoked: asyncio.Event = field(default_factory=asyncio.Event)
    tick_errors: int = 0


_lock = threading.Lock()
_entries: set[_Entry] = set()
_ticker: threading.Thread | None = None
_ticker_wake = threading.Event()


@contextlib.contextmanager
def track(still_authorized: Callable[[], bool]) -> Iterator[asyncio.Event]:
    """Register one open socket for the life of the ``with`` block.

    Yields an :class:`asyncio.Event` that :func:`revalidate` sets once
    ``still_authorized()`` returns false (or raises: deny by default). The
    check also runs once on entry, after registering, so a rotation that
    landed between the gate's own check and this call is not missed: the
    event is then already set. Must be entered from the event loop serving
    the socket.

    ``still_authorized`` runs on the event loop (on entry, and for a
    rotation) and also on the ticker thread (:func:`_tick`), at the same
    time as the loop. It must therefore be thread-safe and must not touch
    loop state: read the credential state and the request's scope, nothing
    else. The middleware's predicate (``_still_admitted``) does only that:
    it reads ``os.environ``, the cached auth posture, the immutable scope
    headers, query string and peer, the session secret file and, for a
    bridge key, ``bridge.db`` (one connection per call).
    """
    entry = _Entry(still_authorized=still_authorized, loop=asyncio.get_running_loop())
    with _lock:
        _entries.add(entry)
        _ensure_ticker()
    if not _check(entry):
        entry.revoked.set()
    try:
        yield entry.revoked
    finally:
        with _lock:
            _entries.discard(entry)


def _check(entry: _Entry, *, tick: bool = False) -> bool:
    """Run one entry's predicate. Deny by default: a check that raises revokes.

    On the ticker path (``tick``) a raising check is forgiven until it has
    raised :data:`TICK_ERRORS_TO_REVOKE` times in a row, so one transient
    error does not end every connection. Nothing else is forgiven.
    """
    try:
        allowed = bool(entry.still_authorized())
    except Exception as exc:
        if tick:
            entry.tick_errors += 1
            if entry.tick_errors < TICK_ERRORS_TO_REVOKE:
                log.warning(
                    "hal0.live_sockets.check_failed",
                    error=str(exc),
                    consecutive=entry.tick_errors,
                    revoked=False,
                )
                return True
        log.warning("hal0.live_sockets.check_failed", error=str(exc), revoked=True)
        return False
    entry.tick_errors = 0
    return allowed


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


def revalidate(*, tick: bool = False) -> int:
    """Re-check every open socket and stream; signal the ones that fail. Returns that count.

    Call it after a credential changes in this process (the key in
    ``os.environ``, the secret on disk), never before: each predicate reads
    the live credential state. ``tick`` is for the periodic ticker only (see
    :func:`_check`); a rotation must stay strict.
    """
    with _lock:
        entries = [e for e in _entries if not e.revoked.is_set()]
    revoked = 0
    for entry in entries:
        if not _check(entry, tick=tick):
            _signal(entry)
            revoked += 1
    if revoked:
        log.info("hal0.live_sockets.revoked", count=revoked)
    return revoked


def _ensure_ticker() -> None:
    """Start the process-wide re-check thread if none is running. Hold ``_lock``."""
    global _ticker
    if _ticker is None or not _ticker.is_alive():
        _ticker = threading.Thread(target=_tick, name="hal0-live-revalidate", daemon=True)
        _ticker.start()


def _tick() -> None:
    """Re-check every registered entry each interval; exit once none are left.

    One thread for the whole process rather than a timer per connection. It
    catches what no rotation announces: a session cookie that expired. Each
    predicate already fails safe (:func:`_check`); this loop only guards
    itself so one bad pass cannot end the re-checks for good.
    """
    global _ticker
    while True:
        _ticker_wake.wait(REVALIDATE_INTERVAL_S)
        _ticker_wake.clear()
        with _lock:
            if not _entries:
                _ticker = None
                return
        try:
            revalidate(tick=True)
        except Exception as exc:
            log.warning("hal0.live_sockets.tick_failed", error=str(exc))


def mark_long_lived(scope: Scope) -> None:
    """Have this request's streaming response registered like an event stream.

    For a route that streams for long under a content type it does not
    control, such as a ``/v1`` ``stream: true`` relay, which passes through
    whatever content type the upstream sent. The scope is the one the
    middleware holds, so a route sets it on ``request.scope``.
    """
    scope[LONG_LIVED_SCOPE_KEY] = True


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


class _StreamRevoked(ConnectionError):
    """Raised to the app when it sends on a stream this module ended.

    An :class:`OSError`, which is what a send on a connection the client
    closed raises, so apps and Starlette already treat it as a disconnect.
    """


async def guard_http_stream(
    app: Callable[[Scope, Receive, Send], Awaitable[None]],
    scope: Scope,
    receive: Receive,
    send: Send,
    still_authorized: Callable[[], bool],
) -> None:
    """Run ``app`` for one HTTP request, ending its response once it is revoked.

    Only a ``text/event-stream`` response, or one whose route called
    :func:`mark_long_lived`, is registered, from its start until the request
    ends: that is the long-lived kind (the event, journal and activity feeds,
    streamed completions). Any other response, a chunked
    one included, is bounded and finishes on its own; registering those would
    also catch the rotation request itself, whose body is chunked by the
    middleware stack and whose own credential the rotation has just
    replaced. An ordinary request therefore costs nothing here. Registering re-runs the check once (see :func:`track`), so a
    rotation that landed while the route was setting up is not missed.

    On revocation:

    - nothing more the app sends reaches the client. If the app listens for
      the disconnect below (it is waiting in ``receive()``, or has been
      handed ``http.disconnect``), a later body chunk is dropped quietly.
      Raising there would throw out of Starlette's ``async for`` over the
      route's generator and leave it suspended at ``yield``, so its
      ``finally`` (unsubscribes, upstream closes) would wait for the garbage
      collector; dropping it lets the generator run on to its next
      ``await``, where the disconnect's cancellation closes it. An app that
      is not listening, or that sends more than
      :data:`REVOKED_CHUNKS_DROPPED_MAX` chunks, gets :class:`_StreamRevoked`
      (an ``OSError``, the same thing a send on a closed connection raises)
      to stop it producing;
    - the app's ``receive()`` returns ``http.disconnect``, a pending one
      included, which is how Starlette's ``StreamingResponse`` and routes
      polling ``request.is_disconnected()`` learn a client went away; their
      normal disconnect path closes the generator and releases its
      subscriptions;
    - once the app returns, the response is ended: a ``text/event-stream``
      one with :data:`REVOKED_SSE_EVENT` as its last frame, a stream marked
      by :func:`mark_long_lived` under another type with no extra bytes;
    - an app still running :data:`STREAM_DRAIN_TIMEOUT_S` after the
      revocation is cancelled. Errors the app raises while winding down are
      logged, not raised into the server.
    """
    loop = asyncio.get_running_loop()
    stack = contextlib.ExitStack()
    revoked: asyncio.Event | None = None
    stop = asyncio.Event()  # set once ``revoked`` is; exists before arming
    watcher: asyncio.Future[None] | None = None
    body_done = False
    listening = 0  # receive() calls waiting for a disconnect right now
    disconnect_delivered = False
    # The one read of the server's ``receive`` after the body, shared by
    # every caller and kept across a caller's cancellation; and whether the
    # client has left, so each later call says so without asking again.
    pending: asyncio.Future[Message] | None = None
    client_gone = False
    dropped = 0
    event_stream = False
    started = False
    finished = False

    def stopped() -> bool:
        return revoked is not None and revoked.is_set()

    async def guarded_receive() -> Message:
        nonlocal body_done, listening, disconnect_delivered, pending, client_gone
        if stopped():
            disconnect_delivered = True
            return {"type": "http.disconnect"}
        if client_gone:
            return {"type": "http.disconnect"}
        if not body_done:
            # The request body is read before any response streams; only a
            # receive after it (a disconnect listener) needs waking.
            message = await receive()
            if message.get("type") != "http.request" or not message.get("more_body", False):
                body_done = True
            return message
        # The read of the server is not cancelled when this call is.
        # ``request.is_disconnected()`` calls in an already-cancelled scope:
        # cancelling the read with it threw away a disconnect the server
        # answered meanwhile, so the check never turned true here (#3068).
        # Kept, the read's answer is waiting for the next call instead.
        if pending is None:
            pending = asyncio.ensure_future(receive())
        incoming = pending
        if not incoming.done():
            waiter = asyncio.ensure_future(stop.wait())
            listening += 1
            try:
                await asyncio.wait({incoming, waiter}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                listening -= 1
                waiter.cancel()
        if stopped():
            disconnect_delivered = True
            return {"type": "http.disconnect"}
        if pending is incoming:
            pending = None
        message = incoming.result()
        if message.get("type") == "http.disconnect":
            client_gone = True
        return message

    async def guarded_send(message: Message) -> None:
        nonlocal started, finished, event_stream, dropped
        kind = message.get("type")
        if kind == "http.response.start":
            started = True
            for name, value in message.get("headers") or ():
                if bytes(name).lower() == b"content-type":
                    event_stream = bytes(value).lower().startswith(b"text/event-stream")
            if event_stream or scope.get(LONG_LIVED_SCOPE_KEY):
                arm()
        elif kind == "http.response.body":
            if stopped():
                if not message.get("more_body", False):
                    return  # the app ending its response: ours ends it instead
                if (listening or disconnect_delivered) and dropped < REVOKED_CHUNKS_DROPPED_MAX:
                    dropped += 1
                    return  # the app is winding down: see the docstring
                raise _StreamRevoked("stream credential revoked")
            if not message.get("more_body", False):
                finished = True
        await send(message)

    def arm() -> None:
        nonlocal revoked, watcher
        if revoked is None:
            revoked = stack.enter_context(track(still_authorized))
            watcher = asyncio.ensure_future(watch(revoked))

    async def watch(event: asyncio.Event) -> None:
        await event.wait()
        stop.set()
        with contextlib.suppress(RuntimeError):  # the app already returned
            deadline.reschedule(loop.time() + STREAM_DRAIN_TIMEOUT_S)

    try:
        async with asyncio.timeout(None) as deadline:
            await app(scope, guarded_receive, guarded_send)
    except Exception as exc:
        if not stopped():
            raise
        log.info(
            "hal0.live_sockets.stream_wind_down",
            path=scope.get("path", ""),
            error=type(exc).__name__,
        )
    finally:
        if watcher is not None:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
        if pending is not None:
            pending.cancel()  # a read nobody is waiting for any more
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await pending
        stack.close()
    if stopped() and started and not finished:
        finished = True
        with contextlib.suppress(Exception):
            await send(
                {
                    "type": "http.response.body",
                    "body": REVOKED_SSE_EVENT if event_stream else b"",
                    "more_body": False,
                }
            )


__all__ = [
    "CLOSE_CREDENTIAL_REVOKED",
    "REVOKED_SSE_EVENT",
    "guard_http_stream",
    "guard_websocket",
    "mark_long_lived",
    "open_count",
    "revalidate",
    "track",
]
