"""RunWatcher: tails a delegated run's Hermes event stream into the ledger (ADR-0043 decision 9).

One task per live run follows ``/v1/runs/<id>/events`` with ``Last-Event-ID``,
writes each event to ``bridge.db``, coalesces ``message.delta`` into
``message.text`` chunks, and reconnects after a quiet end or a dropped stream.
These tests run it against ``FakeHermes`` with the timings scaled down, and wait
on the watcher's own change callback rather than sleeping.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from hal0.bridge.hermes_client import HermesGateway
from hal0.bridge.store import BridgeStore, RunRow
from hal0.bridge.watcher import RunWatcher, WatcherTiming
from hal0.config.schema import BridgeConfig
from hal0.events import EventBus
from hal0.mcp.approval_queue import ApprovalQueue
from tests.bridge._fake_hermes import FakeHermes, sse_frame

FAST = WatcherTiming(backoff_s=(0.01,), coalesce_s=2.0, coalesce_bytes=2048)

Handler = Callable[[httpx.Request], httpx.Response]


class Changes:
    """The watcher's ``on_change`` callback, plus a way to wait for a condition."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self._event = asyncio.Event()

    def __call__(self, run_id: str) -> None:
        self.seen.append(run_id)
        self._event.set()

    async def until(self, cond: Callable[[], bool], timeout: float = 3.0) -> None:
        async def loop() -> None:
            while not cond():
                self._event.clear()
                await self._event.wait()

        await asyncio.wait_for(loop(), timeout)


class Hooked:
    """A FakeHermes handler with a hook that runs before each events request.

    The hook gets the 1-based number of the events request and may return a
    response to serve instead of the fake's.
    """

    def __init__(
        self,
        fake: FakeHermes,
        before_events: Callable[[int, httpx.Request], httpx.Response | None],
    ) -> None:
        self.fake = fake
        self.before_events = before_events
        self.events_requests = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/events"):
            self.events_requests += 1
            replacement = self.before_events(self.events_requests, request)
            if replacement is not None:
                self.fake.calls.append(request)
                self.fake.resume_headers.append(request.headers.get("last-event-id"))
                return replacement
        return self.fake(request)


class _Stream(httpx.AsyncByteStream):
    def __init__(self, inner: AsyncIterator[bytes]) -> None:
        self._inner = inner

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._inner:
            yield chunk


def _keepalives_only(count: int, gap_s: float) -> httpx.Response:
    """A stream that stays open for ``count * gap_s`` seconds and carries only keepalives."""

    async def body() -> AsyncIterator[bytes]:
        yield b": open\n\n"
        for _ in range(count):
            await asyncio.sleep(gap_s)
            yield b": keepalive\n\n"

    return httpx.Response(
        200, headers={"content-type": "text/event-stream"}, stream=_Stream(body())
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[BridgeStore]:
    opened = BridgeStore(tmp_path / "bridge.db")
    opened.open()
    yield opened


@pytest.fixture
def fake() -> FakeHermes:
    return FakeHermes()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def changes() -> Changes:
    return Changes()


def _row(run_id: str, *, client: str = "cc", last_seq: int = 0, **over: Any) -> RunRow:
    fields: dict[str, Any] = {
        "run_id": run_id,
        "client": client,
        "agent": "hermes",
        "profile": "default",
        "session_key": None,
        "status": "running",
        "created_at": 100.0,
        "updated_at": 100.0,
        "input_preview": "ping",
        "output": None,
        "error": None,
        "usage_json": None,
        "admission_id": None,
        "execution_generation": None,
        "pending_request_json": None,
        "last_seq": last_seq,
    }
    fields.update(over)
    return RunRow(**fields)


def _watcher(
    store: BridgeStore,
    fake: FakeHermes,
    tmp_path: Path,
    changes: Changes,
    *,
    handler: Handler | None = None,
    bus: EventBus | None = None,
    timing: WatcherTiming = FAST,
    clock: Callable[[], float] | None = None,
    queue: ApprovalQueue | None = None,
) -> RunWatcher:
    gateway = HermesGateway(
        home=tmp_path,
        environ=fake.environ(),
        transport=httpx.MockTransport(handler if handler is not None else fake),
    )
    kwargs: dict[str, Any] = {} if clock is None else {"clock": clock}
    return RunWatcher(
        store=store,
        gateway=gateway,
        config=BridgeConfig(),
        approval_queue=queue,
        events=bus,
        on_change=changes,
        timing=timing,
        **kwargs,
    )


def _completed(seq: int, rid: str, **fields: Any) -> tuple[int, str]:
    return (seq, sse_frame(seq, "run.completed", rid, completed=True, **fields))


async def _bus_event(bus: EventBus, type_: str, timeout: float = 3.0) -> dict[str, Any]:
    """The first bus event of ``type_``, waiting for it if it has not been emitted yet."""
    async with bus.subscribe() as q:
        for event in list(bus.ring):
            if event["type"] == type_:
                return event

        async def next_match() -> dict[str, Any]:
            while True:
                event = await q.get()
                if event["type"] == type_:
                    return event

        return await asyncio.wait_for(next_match(), timeout)


def _status(store: BridgeStore, run_id: str) -> str:
    row = store.get_run(run_id)
    assert row is not None
    return row.status


async def test_sixty_second_silence_is_not_failure(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, changes: Changes
) -> None:
    # Two connections carry only keepalives (the local model can take 40 s to its
    # first token); the third carries the run. Silence is not a failure.
    rid = fake.seed_run("run_silent", statuses=("running",), frames=[_completed(1, "run_silent")])
    handler = Hooked(fake, lambda n, _req: _keepalives_only(3, 0.02) if n <= 2 else None)
    store.insert_run(_row(rid))
    watcher = _watcher(store, fake, tmp_path, changes, handler=handler)
    try:
        watcher.start(_row(rid))
        await changes.until(lambda: _status(store, rid) == "completed")
    finally:
        await watcher.stop_all()
    row = store.get_run(rid)
    assert row is not None and row.error is None
    assert fake.resume_headers == ["0", "0", "0"]


async def test_reconnect_with_last_event_id(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, changes: Changes
) -> None:
    rid = "run_drop"
    frames = [
        (1, sse_frame(1, "tool.started", rid, tool="terminal")),
        (2, sse_frame(2, "tool.completed", rid, tool="terminal")),
        (3, sse_frame(3, "message.delta", rid, delta="done")),
        _completed(4, rid, output="done"),
    ]
    fake.seed_run(rid, statuses=("running",), frames=frames)
    fake.script_events(rid, frames[:2], then_error=True)

    def before(n: int, _req: httpx.Request) -> None:
        if n == 2:
            fake.script_events(rid, frames)

    store.insert_run(_row(rid))
    watcher = _watcher(store, fake, tmp_path, changes, handler=Hooked(fake, before))
    try:
        watcher.start(_row(rid))
        await changes.until(lambda: _status(store, rid) == "completed")
    finally:
        await watcher.stop_all()
    assert fake.resume_headers == ["0", "2"]
    assert [e["seq"] for e in store.events(rid, since_seq=0, limit=100)] == [1, 2, 3, 4]
    row = store.get_run(rid)
    assert row is not None and row.output == "done" and row.last_seq == 4


async def test_delta_coalesced_and_cap(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, changes: Changes
) -> None:
    rid = "run_deltas"
    deltas = [f"chunk-{i:04d}-" + "x" * 20 for i in range(300)]  # ~9 KiB in all
    frames = [
        (i + 1, sse_frame(i + 1, "message.delta", rid, delta=d)) for i, d in enumerate(deltas)
    ]
    frames.append(_completed(len(deltas) + 1, rid))  # no output field: the deltas are the text
    fake.seed_run(rid, statuses=("running",), frames=frames)
    store.insert_run(_row(rid))
    watcher = _watcher(store, fake, tmp_path, changes)
    try:
        watcher.start(_row(rid))
        await changes.until(lambda: _status(store, rid) == "completed")
    finally:
        await watcher.stop_all()
    evs = store.events(rid, since_seq=0, limit=1000)
    assert all(e["event"] != "message.delta" for e in evs)
    texts = [e for e in evs if e["event"] == "message.text"]
    assert len(texts) >= 4  # 2 KiB per chunk, not one row per delta and not one row in all
    assert all(len(e["payload"]["text"].encode()) < 2048 + 64 for e in texts)
    assert "".join(e["payload"]["text"] for e in texts) == "".join(deltas)
    assert evs[-1]["event"] == "run.completed"
    row = store.get_run(rid)
    assert row is not None
    assert row.output == "".join(deltas)
    assert row.last_seq == len(deltas) + 1


async def test_delta_flushed_once_the_window_has_passed(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, changes: Changes
) -> None:
    rid = "run_window"
    frames = [(i, sse_frame(i, "message.delta", rid, delta=f"d{i}")) for i in (1, 2, 3)]
    frames.append(_completed(4, rid))
    fake.seed_run(rid, statuses=("running",), frames=frames)
    store.insert_run(_row(rid))
    ticks = itertools.count(0, 5)  # every clock read is 5 s after the last: past the 2 s window
    watcher = _watcher(store, fake, tmp_path, changes, clock=lambda: float(next(ticks)))
    try:
        watcher.start(_row(rid))
        await changes.until(lambda: _status(store, rid) == "completed")
    finally:
        await watcher.stop_all()
    texts = [e for e in store.events(rid, since_seq=0, limit=100) if e["event"] == "message.text"]
    assert len(texts) >= 2
    assert "".join(e["payload"]["text"] for e in texts) == "d1d2d3"


async def test_terminal_output_wins_over_deltas_and_usage_is_kept(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, changes: Changes, bus: EventBus
) -> None:
    rid = "run_final"
    frames = [
        (1, sse_frame(1, "message.delta", rid, delta="draft")),
        _completed(2, rid, output="final answer", usage={"total_tokens": 9}),
    ]
    fake.seed_run(rid, statuses=("running",), frames=frames)
    store.insert_run(_row(rid))
    watcher = _watcher(store, fake, tmp_path, changes, bus=bus)
    try:
        watcher.start(_row(rid))
        done = await _bus_event(bus, "agents.run_completed")
    finally:
        await watcher.stop_all()
    row = store.get_run(rid)
    assert row is not None
    assert row.output == "final answer"
    assert row.as_dict()["usage"] == {"total_tokens": 9}
    assert done["severity"] == "info" and done["source"] == "bridge:cc"
    assert done["data"] == {"run_id": rid, "agent": "hermes", "client": "cc", "status": "completed"}


async def test_failed_run_records_error_and_emits_run_failed(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, changes: Changes, bus: EventBus
) -> None:
    rid = "run_failed"
    frames = [(1, sse_frame(1, "run.failed", rid, error={"message": "model crashed"}))]
    fake.seed_run(rid, statuses=("running",), frames=frames)
    store.insert_run(_row(rid))
    watcher = _watcher(store, fake, tmp_path, changes, bus=bus)
    try:
        watcher.start(_row(rid))
        failed = await _bus_event(bus, "agents.run_failed")
    finally:
        await watcher.stop_all()
    row = store.get_run(rid)
    assert row is not None and row.status == "failed" and row.error == "model crashed"
    assert failed["severity"] == "error"
    # No run text on the bus: activity.db is 0644, the error stays in the 0600 ledger.
    assert failed["message"] == "cc → hermes: failed"
    assert failed["data"]["run_id"] == rid


async def test_cancelled_run_emits_run_stopped(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, changes: Changes, bus: EventBus
) -> None:
    rid = "run_cancel"
    frames = [
        (1, sse_frame(1, "run.stopping", rid)),
        (2, sse_frame(2, "run.cancelled", rid)),
    ]
    fake.seed_run(rid, statuses=("running",), frames=frames)
    store.insert_run(_row(rid))
    watcher = _watcher(store, fake, tmp_path, changes, bus=bus)
    try:
        watcher.start(_row(rid))
        stopped = await _bus_event(bus, "agents.run_stopped")
    finally:
        await watcher.stop_all()
    assert _status(store, rid) == "cancelled"
    assert stopped["severity"] == "warn" and stopped["data"]["status"] == "cancelled"


async def test_run_queued_then_work_moves_to_running(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, changes: Changes
) -> None:
    rid = "run_queue"
    frames = [
        (1, sse_frame(1, "run.queued", rid)),
        (2, sse_frame(2, "tool.started", rid, tool="terminal")),
    ]
    fake.seed_run(rid, statuses=("running",), frames=frames)
    store.insert_run(_row(rid))
    statuses: list[str] = []

    def record(run_id: str) -> None:
        statuses.append(_status(store, run_id))
        changes(run_id)

    gateway = HermesGateway(home=tmp_path, environ=fake.environ(), transport=fake.transport())
    watcher = RunWatcher(
        store=store,
        gateway=gateway,
        config=BridgeConfig(),
        approval_queue=None,
        events=None,
        on_change=record,
        timing=FAST,
    )
    try:
        watcher.start(_row(rid))
        await changes.until(lambda: (r := store.get_run(rid)) is not None and r.last_seq == 2)
    finally:
        await watcher.stop_all()
    assert "queued" in statuses
    assert _status(store, rid) == "running"


async def test_hermes_404_marks_unknown_and_stops(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, changes: Changes, bus: EventBus
) -> None:
    rid = "run_forgotten"  # never seeded: Hermes answers 404
    store.insert_run(_row(rid))
    watcher = _watcher(store, fake, tmp_path, changes, bus=bus)
    try:
        watcher.start(_row(rid))
        failed = await _bus_event(bus, "agents.run_failed")
    finally:
        await watcher.stop_all()
    row = store.get_run(rid)
    assert row is not None and row.status == "unknown"
    assert row.error is not None and "no longer knows" in row.error
    assert failed["data"]["status"] == "unknown"
    assert len(fake.requests_to("/events")) == 1  # a 404 is final: no reconnect


async def test_quiet_end_without_progress_checks_the_run_snapshot(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, changes: Changes
) -> None:
    # Hermes no longer has the terminal frame to replay; its run snapshot says done.
    rid = fake.seed_run(
        "run_snapshot", statuses=("completed",), frames=[], output="from snapshot", usage={"n": 1}
    )
    store.insert_run(_row(rid))
    watcher = _watcher(store, fake, tmp_path, changes)
    try:
        watcher.start(_row(rid))
        await changes.until(lambda: _status(store, rid) == "completed")
    finally:
        await watcher.stop_all()
    row = store.get_run(rid)
    assert row is not None and row.output == "from snapshot" and row.as_dict()["usage"] == {"n": 1}


async def test_unreachable_hermes_gives_up_after_the_window(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, changes: Changes
) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    rid = "run_down"
    store.insert_run(_row(rid))
    timing = WatcherTiming(backoff_s=(0.01,), give_up_s=0.05)
    watcher = _watcher(store, fake, tmp_path, changes, handler=down, timing=timing)
    try:
        watcher.start(_row(rid))
        await changes.until(lambda: _status(store, rid) == "unknown")
    finally:
        await watcher.stop_all()
    row = store.get_run(rid)
    assert row is not None and row.error is not None and "lost contact" in row.error


async def test_start_is_idempotent_and_skips_terminal_rows(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, changes: Changes
) -> None:
    rid = fake.seed_run("run_twice", statuses=("running",), frames=[])
    store.insert_run(_row(rid))
    store.insert_run(_row("run_done", status="completed"))
    watcher = _watcher(store, fake, tmp_path, changes)
    try:
        watcher.start(_row(rid))
        watcher.start(_row(rid))
        watcher.start(_row("run_done", status="completed"))
        assert watcher.watching(rid) and not watcher.watching("run_done")
        assert len(watcher.watched()) == 1
    finally:
        await watcher.stop_all()
    assert watcher.watched() == []


async def test_stop_all_cancels_a_watcher_mid_stream(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, changes: Changes
) -> None:
    rid = fake.seed_run("run_long", statuses=("running",), frames=[])
    opened = asyncio.Event()

    def before(_n: int, _req: httpx.Request) -> httpx.Response:
        opened.set()
        return _keepalives_only(1000, 0.05)  # 50 s of keepalives

    store.insert_run(_row(rid))
    watcher = _watcher(store, fake, tmp_path, changes, handler=Hooked(fake, before))
    watcher.start(_row(rid))
    await asyncio.wait_for(opened.wait(), 3)
    await asyncio.wait_for(watcher.stop_all(), 3)
    assert not watcher.watching(rid)
    assert _status(store, rid) == "running"  # a shutdown is not a verdict on the run


async def test_replayed_frames_are_not_written_twice(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, changes: Changes
) -> None:
    # A stream that ignores Last-Event-ID and resends seq 1 must not duplicate it.
    rid = "run_replay"
    frames = [(1, sse_frame(1, "message.delta", rid, delta="once"))]
    fake.seed_run(rid, statuses=("running",), frames=frames)

    def before(n: int, req: httpx.Request) -> httpx.Response | None:
        if n >= 2:
            body = frames[0][1] + sse_frame(2, "run.completed", rid, completed=True)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)
        return None

    store.insert_run(_row(rid))
    watcher = _watcher(store, fake, tmp_path, changes, handler=Hooked(fake, before))
    try:
        watcher.start(_row(rid))
        await changes.until(lambda: _status(store, rid) == "completed")
    finally:
        await watcher.stop_all()
    row = store.get_run(rid)
    assert row is not None and row.output == "once"


async def test_watch_stops_when_the_ledger_row_is_gone(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, changes: Changes
) -> None:
    # The retention pass deleted the row of a run that was still live (an orphan).
    rid = fake.seed_run(
        "run_pruned",
        statuses=("running",),
        frames=[(1, sse_frame(1, "tool.started", "run_pruned"))],
    )
    opened = asyncio.Event()

    def before(_n: int, _req: httpx.Request) -> None:
        opened.set()

    watcher = _watcher(store, fake, tmp_path, changes, handler=Hooked(fake, before))
    watcher.start(_row(rid))  # never inserted into the ledger
    # Hold the task before anything runs: the watch can end (and drop itself from
    # the registry) as soon as it finds no row, which on a loaded runner happened
    # before a lookup by name after the stream opened.
    task = watcher._tasks[rid]
    await asyncio.wait_for(opened.wait(), 3)
    try:
        await asyncio.wait_for(task, 3)  # the watch ends on its own
    finally:
        await watcher.stop_all()
    assert not watcher.watching(rid)
    assert len(fake.requests_to("/events")) == 1


async def test_backoff_restarts_after_a_connection_that_delivered_events(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, changes: Changes
) -> None:
    # refused (delay 0.01), then events and a cut stream, then the rest. The delay after
    # the second connection starts the sequence over; it must not be the 30 s a third
    # consecutive failure would earn.
    rid = "run_backoff"
    frames = [
        (1, sse_frame(1, "tool.started", rid, tool="terminal")),
        _completed(2, rid, output="done"),
    ]
    fake.seed_run(rid, statuses=("running",), frames=frames)
    fake.script_events(rid, frames[:1], then_error=True)

    def before(n: int, _req: httpx.Request) -> httpx.Response | None:
        if n == 1:
            return httpx.Response(503, json={"error": {"code": "busy", "message": "busy"}})
        if n == 3:
            fake.script_events(rid, frames)
        return None

    store.insert_run(_row(rid))
    timing = WatcherTiming(backoff_s=(0.01, 30.0))
    watcher = _watcher(store, fake, tmp_path, changes, handler=Hooked(fake, before), timing=timing)
    try:
        watcher.start(_row(rid))
        await changes.until(lambda: _status(store, rid) == "completed", timeout=3)
    finally:
        await watcher.stop_all()
    assert fake.resume_headers == ["0", "0", "1"]


async def test_pending_row_is_written_before_the_request_is_mirrored(
    store: BridgeStore,
    fake: FakeHermes,
    tmp_path: Path,
    changes: Changes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Whoever the inbox row reaches first (an operator deciding at once) must find
    # the request in the ledger.
    rid = "run_gate"
    frames = [
        (
            1,
            sse_frame(
                1,
                "approval.request",
                rid,
                request_id="req1",
                command="ls",
                execution_generation=1,
            ),
        )
    ]
    fake.seed_run(rid, statuses=("waiting_for_approval",), frames=frames, pending_approval="req1")
    store.insert_run(_row(rid))
    queue = ApprovalQueue()
    at_enqueue: list[RunRow | None] = []
    real_enqueue = queue.enqueue

    async def spy(*args: Any, **kwargs: Any) -> str:
        at_enqueue.append(store.get_run(rid))
        return await real_enqueue(*args, **kwargs)

    monkeypatch.setattr(queue, "enqueue", spy)
    watcher = _watcher(store, fake, tmp_path, changes, queue=queue)
    try:
        watcher.start(_row(rid))
        await changes.until(
            lambda: (
                (r := store.get_run(rid)) is not None
                and r.pending_request_json is not None
                and "approval_id" in r.pending_request_json
            )
        )
    finally:
        await watcher.stop_all()
    [seen] = at_enqueue
    assert seen is not None and seen.status == "waiting_for_approval" and seen.last_seq == 1
    assert seen.pending_request_json is not None
    assert json.loads(seen.pending_request_json)["request_id"] == "req1"
    [entry] = queue.list_pending()
    final = store.get_run(rid)
    assert final is not None and final.pending_request_json is not None
    assert json.loads(final.pending_request_json)["approval_id"] == entry["id"]


async def test_nothing_starts_after_stop_all(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, changes: Changes
) -> None:
    rid = fake.seed_run("run_late", statuses=("running",), frames=[])
    store.insert_run(_row(rid))
    watcher = _watcher(store, fake, tmp_path, changes)
    await watcher.stop_all()
    watcher.start(_row(rid))
    assert await watcher.attach(_row(rid)) is False
    assert watcher.watched() == []
    assert not [t for t in asyncio.all_tasks() if t.get_name() == f"bridge-watch:{rid}"]


async def test_a_cancelled_attach_releases_its_claim_on_the_run(
    store: BridgeStore,
    fake: FakeHermes,
    tmp_path: Path,
    changes: Changes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rid = fake.seed_run("run_claim", statuses=("running",), frames=[])
    pending = {"event": "approval.request", "request_id": "req1", "execution_generation": 1}
    row = _row(rid, status="waiting_for_approval", pending_request_json=json.dumps(pending))
    store.insert_run(row)
    queue = ApprovalQueue()
    real_enqueue = queue.enqueue
    entered = asyncio.Event()

    async def stuck(*args: Any, **kwargs: Any) -> str:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(queue, "enqueue", stuck)
    watcher = _watcher(store, fake, tmp_path, changes, queue=queue)
    first = asyncio.create_task(watcher.attach(row))
    await asyncio.wait_for(entered.wait(), 3)
    # While the first attach is restoring the request, a second one backs off.
    assert await watcher.attach(row) is False
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    monkeypatch.setattr(queue, "enqueue", real_enqueue)
    try:
        assert await watcher.attach(row) is True  # the claim did not outlive the cancel
        assert watcher.watching(rid)
    finally:
        await watcher.stop_all()
