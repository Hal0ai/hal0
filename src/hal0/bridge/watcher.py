"""The run watcher: one task per delegated run, tailing Hermes into the ledger (ADR-0043).

ADR-0043 decision 9 makes the ledger in ``bridge.db`` the bridge's durable
record of a run, so a client that disconnects, or a hal0-api restart, loses
nothing. :class:`RunWatcher` keeps that record current. For each live run it
follows ``GET /v1/runs/<id>/events`` with ``Last-Event-ID`` and:

* writes every event to the ledger verbatim, except ``message.delta``: deltas
  are joined into the run's ``output`` and stored as one ``message.text`` event
  per :attr:`WatcherTiming.coalesce_s` or :attr:`WatcherTiming.coalesce_bytes`,
  whichever comes first (checked as each delta arrives), and whenever any other
  event arrives or the stream ends;
* sets the run's status from Hermes's events, plus hal0's derived
  ``waiting_for_clarification`` between ``clarify.request`` and
  ``clarify.settled`` (Hermes itself reports ``running`` there);
* mirrors an ``approval.request`` into the operator's approval inbox as tool
  ``agent_run_approval``, which grants ``once`` or posts ``deny``: there is no
  approve tool, a gated action needs a human (ADR-0004);
* keeps a ``clarify.request`` for the delegating client, and stops the run with
  ``"clarification unanswered after <n>s"`` if nobody answers within
  ``[bridge].clarify_timeout_s``;
* reconnects after a quiet end or a dropped stream, with backoff, from the last
  sequence it stored. A run that is silent for a minute is not a failed run (the
  local model can take 40 s to its first token), so silence alone never ends a
  watch. The watch ends at a terminal event, when Hermes no longer knows the run
  (404: status ``unknown``), or after :attr:`WatcherTiming.give_up_s` without
  reaching Hermes.

A decision made in the inbox can fail (Hermes restarting, a dropped connection).
The run then stays ``waiting_for_approval`` in the ledger, so the watcher files the
request in the inbox again unless Hermes answered that the request is no longer
pending (409 ``approval_not_pending`` or ``stale_generation``): the operator must
always have a row to answer. For the same reason the ledger holds a pending request
before its inbox row exists, and a decision reads the ledger under the run's lock.

Every change to a run calls ``on_change(run_id)``; :class:`~hal0.bridge.service.
BridgeService` uses it to wake ``wait_run`` and the run event streams. All
ledger access goes through ``asyncio.to_thread`` (the store is synchronous
SQLite), and every write to a run's row happens under that run's lock, so the
watcher, the approval executor and the service never interleave a
read-modify-write.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import time
import weakref
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import structlog

from hal0.bridge.errors import BridgeError
from hal0.bridge.hermes_client import HermesGateway, HermesRefused
from hal0.bridge.store import TERMINAL_STATUSES, BridgeStore, RunRow
from hal0.config.schema import BridgeConfig

if TYPE_CHECKING:
    from hal0.events import EventBus
    from hal0.mcp.approval_queue import ApprovalQueue

__all__ = [
    "APPROVAL_TOOL",
    "HERMES_STATUSES",
    "ConfigSource",
    "RunWatcher",
    "WatcherTiming",
    "pending_of",
]

log = structlog.get_logger(__name__)

#: The bridge config, or a callable returning the current one (``[bridge]`` limits
#: other than ``enabled`` apply to the next request, ADR-0043 decision 6).
ConfigSource = BridgeConfig | Callable[[], BridgeConfig]

#: The inbox tool a mirrored run approval is filed under.
APPROVAL_TOOL = "agent_run_approval"

#: Run statuses Hermes reports. The ledger adds ``waiting_for_clarification``.
HERMES_STATUSES: frozenset[str] = frozenset(
    {
        "queued",
        "running",
        "waiting_for_approval",
        "stopping",
        "unknown",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
    }
)

_WAITING = frozenset({"waiting_for_approval", "waiting_for_clarification"})

_TERMINAL_EVENTS: dict[str, str] = {
    "run.completed": "completed",
    "run.failed": "failed",
    "run.cancelled": "cancelled",
    "run.interrupted": "interrupted",
}

#: Bus event and severity announcing each terminal status.
_BUS_FOR_STATUS: dict[str, tuple[str, str]] = {
    "completed": ("agents.run_completed", "info"),
    "failed": ("agents.run_failed", "error"),
    "unknown": ("agents.run_failed", "error"),
    "cancelled": ("agents.run_stopped", "warn"),
    "interrupted": ("agents.run_stopped", "warn"),
}

_FORGOTTEN = "Hermes no longer knows this run"
_MAX_ERROR_TEXT = 2000

Decide = Callable[[RunRow], dict[str, Any]]


@dataclass(frozen=True)
class WatcherTiming:
    """The watcher's clocks. Production uses the defaults; tests scale them down.

    ``backoff_s`` is the reconnect delay sequence (the last value repeats); it
    restarts from the first value after a connection that delivered an event.
    ``give_up_s`` is how long Hermes may stay unreachable before the run is
    marked ``unknown``. ``clarify_timeout_s`` overrides
    ``[bridge].clarify_timeout_s`` when set.
    """

    backoff_s: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)
    coalesce_s: float = 2.0
    coalesce_bytes: int = 2048
    give_up_s: float = 1800.0
    clarify_timeout_s: float | None = None


@dataclass
class _Tail:
    """What a watcher holds in memory for one run, beyond the ledger row."""

    run_id: str
    client: str
    agent: str
    profile: str
    last_seq: int
    output: str
    buffer: list[str] = field(default_factory=list)
    buffer_bytes: int = 0
    buffer_from: int = 0
    buffer_to: int = 0
    buffer_started: float = 0.0
    clarify_timer: asyncio.Task[None] | None = None
    done: bool = False

    @classmethod
    def of(cls, row: RunRow) -> _Tail:
        return cls(
            run_id=row.run_id,
            client=row.client,
            agent=row.agent,
            profile=row.profile,
            last_seq=row.last_seq,
            output=row.output or "",
        )

    @property
    def seen(self) -> int:
        """Highest sequence handled, stored or still in the delta buffer."""
        return max(self.last_seq, self.buffer_to)


def pending_of(row: RunRow | None) -> dict[str, Any] | None:
    """The run's pending approval or clarification request, decoded; None when there is none."""
    if row is None or row.pending_request_json is None:
        return None
    try:
        value = json.loads(row.pending_request_json)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _is_pending(row: RunRow, event: str, request_id: str | None = None) -> bool:
    pending = pending_of(row)
    if pending is None or pending.get("event") != event:
        return False
    return request_id is None or pending.get("request_id") == request_id


def _error_text(value: Any) -> str | None:
    """A Hermes error as one line of text: a string, or an object's ``message``."""
    if isinstance(value, str):
        text = value
    elif isinstance(value, Mapping):
        message = value.get("message")
        text = message if isinstance(message, str) else json.dumps(value, default=str)
    else:
        return None
    text = text.strip()
    return text[:_MAX_ERROR_TEXT] or None


def _timestamp(event: Mapping[str, Any]) -> float:
    value = event.get("timestamp")
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return time.time()


def _bus_message(tail: _Tail) -> str:
    """A bus event's message for a run: who delegated to which agent, and no run text.

    ``activity.db`` is world-readable (0644), so a command, a question, the
    task or an error from Hermes stays in the 0600 ledger; the event's ``data``
    carries the run id and status (ADR-0043).
    """
    return f"{tail.client} → {tail.agent}"


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _record_sync(
    store: BridgeStore,
    run_id: str,
    event: tuple[int, float, str, Any] | None,
    decide: Decide,
    *,
    skip_terminal: bool,
) -> tuple[RunRow, RunRow, bool] | None:
    """Write an event (optional) and the row changes ``decide`` picks, in a worker thread.

    One transaction (:meth:`BridgeStore.record`): a crash between the event and
    the row cannot lose a pending request the event announced. Returns
    ``(before, after, changed)``, or None when the run is gone or, with
    ``skip_terminal``, already terminal (nothing is written then).
    """
    return store.record(run_id, event, decide, skip_terminal=skip_terminal)


class RunWatcher:
    """Tails each live run's Hermes event stream into the ledger (module docstring).

    ``approval_queue`` and ``events`` may be None (no inbox mirroring, no bus
    events). ``clock`` is the monotonic clock the coalescing window and the
    give-up window are measured with.
    """

    def __init__(
        self,
        *,
        store: BridgeStore,
        gateway: HermesGateway,
        config: ConfigSource,
        approval_queue: ApprovalQueue | None,
        events: EventBus | None,
        on_change: Callable[[str], None],
        timing: WatcherTiming | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._config = config
        self._queue = approval_queue
        self._events = events
        self._on_change = on_change
        self._timing = timing if timing is not None else WatcherTiming()
        self._clock = clock
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._tails: dict[str, _Tail] = {}
        self._timers: set[asyncio.Task[None]] = set()
        self._stopped = False
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    # -- lifecycle ---------------------------------------------------------------------

    def start(self, row: RunRow) -> None:
        """Start watching a new run; a terminal row, or a run already watched, is a no-op."""
        if self._stopped or row.status in TERMINAL_STATUSES or self._claimed(row.run_id):
            return
        self._spawn(row, _Tail.of(row))

    async def attach(self, row: RunRow) -> bool:
        """Resume watching a run from the ledger (hal0-api start); False when there is nothing to do.

        Unlike :meth:`start`, a pending request is restored before this returns:
        an approval is mirrored into the (in-memory, so empty) inbox again, and a
        clarification gets its timeout re-armed from when it was asked.

        The run is claimed before the first await, so overlapping calls for one run
        start one watcher (the later call returns False), and none starts after
        :meth:`stop_all`.
        """
        if self._stopped or row.status in TERMINAL_STATUSES or self._claimed(row.run_id):
            return False
        tail = _Tail.of(row)
        self._tails[row.run_id] = tail
        spawned = False
        try:
            try:
                await self._resume_pending(tail, row)
            except Exception:
                log.warning("bridge.resume_pending_failed", run_id=row.run_id, exc_info=True)
            if self._stopped:  # stop_all ran while the pending request was restored
                return False
            self._spawn(row, tail)
            spawned = True
            return True
        finally:
            if not spawned and self._tails.get(row.run_id) is tail:
                del self._tails[row.run_id]
                self._cancel_clarify(tail)

    def _claimed(self, run_id: str) -> bool:
        """Whether a watcher task is alive for ``run_id``, or an :meth:`attach` has reserved it."""
        return self.watching(run_id) or run_id in self._tails

    def _spawn(self, row: RunRow, tail: _Tail) -> None:
        self._tails[row.run_id] = tail
        task = asyncio.create_task(self._watch(tail), name=f"bridge-watch:{row.run_id}")
        self._tasks[row.run_id] = task
        task.add_done_callback(functools.partial(self._forget, row.run_id))

    def watching(self, run_id: str) -> bool:
        """Whether a watcher task for ``run_id`` is alive."""
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    def watched(self) -> list[str]:
        """Run ids with a live watcher task."""
        return [run_id for run_id, task in self._tasks.items() if not task.done()]

    async def stop_all(self) -> None:
        """Cancel every watcher and clarify timer, and wait for them to finish.

        A cancelled watcher writes nothing more: deltas still in its buffer were
        never stored, so ``last_seq`` has not passed them and Hermes replays them
        when the run is re-attached. Once stopped, the watcher takes no new runs.
        """
        self._stopped = True
        tasks = [*self._tasks.values(), *self._timers]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._timers.clear()
        self._tails.clear()

    def _forget(self, run_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(run_id) is task:
            del self._tasks[run_id]

    # -- writes shared with the service ------------------------------------------------

    async def update(self, run_id: str, decide: Decide) -> RunRow | None:
        """Apply ``decide(row)`` to a live run under its lock.

        Returns the row after the change, or None when nothing changed: the run
        is gone or terminal, or ``decide`` returned no fields.
        """
        async with self._lock(run_id):
            result = await asyncio.to_thread(
                _record_sync, self._store, run_id, None, decide, skip_terminal=True
            )
        if result is None or not result[2]:
            return None
        self._changed(run_id)
        return result[1]

    async def settle(self, run_id: str, request_id: str) -> RunRow | None:
        """Clear the run's pending request ``request_id`` once it has been answered.

        The status goes back to ``running`` unless the run is stopping. Called
        after a successful approval or clarify post; the ``*.settled`` event
        that follows from Hermes then changes nothing.
        """
        cleared: list[Any] = []

        def decide(row: RunRow) -> dict[str, Any]:
            changes = _clear_pending(request_id, row)
            if changes:
                cleared.append((pending_of(row) or {}).get("event"))
            return changes

        row = await self.update(run_id, decide)
        tail = self._tails.get(run_id)
        if tail is not None and "clarify.request" in cleared:
            self._cancel_clarify(tail)
        return row

    async def stopping(self, run_id: str, status: str = "stopping") -> RunRow | None:
        """Record that a stop was asked of Hermes for a live run.

        The status becomes ``status`` (``stopping``; a ``waiting_for_*`` status is
        derived here and never taken from Hermes). A pending approval or
        clarification is cleared with it: its inbox row is withdrawn and the
        clarify timer cancelled, or the timer would later overwrite the run's
        ``error`` and post a second stop. The terminal event that follows finishes
        the run.
        """
        if status in _WAITING:
            status = "stopping"
        cleared: list[RunRow] = []

        def decide(row: RunRow) -> dict[str, Any]:
            changes: dict[str, Any] = {}
            if row.status != status:
                changes["status"] = status
            if row.pending_request_json is not None:
                changes["pending_request_json"] = None
                cleared.append(row)
            return changes

        tail = self._tails.get(run_id)
        row = await self.update(run_id, decide)
        if tail is not None:
            self._cancel_clarify(tail)
        for before in cleared:
            await self._withdraw(before, "the run was stopped")
        return row

    async def mark_unknown(self, row: RunRow, reason: str) -> None:
        """Record that Hermes no longer knows ``row``'s run (status ``unknown``)."""
        await self._finish(_Tail.of(row), "unknown", error=reason)

    # -- the watch loop ----------------------------------------------------------------

    async def _watch(self, tail: _Tail) -> None:
        try:
            await self._follow(tail)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Only a failure to record the end of a watch gets here (_follow
            # retries everything else); the run is re-attached at the next start.
            log.exception("bridge.watcher_failed", run_id=tail.run_id)
        finally:
            self._cancel_clarify(tail)
            if self._tails.get(tail.run_id) is tail:
                del self._tails[tail.run_id]

    async def _follow(self, tail: _Tail) -> None:
        attempt = 0
        last_contact = self._clock()
        while True:
            progressed = False
            try:
                stream = cast(
                    AsyncGenerator[dict[str, Any], None],
                    self._gateway.events(tail.profile, tail.run_id, last_seq=tail.last_seq),
                )
                async with contextlib.aclosing(stream):
                    async for event in stream:
                        last_contact = self._clock()
                        progressed = await self._on_event(tail, event) or progressed
                        if tail.done:
                            return
                await self._flush(tail)
                last_contact = self._clock()
                if not progressed and await self._finished_in_snapshot(tail):
                    return
            except HermesRefused as exc:
                await self._flush(tail)
                if exc.hermes_status == 404:
                    await self._finish(tail, "unknown", error=_FORGOTTEN)
                    return
                log.warning("bridge.watcher_refused", run_id=tail.run_id, error=exc.message)
            except BridgeError as exc:
                if exc.code == "bridge.run_not_found":  # the ledger row is gone (pruned)
                    log.warning("bridge.watcher_run_gone", run_id=tail.run_id)
                    return
                # HermesUnavailable, or the profile's key is not mirrored (yet).
                await self._flush(tail)
                log.info("bridge.watcher_reconnecting", run_id=tail.run_id, error=exc.message)
            except Exception:
                # A transient ledger failure (SQLite busy): retry like a dropped
                # stream rather than leave the run unwatched until a restart.
                log.exception("bridge.watcher_cycle_failed", run_id=tail.run_id)
            if self._clock() - last_contact >= self._timing.give_up_s:
                await self._finish(
                    tail,
                    "unknown",
                    error=f"lost contact with Hermes for {self._timing.give_up_s:g}s",
                )
                return
            if progressed:  # the connection worked: the sequence starts over from its first delay
                attempt = 0
            backoff = self._timing.backoff_s
            delay = backoff[min(attempt, len(backoff) - 1)]
            attempt += 1
            await asyncio.sleep(delay)

    async def _finished_in_snapshot(self, tail: _Tail) -> bool:
        """After a quiet end with nothing new, ask Hermes whether the run is over.

        Covers a terminal event Hermes can no longer replay. Returns True when the
        run was finished from the snapshot.
        """
        try:
            snapshot = await self._gateway.get_run(tail.profile, tail.run_id)
        except HermesRefused as exc:
            if exc.hermes_status == 404:
                await self._finish(tail, "unknown", error=_FORGOTTEN)
                return True
            return False
        except BridgeError:
            return False
        status = snapshot.get("status")
        if status == "unknown":
            await self._finish(tail, "unknown", error="Hermes reports the run's state as unknown")
            return True
        if status not in _TERMINAL_EVENTS.values():
            return False
        output = snapshot.get("output")
        await self._finish(
            tail,
            str(status),
            output=output if isinstance(output, str) else None,
            usage=snapshot.get("usage"),
            error=_error_text(snapshot.get("error")),
        )
        return True

    # -- events ------------------------------------------------------------------------

    async def _on_event(self, tail: _Tail, event: dict[str, Any]) -> bool:
        """Handle one frame; returns False for a frame already handled (a replay)."""
        seq = _int_or_none(event.get("seq"))
        if seq is None or seq <= tail.seen:
            return False
        raw_name = event.get("event")
        name = raw_name if isinstance(raw_name, str) and raw_name else "unknown"
        if name == "message.delta":
            await self._buffer_delta(tail, seq, event.get("delta"))
            return True
        await self._flush(tail)
        ts = _timestamp(event)
        if name in _TERMINAL_EVENTS:
            output = event.get("output")
            await self._finish(
                tail,
                _TERMINAL_EVENTS[name],
                event=(seq, ts, name, event),
                output=output if isinstance(output, str) else None,
                usage=event.get("usage"),
                error=_error_text(event.get("error")),
            )
        elif name == "approval.request":
            await self._approval_requested(tail, seq, ts, event)
        elif name == "clarify.request":
            await self._clarify_requested(tail, seq, ts, event)
        else:
            await self._other_event(tail, seq, ts, name, event)
        return True

    async def _buffer_delta(self, tail: _Tail, seq: int, delta: Any) -> None:
        text = delta if isinstance(delta, str) else ""
        if not tail.buffer_to or tail.buffer_to <= tail.last_seq:
            tail.buffer_from = seq
            tail.buffer_started = self._clock()
        tail.buffer.append(text)
        tail.buffer_bytes += len(text.encode())
        tail.buffer_to = seq
        timing = self._timing
        if (
            tail.buffer_bytes >= timing.coalesce_bytes
            or self._clock() - tail.buffer_started >= timing.coalesce_s
        ):
            await self._flush(tail)

    async def _flush(self, tail: _Tail) -> None:
        """Store the buffered deltas as one ``message.text`` event and extend ``output``."""
        if tail.buffer_to <= tail.last_seq:
            return
        text = "".join(tail.buffer)
        output = tail.output + text
        seq = tail.buffer_to
        payload = {
            "event": "message.text",
            "run_id": tail.run_id,
            "seq": seq,
            "from_seq": tail.buffer_from,
            "text": text,
        }

        def decide(row: RunRow) -> dict[str, Any]:
            changes: dict[str, Any] = {"output": output}
            if row.status == "queued":
                changes["status"] = "running"
            return changes

        await self._record(tail, (seq, time.time(), "message.text", payload), decide)
        tail.output = output
        tail.last_seq = seq
        tail.buffer = []
        tail.buffer_bytes = 0
        tail.buffer_to = 0
        self._changed(tail.run_id)

    async def _other_event(
        self, tail: _Tail, seq: int, ts: float, name: str, event: dict[str, Any]
    ) -> None:
        request_id = event.get("request_id")
        settled_id = request_id if isinstance(request_id, str) else None

        def decide(row: RunRow) -> dict[str, Any]:
            if name == "run.queued":
                return {"status": "queued"} if row.status != "queued" else {}
            if name == "run.stopping":
                return {"status": "stopping"} if row.status != "stopping" else {}
            if name in ("approval.settled", "clarify.settled"):
                asked = "approval.request" if name == "approval.settled" else "clarify.request"
                if _is_pending(row, asked, settled_id):
                    return _clear_pending(None, row)
                return {}
            return {"status": "running"} if row.status == "queued" else {}

        before, _after = await self._record(tail, (seq, ts, name, event), decide)
        tail.last_seq = seq
        if name == "approval.settled" and _is_pending(before, "approval.request", settled_id):
            await self._withdraw(before, "settled in Hermes before a decision here")
        if name == "clarify.settled" and _is_pending(before, "clarify.request", settled_id):
            self._cancel_clarify(tail)
        self._changed(tail.run_id)

    async def _approval_requested(
        self, tail: _Tail, seq: int, ts: float, event: dict[str, Any]
    ) -> None:
        """Record the request in the ledger, then file it in the inbox (ADR-0043 decision 9).

        The ledger comes first, and the whole step holds the run's lock: an inbox
        row can be decided the moment it exists, and the decision must find the
        request pending (and wait for this step to finish).
        """
        pending = {**event, "received_at": time.time()}
        generation = _int_or_none(event.get("execution_generation"))
        request_id = event.get("request_id")

        def decide(row: RunRow) -> dict[str, Any]:
            changes: dict[str, Any] = {
                "status": "waiting_for_approval",
                "pending_request_json": json.dumps(pending),
            }
            if generation is not None:
                changes["execution_generation"] = generation
            return changes

        async with self._lock(tail.run_id):
            current = await asyncio.to_thread(self._store.get_run, tail.run_id)
            prior = pending_of(current)
            if (
                current is not None
                and prior is not None
                and prior.get("event") == "approval.request"
                and prior.get("request_id") != request_id
            ):
                # Retire the older row first: the inbox keeps one row per run, so
                # a pending stale row would swallow this request.
                await self._withdraw(current, "superseded by a newer approval request")
            if prior is not None and prior.get("event") == "clarify.request":
                self._cancel_clarify(tail)  # Hermes moved on past the question
            result = await asyncio.to_thread(
                _record_sync,
                self._store,
                tail.run_id,
                (seq, ts, "approval.request", event),
                decide,
                skip_terminal=False,
            )
            if result is None:  # the ledger row is gone (pruned): stop watching
                raise BridgeError("bridge.run_not_found", f"no run {tail.run_id!r}")
            tail.last_seq = seq
            await self._file_in_inbox(tail, pending)
        await self._emit(
            tail,
            "agents.approval_requested",
            "warn",
            _bus_message(tail),
            "waiting_for_approval",
        )
        self._changed(tail.run_id)

    async def _clarify_requested(
        self, tail: _Tail, seq: int, ts: float, event: dict[str, Any]
    ) -> None:
        pending = {**event, "received_at": time.time()}
        generation = _int_or_none(event.get("execution_generation"))

        def decide(row: RunRow) -> dict[str, Any]:
            changes: dict[str, Any] = {
                "status": "waiting_for_clarification",
                "pending_request_json": json.dumps(pending),
            }
            if generation is not None:
                changes["execution_generation"] = generation
            return changes

        before, _after = await self._record(tail, (seq, ts, "clarify.request", event), decide)
        tail.last_seq = seq
        if _is_pending(before, "approval.request"):
            await self._withdraw(before, "superseded by a clarification request")
        request_id = event.get("request_id")
        if isinstance(request_id, str):
            self._arm_clarify(tail, request_id, self._clarify_timeout())
        await self._emit(
            tail,
            "agents.clarification_requested",
            "warn",
            _bus_message(tail),
            "waiting_for_clarification",
        )
        self._changed(tail.run_id)

    async def _finish(
        self,
        tail: _Tail,
        status: str,
        *,
        event: tuple[int, float, str, Any] | None = None,
        output: str | None = None,
        usage: Any = None,
        error: str | None = None,
    ) -> None:
        """Record a terminal status with the run's output, usage and error."""
        self._cancel_clarify(tail)  # before any await: a timer must not stop a run that ended
        await self._flush(tail)

        def decide(row: RunRow) -> dict[str, Any]:
            changes: dict[str, Any] = {"status": status, "pending_request_json": None}
            final = output if output is not None else (tail.output or row.output)
            if final is not None:
                changes["output"] = final
            if usage is not None:
                changes["usage_json"] = json.dumps(usage)
            # Keep an error hal0 recorded first (the clarify timeout) if Hermes adds none.
            changes["error"] = error or row.error
            return changes

        async with self._lock(tail.run_id):
            result = await asyncio.to_thread(
                _record_sync, self._store, tail.run_id, event, decide, skip_terminal=True
            )
        tail.done = True
        self._cancel_clarify(tail)
        if event is not None:
            tail.last_seq = max(tail.last_seq, event[0])
        if result is None:
            return
        before, _after, _changed = result
        if _is_pending(before, "approval.request"):
            await self._withdraw(before, f"the run ended ({status})")
        bus_type, severity = _BUS_FOR_STATUS[status]
        await self._emit(tail, bus_type, severity, f"{_bus_message(tail)}: {status}", status)
        self._changed(tail.run_id)

    # -- approvals ---------------------------------------------------------------------

    async def _resume_pending(self, tail: _Tail, row: RunRow) -> None:
        """On (re)attach, mirror a pending approval again and re-arm a clarify timeout.

        The approval inbox is in memory, so after a restart a run waiting for
        approval has no inbox row until it is mirrored again.
        """
        pending = pending_of(row)
        if pending is None:
            return
        request_id = pending.get("request_id")
        if not isinstance(request_id, str):
            return
        if pending.get("event") == "approval.request" and self._queue is not None:
            async with self._lock(tail.run_id):
                await self._file_in_inbox(tail, pending)
            self._changed(tail.run_id)
        elif pending.get("event") == "clarify.request":
            received = pending.get("received_at")
            asked_at = float(received) if isinstance(received, int | float) else time.time()
            timeout = self._clarify_timeout()
            self._arm_clarify(tail, request_id, max(0.0, timeout - (time.time() - asked_at)))

    async def _mirror(self, tail: _Tail, pending: Mapping[str, Any]) -> str | None:
        """File the run's approval request in the inbox; returns the approval id."""
        request_id = pending.get("request_id")
        if self._queue is None or not isinstance(request_id, str):
            return None
        run_id, profile = tail.run_id, tail.profile
        generation = _int_or_none(pending.get("execution_generation"))

        async def approve(_args: dict[str, Any]) -> Any:
            return await self._answer_approval(run_id, profile, request_id, generation, "once")

        async def deny(_args: dict[str, Any]) -> Any:
            return await self._answer_approval(run_id, profile, request_id, generation, "deny")

        args = {
            "run_id": run_id,
            "request_id": request_id,
            "command": pending.get("command"),
            "description": pending.get("description"),
            "pattern_key": pending.get("pattern_key"),
        }
        return await self._queue.enqueue(
            APPROVAL_TOOL,
            args,
            client_id=f"bridge:{tail.client}",
            executor=approve,
            target_args=("run_id",),
            on_deny=deny,
        )

    async def _file_in_inbox(self, tail: _Tail, pending: Mapping[str, Any]) -> None:
        """Mirror ``pending`` into the inbox and record the row's id in the ledger.

        The caller holds the run's lock. A failure is logged, not raised: the
        request is already in the ledger, and the next :meth:`attach` files it.
        """
        request_id = pending.get("request_id")
        try:
            approval_id = await self._mirror(tail, pending)
            if approval_id is None or not isinstance(request_id, str):
                return
            await self._store_approval_id(tail.run_id, request_id, approval_id)
        except Exception:
            log.warning("bridge.approval_mirror_failed", run_id=tail.run_id, exc_info=True)

    async def _store_approval_id(self, run_id: str, request_id: str, approval_id: str) -> None:
        """Put the inbox row's id into the run's pending request; the caller holds the lock."""

        def decide(row: RunRow) -> dict[str, Any]:
            pending = pending_of(row)
            if (
                pending is None
                or pending.get("event") != "approval.request"
                or pending.get("request_id") != request_id
                or pending.get("approval_id") == approval_id
            ):
                return {}
            return {"pending_request_json": json.dumps({**pending, "approval_id": approval_id})}

        await asyncio.to_thread(_record_sync, self._store, run_id, None, decide, skip_terminal=True)

    async def _answer_approval(
        self, run_id: str, profile: str, request_id: str, generation: int | None, choice: str
    ) -> dict[str, Any]:
        """Post the operator's inbox decision to Hermes, if the request is still pending.

        The inbox grants ``once`` only; ``session`` and ``always`` are Hermes-side
        allowlist decisions (ADR-0043 decision 9). The ledger is read under the
        run's lock, so a decision made the moment the row appeared waits for the
        request to be recorded.

        If the post fails for any reason but Hermes saying the request is no longer
        pending, the request goes back in the inbox (the queue marks this decision
        failed or denied, and the run is still waiting).
        """
        async with self._lock(run_id):
            row = await asyncio.to_thread(self._store.get_run, run_id)
        if (
            row is None
            or row.status in TERMINAL_STATUSES
            or not _is_pending(row, "approval.request", request_id)
        ):
            raise BridgeError(
                "bridge.no_pending_request",
                f"run {run_id!r} is no longer waiting for approval {request_id!r}",
            )
        execution_generation = generation if generation is not None else row.execution_generation
        if execution_generation is None:
            raise BridgeError(
                "bridge.no_pending_request",
                f"approval {request_id!r} of run {run_id!r} carries no execution generation",
            )
        try:
            result = await self._gateway.approval(
                profile,
                run_id,
                request_id=request_id,
                execution_generation=execution_generation,
                choice=choice,
            )
        except asyncio.CancelledError:
            # The decision was abandoned part-way; Hermes may or may not have it.
            await asyncio.shield(self._refile(run_id, request_id))
            raise
        except Exception as exc:
            if not _no_longer_pending(exc):
                await self._refile(run_id, request_id)
            raise
        await self.settle(run_id, request_id)
        return result

    async def _refile(self, run_id: str, request_id: str) -> None:
        """Put a run's still-pending approval back in the inbox after a failed decision.

        A no-op when the request is no longer pending in the ledger. The inbox
        keeps one row per run, so the failed decision's entry (already out of the
        queue's dedup index) is replaced, not joined, by the new one.
        """
        try:
            async with self._lock(run_id):
                row = await asyncio.to_thread(self._store.get_run, run_id)
                if (
                    row is None
                    or row.status in TERMINAL_STATUSES
                    or not _is_pending(row, "approval.request", request_id)
                ):
                    return
                await self._file_in_inbox(_Tail.of(row), pending_of(row) or {})
        except Exception:
            log.warning("bridge.approval_refile_failed", run_id=run_id, exc_info=True)
            return
        self._changed(run_id)

    async def _withdraw(self, row: RunRow, reason: str) -> None:
        """Retire the inbox row of ``row``'s pending approval, if it is still pending."""
        pending = pending_of(row)
        approval_id = None if pending is None else pending.get("approval_id")
        if self._queue is None or not isinstance(approval_id, str):
            return
        try:
            await self._queue.withdraw(approval_id, reason)
        except Exception:
            log.warning("bridge.approval_withdraw_failed", run_id=row.run_id, exc_info=True)

    # -- clarification -----------------------------------------------------------------

    def _clarify_timeout(self) -> float:
        if self._timing.clarify_timeout_s is not None:
            return self._timing.clarify_timeout_s
        return float(_config(self._config).clarify_timeout_s)

    def _arm_clarify(self, tail: _Tail, request_id: str, delay: float) -> None:
        self._cancel_clarify(tail)
        if self._stopped:
            return
        task = asyncio.create_task(
            self._clarify_deadline(tail, request_id, delay),
            name=f"bridge-clarify:{tail.run_id}",
        )
        tail.clarify_timer = task
        self._timers.add(task)
        task.add_done_callback(self._timers.discard)

    def _cancel_clarify(self, tail: _Tail) -> None:
        timer, tail.clarify_timer = tail.clarify_timer, None
        if timer is not None and not timer.done():
            timer.cancel()

    async def _clarify_deadline(self, tail: _Tail, request_id: str, delay: float) -> None:
        """Stop a run whose clarifying question went unanswered for the timeout."""
        await asyncio.sleep(delay)
        tail.clarify_timer = None  # fired: an answer arriving now must not cancel the stop
        error = f"clarification unanswered after {self._clarify_timeout():g}s"
        # The error is written before the stop is posted, so the terminal event
        # that the stop produces finds it and keeps it.
        changed = await self.update(
            tail.run_id,
            lambda row: (
                {"pending_request_json": None, "error": error, "status": "stopping"}
                if _is_pending(row, "clarify.request", request_id)
                else {}
            ),
        )
        if changed is None:
            return  # answered meanwhile, or the run ended
        log.info("bridge.clarify_timeout", run_id=tail.run_id, request_id=request_id)
        try:
            await self._gateway.stop(tail.profile, tail.run_id)
        except BridgeError as exc:
            log.warning("bridge.clarify_stop_failed", run_id=tail.run_id, error=exc.message)

    # -- plumbing ----------------------------------------------------------------------

    def _lock(self, run_id: str) -> asyncio.Lock:
        lock = self._locks.get(run_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[run_id] = lock
        return lock

    async def _record(
        self, tail: _Tail, event: tuple[int, float, str, Any], decide: Decide
    ) -> tuple[RunRow, RunRow]:
        """Write ``event`` and ``decide``'s changes under the run's lock.

        The caller calls :meth:`_changed` once its side effects (inbox, bus) are
        done, so whoever is woken sees them.
        """
        async with self._lock(tail.run_id):
            result = await asyncio.to_thread(
                _record_sync, self._store, tail.run_id, event, decide, skip_terminal=False
            )
        if result is None:  # the ledger row is gone (pruned): stop watching
            raise BridgeError("bridge.run_not_found", f"no run {tail.run_id!r}")
        return result[0], result[1]

    async def _emit(
        self, tail: _Tail, type_: str, severity: str, message: str, status: str
    ) -> None:
        if self._events is None:
            return
        data = {"run_id": tail.run_id, "agent": tail.agent, "client": tail.client, "status": status}
        try:
            await self._events.emit(type_, severity, f"bridge:{tail.client}", message, data=data)
        except Exception:
            log.warning("bridge.event_emit_failed", run_id=tail.run_id, exc_info=True)

    def _changed(self, run_id: str) -> None:
        try:
            self._on_change(run_id)
        except Exception:
            log.warning("bridge.on_change_failed", run_id=run_id, exc_info=True)


def _clear_pending(request_id: str | None, row: RunRow) -> dict[str, Any]:
    """Row changes that clear a pending request (``request_id`` None: whichever is pending)."""
    pending = pending_of(row)
    if pending is None or (request_id is not None and pending.get("request_id") != request_id):
        return {}
    changes: dict[str, Any] = {"pending_request_json": None}
    if row.status in _WAITING:
        changes["status"] = "running"
    return changes


def _no_longer_pending(exc: BaseException) -> bool:
    """Whether ``exc`` is Hermes saying the approval it was asked about is gone.

    That is a final answer (409 ``approval_not_pending``, or ``stale_generation``
    for a run that moved on); anything else may succeed on another try.
    """
    return (
        isinstance(exc, HermesRefused)
        and exc.hermes_status == 409
        and exc.hermes_code in ("approval_not_pending", "stale_generation")
    )


def _config(source: ConfigSource) -> BridgeConfig:
    return source if isinstance(source, BridgeConfig) else source()
