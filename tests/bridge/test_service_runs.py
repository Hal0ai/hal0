"""BridgeService: the delegated-run lifecycle behind the run tools (ADR-0043 decision 9).

The REST routes and the ``hal0-agents`` MCP tools are thin projections of
``BridgeService``; these tests pin its behaviour against ``FakeHermes``: start,
wait, ownership (a foreign run is a 404, never a 403), per-client concurrency,
per-client session and idempotency namespacing, approval mirroring into the
inbox, clarification answered by the delegating client, the clarify timeout,
and re-attaching to live runs after a restart.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from hal0.agents.hermes_api import model_id_for
from hal0.api.auth import AuthPrincipal
from hal0.bridge.errors import BridgeError
from hal0.bridge.hermes_client import HermesGateway
from hal0.bridge.service import BridgeService, Caller
from hal0.bridge.store import TERMINAL_STATUSES, BridgeStore, RunRow
from hal0.bridge.watcher import WatcherTiming
from hal0.config.schema import BridgeConfig
from hal0.events import EventBus
from hal0.mcp.approval_queue import ApprovalQueue
from tests.bridge._fake_hermes import FakeHermes, sse_frame

FAST = WatcherTiming(backoff_s=(0.01,))

Frames = Callable[[str], list[tuple[int, str]]]

VIEW_KEYS = {
    "run_id",
    "agent",
    "profile",
    "client",
    "status",
    "created_at",
    "updated_at",
    "input_preview",
    "output",
    "error",
    "usage",
    "pending",
    "last_seq",
}


# -- scripted Hermes ---------------------------------------------------------------------


class Planner:
    """A FakeHermes handler that scripts each run ``POST /v1/runs`` creates.

    ``plan(...)`` queues a script for the next new run (statuses, SSE frames as a
    function of the run id, and any ``_Run`` attribute). A run started with no
    plan queued stays ``running`` with no events, so it counts as in flight until
    a test scripts more. ``on_stop`` runs after a ``POST .../stop`` with the run
    id, so a test can script what Hermes sends after a stop. ``approval_failures``
    holds ``(status, code)`` answers for the next ``POST .../approval`` calls (the
    post is still recorded in ``fake.calls``), and ``stop_status`` replaces the
    status Hermes's stop answer carries.
    """

    def __init__(self, fake: FakeHermes) -> None:
        self.fake = fake
        self.plans: list[Callable[[str], None]] = []
        self.on_stop: Callable[[str], None] | None = None
        self.approval_failures: list[tuple[int, str]] = []
        self.stop_status: str | None = None

    def plan(
        self,
        *,
        statuses: Sequence[str] = ("running",),
        frames: Frames = lambda rid: [],
        **attrs: Any,
    ) -> None:
        def apply(rid: str) -> None:
            run = self.fake.runs[rid]
            run.statuses = list(statuses)
            run.polls = 0
            run.frames = frames(rid)
            for name, value in attrs.items():
                setattr(run, name, value)

        self.plans.append(apply)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/approval") and self.approval_failures:
            status, code = self.approval_failures.pop(0)
            self.fake.calls.append(request)
            return httpx.Response(status, json={"error": {"code": code, "message": "scripted"}})
        resp = self.fake(request)
        if request.method == "POST" and path.endswith("/v1/runs") and resp.status_code == 202:
            body = json.loads(resp.read())
            if not body["replayed"]:
                if not self.plans:
                    self.plan()
                self.plans.pop(0)(body["run_id"])
        if request.method == "POST" and path.endswith("/stop"):
            if self.on_stop is not None:
                self.on_stop(path.split("/")[-2])
            if self.stop_status is not None:
                body = json.loads(resp.read())
                body["status"] = self.stop_status
                return httpx.Response(resp.status_code, json=body)
        return resp


def _plain(rid: str) -> list[tuple[int, str]]:
    return [
        (1, sse_frame(1, "message.delta", rid, delta="po")),
        (2, sse_frame(2, "tool.started", rid, tool="terminal")),
        (3, sse_frame(3, "message.delta", rid, delta="ng")),
        (
            4,
            sse_frame(
                4, "run.completed", rid, completed=True, output="pong", usage={"total_tokens": 9}
            ),
        ),
    ]


def _approval(rid: str) -> list[tuple[int, str]]:
    return [
        (
            1,
            sse_frame(
                1,
                "approval.request",
                rid,
                request_id="req1",
                command="rm -rf tmp",
                description="delete tmp",
                pattern_key="rm",
                choices=["once", "session", "deny"],
                execution_generation=1,
            ),
        )
    ]


def _clarify(rid: str) -> list[tuple[int, str]]:
    return [
        (
            1,
            sse_frame(
                1,
                "clarify.request",
                rid,
                request_id="clr1",
                question="Which colour?",
                choices=["red", "blue"],
                execution_generation=1,
            ),
        )
    ]


def _then(frames: Frames, *more: tuple[str, dict[str, Any]]) -> Frames:
    """``frames`` followed by more events, numbered on from the last one."""

    def build(rid: str) -> list[tuple[int, str]]:
        out = frames(rid)
        seq = out[-1][0] if out else 0
        for event, fields in more:
            seq += 1
            out.append((seq, sse_frame(seq, event, rid, **fields)))
        return out

    return build


# -- fixtures ----------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Iterator[BridgeStore]:
    opened = BridgeStore(tmp_path / "bridge.db")
    opened.open()
    yield opened


@pytest.fixture
def fake() -> FakeHermes:
    return FakeHermes()


@pytest.fixture
def planner(fake: FakeHermes) -> Planner:
    return Planner(fake)


@pytest.fixture
def queue() -> ApprovalQueue:
    return ApprovalQueue()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def cc() -> Caller:
    return Caller("bridge", "cc")


@pytest.fixture
def other() -> Caller:
    return Caller("bridge", "other")


@pytest.fixture
def admin() -> Caller:
    return Caller("admin", "admin")


SvcFactory = Callable[..., BridgeService]


@pytest.fixture
async def svc_factory(
    store: BridgeStore,
    fake: FakeHermes,
    planner: Planner,
    queue: ApprovalQueue,
    bus: EventBus,
    tmp_path: Path,
) -> AsyncIterator[SvcFactory]:
    built: list[BridgeService] = []

    def make(
        the_store: BridgeStore | None = None,
        *,
        config: BridgeConfig | Callable[[], BridgeConfig] | None = None,
        timing: WatcherTiming = FAST,
        probe: Callable[[str], int] | None = None,
    ) -> BridgeService:
        gateway = HermesGateway(
            home=tmp_path, environ=fake.environ(), transport=httpx.MockTransport(planner)
        )
        svc = BridgeService(
            store=the_store if the_store is not None else store,
            gateway=gateway,
            config=config if config is not None else BridgeConfig(),
            approval_queue=queue,
            events=bus,
            timing=timing,
            probe=probe,
        )
        built.append(svc)
        return svc

    yield make
    for svc in built:
        await svc.close()


@pytest.fixture
def svc(svc_factory: SvcFactory) -> BridgeService:
    return svc_factory()


async def _until(
    svc: BridgeService, store: BridgeStore, run_id: str, cond: Callable[[RunRow], bool]
) -> RunRow:
    """Wait, on the service's change signal, until the run's row satisfies ``cond``."""

    async def loop() -> RunRow:
        while True:
            signal = svc.change_signal(run_id)
            row = store.get_run(run_id)
            assert row is not None
            if cond(row):
                return row
            await signal.wait()

    return await asyncio.wait_for(loop(), 3)


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


def _posts(fake: FakeHermes, suffix: str) -> list[httpx.Request]:
    return [r for r in fake.requests_to(suffix) if r.method == "POST"]


def _body(request: httpx.Request) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(request.content)
    return out


# -- callers -----------------------------------------------------------------------------


def test_caller_from_principal() -> None:
    assert Caller.from_principal(AuthPrincipal(tier="admin")) == Caller("admin", "admin")
    bridge = AuthPrincipal(tier="bridge", name="cc", scopes=frozenset({"agents"}))
    assert Caller.from_principal(bridge) == Caller("bridge", "cc")
    for principal in (AuthPrincipal(tier="client"), AuthPrincipal(tier="anon")):
        with pytest.raises(BridgeError) as e:
            Caller.from_principal(principal)
        assert e.value.code == "auth.forbidden" and e.value.status == 403
    with pytest.raises(BridgeError):
        Caller.from_principal(AuthPrincipal(tier="bridge", name=None))


# -- start and wait ----------------------------------------------------------------------


async def test_start_and_wait_completed(
    svc: BridgeService, planner: Planner, cc: Caller, bus: EventBus
) -> None:
    planner.plan(frames=_plain)
    view = await svc.start_run(cc, agent="hermes", input="Reply pong")
    assert set(view) == VIEW_KEYS
    assert view["status"] in ("started", "queued", "running")
    assert view["client"] == "cc" and view["agent"] == "hermes" and view["profile"] == "default"
    assert view["input_preview"] == "Reply pong"
    done = await svc.wait_run(cc, view["run_id"], timeout_s=5)
    assert set(done) == VIEW_KEYS | {"events_tail", "timed_out"}
    assert done["status"] == "completed" and done["output"] == "pong"
    assert done["timed_out"] is False and done["usage"] == {"total_tokens": 9}
    assert [e["event"] for e in done["events_tail"]][-1] == "run.completed"
    completed = await _bus_event(bus, "agents.run_completed")
    assert completed["data"]["status"] == "completed"
    [started] = [e for e in bus.ring if e["type"] == "agents.run_started"]
    assert started["source"] == "bridge:cc" and started["severity"] == "info"
    assert started["data"]["run_id"] == view["run_id"] and started["data"]["agent"] == "hermes"
    # Who and where, never the task: the bus lands in the world-readable activity.db.
    assert started["message"] == "cc → hermes"
    assert completed["message"] == "cc → hermes: completed"


async def test_input_preview_is_capped(svc: BridgeService, cc: Caller) -> None:
    view = await svc.start_run(cc, agent="hermes", input="y" * 1000)
    assert view["input_preview"] == "y" * 200


async def test_input_preview_is_masked_before_it_is_cut(
    svc: BridgeService, store: BridgeStore, cc: Caller
) -> None:
    """A token the 200-character cut would halve is masked whole, not left half-recognisable."""
    token = "hf_" + "a" * 40
    text = "x" * 190 + " " + token  # the cut falls inside the token
    view = await svc.start_run(cc, agent="hermes", input=text)
    row = store.get_run(view["run_id"])
    assert row is not None
    assert "aaaaaa" not in row.input_preview and "hf_aaa" not in row.input_preview
    assert len(row.input_preview) <= 200


async def test_session_and_idempotency_namespaced(
    svc: BridgeService, fake: FakeHermes, cc: Caller
) -> None:
    view = await svc.start_run(
        cc, agent="hermes/coder", input="x", session_key="s1", idempotency_key="k1"
    )
    [post] = _posts(fake, "/v1/runs")
    assert post.url.path == "/p/coder/v1/runs"
    assert post.headers["x-hermes-session-key"] == "hal0-bridge:cc:s1"
    assert post.headers["idempotency-key"] == "hal0-bridge:cc:k1"
    assert view["profile"] == "coder" and view["agent"] == "hermes/coder"


async def test_admin_runs_are_namespaced_as_admin(
    svc: BridgeService, fake: FakeHermes, admin: Caller
) -> None:
    view = await svc.start_run(admin, agent="hermes", input="x", session_key="s")
    assert view["client"] == "admin"
    assert _posts(fake, "/v1/runs")[-1].headers["x-hermes-session-key"] == "hal0-bridge:admin:s"


@pytest.mark.parametrize("session_key", ["s" * 250, "a\nb", "a b", ""])
async def test_bad_session_key_is_refused_before_posting(
    svc: BridgeService, fake: FakeHermes, cc: Caller, session_key: str
) -> None:
    with pytest.raises(BridgeError) as e:
        await svc.start_run(cc, agent="hermes", input="x", session_key=session_key)
    assert e.value.code == "bridge.invalid_session_key" and e.value.status == 422
    assert _posts(fake, "/v1/runs") == []


async def test_unknown_agent_is_refused(svc: BridgeService, cc: Caller) -> None:
    with pytest.raises(BridgeError) as e:
        await svc.start_run(cc, agent="pi", input="x")
    assert e.value.code == "bridge.unknown_agent"


async def test_disabled_bridge_refuses_to_start(svc_factory: SvcFactory, cc: Caller) -> None:
    svc = svc_factory(config=BridgeConfig(enabled=False))
    with pytest.raises(BridgeError) as e:
        await svc.start_run(cc, agent="hermes", input="x")
    assert e.value.code == "bridge.disabled" and e.value.status == 404


async def test_start_cancelled_after_hermes_accepted_still_records_the_run(
    svc: BridgeService,
    store: BridgeStore,
    cc: Caller,
    bus: EventBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The caller disconnects while the ledger write is in flight: the run Hermes
    # accepted must still be recorded and watched, or it holds a slot unwatched.
    entered, release = threading.Event(), threading.Event()
    real_insert = store.insert_run

    def slow_insert(row: RunRow) -> None:
        entered.set()
        release.wait(3)
        real_insert(row)

    monkeypatch.setattr(store, "insert_run", slow_insert)
    task = asyncio.create_task(svc.start_run(cc, agent="hermes", input="x"))
    assert await asyncio.to_thread(entered.wait, 3)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    started = await _bus_event(bus, "agents.run_started")
    rid = started["data"]["run_id"]
    assert store.get_run(rid) is not None and rid in svc.watching()


async def test_a_cancelled_start_holds_the_client_lock_until_its_run_is_recorded(
    svc_factory: SvcFactory,
    store: BridgeStore,
    fake: FakeHermes,
    cc: Caller,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex on #3138: a second start while the first one's insert is in flight must see it."""
    svc = svc_factory(config=BridgeConfig(max_concurrent_runs_per_client=1))
    entered, release = threading.Event(), threading.Event()
    real_insert = store.insert_run

    def slow_insert(row: RunRow) -> None:
        entered.set()
        release.wait(3)
        real_insert(row)

    monkeypatch.setattr(store, "insert_run", slow_insert)
    first = asyncio.create_task(svc.start_run(cc, agent="hermes", input="one"))
    assert await asyncio.to_thread(entered.wait, 3)
    first.cancel()
    second = asyncio.create_task(svc.start_run(cc, agent="hermes", input="two"))
    await asyncio.sleep(0.05)  # without the fix, the second start posts here
    assert len(_posts(fake, "/v1/runs")) == 1
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    with pytest.raises(BridgeError) as e:
        await second
    assert e.value.code == "bridge.too_many_runs"
    assert len(_posts(fake, "/v1/runs")) == 1
    assert len(store.list_runs(client="cc", status=None, limit=10)) == 1


async def test_a_ledger_failure_after_hermes_accepted_stops_the_run_and_is_503(
    svc: BridgeService,
    store: BridgeStore,
    fake: FakeHermes,
    cc: Caller,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CodeRabbit on #3138: an unrecorded run would hold a Hermes slot with nobody watching."""
    import sqlite3

    def broken_insert(_row: RunRow) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "insert_run", broken_insert)
    with pytest.raises(BridgeError) as e:
        await svc.start_run(cc, agent="hermes", input="x")
    assert e.value.code == "bridge.store_unavailable" and e.value.status == 503
    assert "locked" not in e.value.message  # the cause is logged, not sent
    assert len(_posts(fake, "/v1/runs")) == 1
    [stop] = _posts(fake, "/stop")
    assert stop.url.path.endswith("/stop") and "/v1/runs/" in stop.url.path
    assert svc.watching() == []
    assert store.list_runs(client="cc", status=None, limit=10) == []


# -- ownership (Review Focus 4) ------------------------------------------------------------


async def test_other_client_gets_404_admin_sees_all(
    svc: BridgeService, cc: Caller, other: Caller, admin: Caller
) -> None:
    v = await svc.start_run(cc, agent="hermes", input="x")
    rid = v["run_id"]
    calls: list[Callable[[], Any]] = [
        lambda: svc.get_run(other, rid),
        lambda: svc.stop_run(other, rid),
        lambda: svc.steer_run(other, rid, input="more"),
        lambda: svc.answer_run(other, rid, request_id="r", answer="a"),
        lambda: svc.wait_run(other, rid, timeout_s=0),
        lambda: svc.run_events(other, rid),
    ]
    for call in calls:
        with pytest.raises(BridgeError) as e:
            await call()
        assert e.value.code == "bridge.run_not_found" and e.value.status == 404
    missing = await _raises(svc.get_run(cc, "run_nope"))
    assert missing.message == (await _raises(svc.get_run(other, rid))).message.replace(
        rid, "run_nope"
    )
    assert (await svc.get_run(admin, rid))["client"] == "cc"
    assert [r["run_id"] for r in await svc.list_runs(admin)] == [rid]
    assert await svc.list_runs(other) == []
    assert [r["run_id"] for r in await svc.list_runs(cc)] == [rid]


async def _raises(awaitable: Any) -> BridgeError:
    with pytest.raises(BridgeError) as e:
        await awaitable
    return e.value


async def test_list_runs_filters_by_status(
    svc: BridgeService, planner: Planner, cc: Caller
) -> None:
    planner.plan(frames=_plain)
    done = await svc.start_run(cc, agent="hermes", input="a")
    await svc.wait_run(cc, done["run_id"], timeout_s=5)
    live = await svc.start_run(cc, agent="hermes", input="b")
    assert [r["run_id"] for r in await svc.list_runs(cc, status="completed")] == [done["run_id"]]
    assert [r["run_id"] for r in await svc.list_runs(cc, status="running")] == [live["run_id"]]
    assert len(await svc.list_runs(cc, limit=1)) == 1


async def test_list_runs_client_filter(
    svc: BridgeService, planner: Planner, cc: Caller, other: Caller, admin: Caller
) -> None:
    """``client`` narrows the admin view; a bridge caller cannot widen its own."""
    mine = await svc.start_run(cc, agent="hermes", input="a")
    theirs = await svc.start_run(other, agent="hermes", input="b")
    assert {r["run_id"] for r in await svc.list_runs(admin)} == {mine["run_id"], theirs["run_id"]}
    assert [r["run_id"] for r in await svc.list_runs(admin, client="cc")] == [mine["run_id"]]
    assert await svc.list_runs(admin, client="nobody") == []
    assert [r["run_id"] for r in await svc.list_runs(cc, client="cc")] == [mine["run_id"]]
    assert await svc.list_runs(cc, client="other") == []
    assert [r["run_id"] for r in await svc.list_runs(cc, client=None)] == [mine["run_id"]]


# -- concurrency -------------------------------------------------------------------------


async def test_concurrency_limit(
    svc_factory: SvcFactory, fake: FakeHermes, cc: Caller, other: Caller
) -> None:
    config = {"now": BridgeConfig(max_concurrent_runs_per_client=1)}
    svc = svc_factory(config=lambda: config["now"])
    await svc.start_run(cc, agent="hermes", input="a")  # stays running
    with pytest.raises(BridgeError) as e:
        await svc.start_run(cc, agent="hermes", input="b")
    assert e.value.code == "bridge.too_many_runs" and e.value.status == 429
    assert len(_posts(fake, "/v1/runs")) == 1  # refused before posting
    await svc.start_run(other, agent="hermes", input="c")  # the limit is per client
    config["now"] = BridgeConfig(max_concurrent_runs_per_client=2)  # applies next request
    await svc.start_run(cc, agent="hermes", input="d")


async def test_concurrent_starts_do_not_overshoot_the_limit(
    svc_factory: SvcFactory, cc: Caller
) -> None:
    svc = svc_factory(config=BridgeConfig(max_concurrent_runs_per_client=1))
    results = await asyncio.gather(
        *(svc.start_run(cc, agent="hermes", input=str(i)) for i in range(3)),
        return_exceptions=True,
    )
    assert sum(isinstance(r, dict) for r in results) == 1
    assert all(
        isinstance(r, dict) or (isinstance(r, BridgeError) and r.code == "bridge.too_many_runs")
        for r in results
    )


# -- idempotency (Review Focus 3) ----------------------------------------------------------


async def test_replayed_idempotency_for_foreign_run_is_409(
    svc: BridgeService, fake: FakeHermes, store: BridgeStore, cc: Caller, other: Caller
) -> None:
    v = await svc.start_run(cc, agent="hermes", input="same", idempotency_key="k")
    fake.replay_next(v["run_id"])  # Hermes answers replayed:true with cc's run id
    with pytest.raises(BridgeError) as e:
        await svc.start_run(other, agent="hermes", input="same", idempotency_key="k")
    assert e.value.code == "bridge.idempotency_conflict" and e.value.status == 409
    assert store.list_runs(client="other", status=None, limit=10) == []
    row = store.get_run(v["run_id"])
    assert row is not None and row.client == "cc"


async def test_replayed_idempotency_for_own_run_returns_it(
    svc: BridgeService, fake: FakeHermes, store: BridgeStore, cc: Caller
) -> None:
    v = await svc.start_run(cc, agent="hermes", input="same", idempotency_key="k")
    again = await svc.start_run(cc, agent="hermes", input="same", idempotency_key="k")
    assert _posts(fake, "/v1/runs")[-1].headers["idempotency-key"] == "hal0-bridge:cc:k"
    assert again["run_id"] == v["run_id"]
    assert len(store.list_runs(client="cc", status=None, limit=10)) == 1


async def test_an_idempotent_replay_at_the_concurrency_cap_returns_the_run(
    svc_factory: SvcFactory, fake: FakeHermes, cc: Caller
) -> None:
    """Codex on #3138: the replay of an admitted start is that run, not one run too many."""
    svc = svc_factory(config=BridgeConfig(max_concurrent_runs_per_client=1))
    first = await svc.start_run(cc, agent="hermes", input="same", idempotency_key="k")
    again = await svc.start_run(cc, agent="hermes", input="same", idempotency_key="k")
    assert again["run_id"] == first["run_id"] and again["status"] == "running"
    assert len(_posts(fake, "/v1/runs")) == 1  # answered from the ledger
    with pytest.raises(BridgeError) as e:  # the same key with another request
        await svc.start_run(cc, agent="hermes", input="different", idempotency_key="k")
    assert e.value.code == "bridge.idempotency_conflict"
    with pytest.raises(BridgeError) as e:  # a new key still counts against the cap
        await svc.start_run(cc, agent="hermes", input="same", idempotency_key="k2")
    assert e.value.code == "bridge.too_many_runs"


async def test_an_idempotency_key_is_per_client_and_per_profile(
    svc: BridgeService, fake: FakeHermes, cc: Caller, other: Caller
) -> None:
    mine = await svc.start_run(cc, agent="hermes", input="same", idempotency_key="k")
    coder = await svc.start_run(cc, agent="hermes/coder", input="same", idempotency_key="k")
    theirs = await svc.start_run(other, agent="hermes", input="same", idempotency_key="k")
    assert len({mine["run_id"], coder["run_id"], theirs["run_id"]}) == 3
    assert len(_posts(fake, "/v1/runs")) == 3


async def test_idempotency_key_reused_with_another_input_is_409(
    svc: BridgeService, cc: Caller
) -> None:
    await svc.start_run(cc, agent="hermes", input="one", idempotency_key="k")
    with pytest.raises(BridgeError) as e:
        await svc.start_run(cc, agent="hermes", input="two", idempotency_key="k")
    assert e.value.code == "bridge.idempotency_conflict" and e.value.status == 409


# -- approvals ---------------------------------------------------------------------------


async def test_approval_mirrored_once_and_executes(
    svc: BridgeService,
    planner: Planner,
    fake: FakeHermes,
    store: BridgeStore,
    cc: Caller,
    queue: ApprovalQueue,
    bus: EventBus,
) -> None:
    planner.plan(statuses=("waiting_for_approval",), frames=_approval, pending_approval="req1")
    v = await svc.start_run(cc, agent="hermes", input="rm -rf tmp")
    rid = v["run_id"]
    await _until(svc, store, rid, lambda r: r.status == "waiting_for_approval")
    # An approval is the operator's to give: it does not end the client's wait.
    waited = await svc.wait_run(cc, rid, timeout_s=0.05)
    assert waited["timed_out"] is True
    assert waited["status"] == "waiting_for_approval" and waited["pending"]["request_id"] == "req1"
    [entry] = queue.list_pending()
    assert entry["tool"] == "agent_run_approval" and entry["client_id"] == "bridge:cc"
    assert entry["args"] == {
        "run_id": rid,
        "request_id": "req1",
        "command": "rm -rf tmp",
        "description": "delete tmp",
        "pattern_key": "rm",
    }
    assert entry["target_args"] == ["run_id"] and entry["hit_count"] == 1
    assert waited["pending"]["approval_id"] == entry["id"]
    asked = await _bus_event(bus, "agents.approval_requested")
    assert asked["severity"] == "warn" and asked["data"]["status"] == "waiting_for_approval"
    assert asked["message"] == "cc → hermes" and asked["data"]["run_id"] == rid  # no command

    result = await queue.approve(entry["id"])
    assert result["state"] == "executed"
    assert _body(_posts(fake, "/approval")[-1]) == {
        "request_id": "req1",
        "execution_generation": 1,
        "choice": "once",
    }
    resumed = await svc.get_run(cc, rid)
    assert resumed["status"] == "running" and resumed["pending"] is None

    fake.script_events(
        rid,
        _then(
            _approval,
            ("approval.settled", {"request_id": "req1", "choice": "once"}),
            ("run.completed", {"completed": True, "output": "removed"}),
        )(rid),
    )
    done = await svc.wait_run(cc, rid, timeout_s=3)
    assert done["status"] == "completed" and done["output"] == "removed"
    assert queue.list_pending() == []


async def test_denied_approval_posts_deny(
    svc: BridgeService,
    planner: Planner,
    fake: FakeHermes,
    store: BridgeStore,
    cc: Caller,
    queue: ApprovalQueue,
) -> None:
    planner.plan(statuses=("waiting_for_approval",), frames=_approval, pending_approval="req1")
    v = await svc.start_run(cc, agent="hermes", input="rm -rf tmp")
    await _until(svc, store, v["run_id"], lambda r: r.status == "waiting_for_approval")
    [entry] = queue.list_pending()
    result = await queue.deny(entry["id"])
    assert result["state"] == "denied" and result["error"] is None
    assert _body(_posts(fake, "/approval")[-1])["choice"] == "deny"
    view = await svc.get_run(cc, v["run_id"])
    assert view["status"] == "running" and view["pending"] is None


async def test_approval_settled_in_hermes_withdraws_the_inbox_row(
    svc: BridgeService, planner: Planner, store: BridgeStore, cc: Caller, queue: ApprovalQueue
) -> None:
    planner.plan(
        frames=_then(_approval, ("approval.settled", {"request_id": "req1", "choice": "deny"}))
    )
    v = await svc.start_run(cc, agent="hermes", input="rm -rf tmp")
    await _until(svc, store, v["run_id"], lambda r: r.last_seq == 2 and not queue.list_pending())
    [entry] = queue.list_all()
    assert entry["state"] == "failed" and "settled" in entry["error"]
    view = await svc.get_run(cc, v["run_id"])
    assert view["status"] == "running" and view["pending"] is None


async def test_approval_after_the_run_ended_is_withdrawn(
    svc: BridgeService, planner: Planner, cc: Caller, queue: ApprovalQueue
) -> None:
    planner.plan(frames=_then(_approval, ("run.cancelled", {})))
    v = await svc.start_run(cc, agent="hermes", input="rm -rf tmp")
    done = await svc.wait_run(cc, v["run_id"], timeout_s=3)
    assert done["status"] == "cancelled" and done["pending"] is None
    assert queue.list_pending() == []


async def _gated_run(svc: BridgeService, planner: Planner, store: BridgeStore, cc: Caller) -> str:
    """Start a run that waits for approval ``req1``; returns once the ledger says so."""
    planner.plan(statuses=("waiting_for_approval",), frames=_approval, pending_approval="req1")
    started = await svc.start_run(cc, agent="hermes", input="rm -rf tmp")
    await _until(svc, store, started["run_id"], lambda r: r.status == "waiting_for_approval")
    return str(started["run_id"])


@pytest.mark.parametrize("decision", ["approve", "deny"])
async def test_failed_inbox_decision_puts_the_request_back_in_the_inbox(
    decision: str,
    svc: BridgeService,
    planner: Planner,
    fake: FakeHermes,
    store: BridgeStore,
    cc: Caller,
    queue: ApprovalQueue,
) -> None:
    rid = await _gated_run(svc, planner, store, cc)
    [first] = queue.list_pending()
    decide = queue.approve if decision == "approve" else queue.deny
    choice = "once" if decision == "approve" else "deny"

    planner.approval_failures.append((503, "hermes_restarting"))  # a transient Hermes failure
    result = await decide(first["id"])
    assert "503" in result["error"]
    # The operator can still answer: one fresh pending row, the run still waiting.
    [again] = queue.list_pending()
    assert again["id"] != first["id"] and again["args"]["request_id"] == "req1"
    assert again["tool"] == "agent_run_approval" and again["client_id"] == "bridge:cc"
    waiting = await svc.get_run(cc, rid)
    assert waiting["status"] == "waiting_for_approval"
    assert waiting["pending"]["approval_id"] == again["id"]

    retried = await decide(again["id"])
    assert retried["error"] is None
    assert [_body(r)["choice"] for r in _posts(fake, "/approval")] == [choice, choice]
    resumed = await svc.get_run(cc, rid)
    assert resumed["status"] == "running" and resumed["pending"] is None
    assert queue.list_pending() == []


@pytest.mark.parametrize("code", ["approval_not_pending", "stale_generation"])
async def test_a_definitive_hermes_409_is_not_filed_again(
    code: str,
    svc: BridgeService,
    planner: Planner,
    store: BridgeStore,
    cc: Caller,
    queue: ApprovalQueue,
) -> None:
    rid = await _gated_run(svc, planner, store, cc)
    [entry] = queue.list_pending()
    planner.approval_failures.append((409, code))
    result = await queue.approve(entry["id"])
    assert result["state"] == "failed" and "409" in result["error"]
    assert queue.list_pending() == []  # Hermes no longer has the request: nothing to answer
    # The ledger is left for Hermes's own settled/terminal event to clear.
    assert (await svc.get_run(cc, rid))["status"] == "waiting_for_approval"


async def test_approving_on_the_enqueued_event_posts_once(
    svc: BridgeService,
    planner: Planner,
    fake: FakeHermes,
    store: BridgeStore,
    cc: Caller,
    queue: ApprovalQueue,
) -> None:
    # The operator's UI can decide the instant the row shows up, before the watcher
    # has finished recording the request.
    planner.plan(statuses=("waiting_for_approval",), frames=_approval, pending_approval="req1")
    subscribed = asyncio.Event()

    async def instant_approver() -> dict[str, Any]:
        async with queue.subscribe() as sub:
            subscribed.set()
            event = await sub.get()
            assert event.kind == "enqueued"
            return await queue.approve(event.entry["id"])  # no pause between row and decision

    approver = asyncio.create_task(instant_approver())
    await subscribed.wait()
    started = await svc.start_run(cc, agent="hermes", input="rm -rf tmp")
    result = await asyncio.wait_for(approver, 3)
    assert result["state"] == "executed", result["error"]
    posts = _posts(fake, "/approval")
    assert len(posts) == 1 and _body(posts[0])["choice"] == "once"
    row = store.get_run(started["run_id"])
    assert row is not None and row.status == "running" and row.pending_request_json is None


# -- clarification -----------------------------------------------------------------------


async def test_clarify_goes_to_client_and_answer_resumes(
    svc: BridgeService,
    planner: Planner,
    fake: FakeHermes,
    cc: Caller,
    queue: ApprovalQueue,
    bus: EventBus,
) -> None:
    planner.plan(frames=_clarify, pending_clarify="clr1")
    v = await svc.start_run(cc, agent="hermes", input="?")
    rid = v["run_id"]
    waited = await svc.wait_run(cc, rid, timeout_s=5)
    assert waited["timed_out"] is False
    assert waited["status"] == "waiting_for_clarification"
    assert waited["pending"]["event"] == "clarify.request"
    assert waited["pending"]["question"] == "Which colour?"
    assert queue.list_all() == []  # the question is for the client, not the inbox
    asked = await _bus_event(bus, "agents.clarification_requested")
    assert asked["severity"] == "warn"
    assert asked["message"] == "cc → hermes" and asked["data"]["run_id"] == rid  # no question

    with pytest.raises(BridgeError) as e:
        await svc.answer_run(cc, rid, request_id="wrong", answer="blue")
    assert e.value.code == "bridge.no_pending_request" and e.value.status == 409

    answered = await svc.answer_run(
        cc, rid, request_id=waited["pending"]["request_id"], answer="blue"
    )
    assert answered["status"] == "running" and answered["pending"] is None
    assert _body(_posts(fake, "/clarify")[-1]) == {
        "request_id": "clr1",
        "execution_generation": 1,
        "answer": "blue",
    }
    fake.script_events(
        rid,
        _then(
            _clarify,
            ("clarify.settled", {"request_id": "clr1"}),
            ("run.completed", {"completed": True, "output": "blue it is"}),
        )(rid),
    )
    done = await svc.wait_run(cc, rid, timeout_s=5)
    assert done["status"] == "completed" and done["output"] == "blue it is"


async def test_clarify_timeout_stops_run(
    svc_factory: SvcFactory,
    planner: Planner,
    fake: FakeHermes,
    store: BridgeStore,
    cc: Caller,
    bus: EventBus,
) -> None:
    svc = svc_factory(timing=WatcherTiming(backoff_s=(0.01,), clarify_timeout_s=0.05))
    planner.plan(frames=_clarify, pending_clarify="clr1")
    planner.on_stop = lambda rid: fake.script_events(
        rid, _then(_clarify, ("run.cancelled", {}))(rid)
    )
    v = await svc.start_run(cc, agent="hermes", input="?")
    rid = v["run_id"]
    first = await svc.wait_run(cc, rid, timeout_s=5)
    assert first["status"] == "waiting_for_clarification"
    await _until(svc, store, rid, lambda r: r.status in TERMINAL_STATUSES)
    done = await svc.wait_run(cc, rid, timeout_s=5)
    assert done["status"] in ("cancelled", "stopping")
    assert done["error"] == "clarification unanswered after 0.05s"
    assert done["pending"] is None
    assert len(_posts(fake, "/stop")) == 1
    await _bus_event(bus, "agents.run_stopped")


async def test_answered_clarification_does_not_time_out(
    svc_factory: SvcFactory, planner: Planner, fake: FakeHermes, cc: Caller
) -> None:
    svc = svc_factory(timing=WatcherTiming(backoff_s=(0.01,), clarify_timeout_s=0.2))
    planner.plan(frames=_clarify, pending_clarify="clr1")
    v = await svc.start_run(cc, agent="hermes", input="?")
    await svc.wait_run(cc, v["run_id"], timeout_s=5)
    await svc.answer_run(cc, v["run_id"], request_id="clr1", answer="red")
    late = await svc.wait_run(cc, v["run_id"], timeout_s=0.4)  # past the clarify timeout
    assert late["timed_out"] is True and late["error"] is None
    assert _posts(fake, "/stop") == []


# -- wait, steer and stop ----------------------------------------------------------------


async def test_wait_timeout_flag(svc: BridgeService, cc: Caller) -> None:
    v = await svc.start_run(cc, agent="hermes", input="slow")
    waited = await svc.wait_run(cc, v["run_id"], timeout_s=0.2)
    assert waited["timed_out"] is True and waited["status"] == "running"
    assert (await svc.wait_run(cc, v["run_id"], timeout_s=-5))["timed_out"] is True


def test_wait_timeout_is_clamped() -> None:
    from hal0.bridge.service import clamp_wait

    assert clamp_wait(1000) == 600 and clamp_wait(-1) == 0 and clamp_wait(30) == 30
    assert clamp_wait(float("nan")) == 120


async def test_steer_only_while_running(
    svc: BridgeService, planner: Planner, fake: FakeHermes, cc: Caller
) -> None:
    live = await svc.start_run(cc, agent="hermes", input="x")
    steered = await svc.steer_run(cc, live["run_id"], input="more")
    assert steered["run_id"] == live["run_id"]
    assert _body(_posts(fake, "/steer")[-1]) == {"input": "more"}

    planner.plan(frames=_plain)
    done = await svc.start_run(cc, agent="hermes", input="x")
    await svc.wait_run(cc, done["run_id"], timeout_s=5)
    with pytest.raises(BridgeError) as e:
        await svc.steer_run(cc, done["run_id"], input="more")
    assert e.value.code == "bridge.run_not_running" and e.value.status == 409


async def test_steer_refused_by_hermes_maps_to_run_not_running(
    svc: BridgeService, planner: Planner, cc: Caller
) -> None:
    planner.plan(statuses=("waiting_for_approval",))  # the ledger still says running
    v = await svc.start_run(cc, agent="hermes", input="x")
    with pytest.raises(BridgeError) as e:
        await svc.steer_run(cc, v["run_id"], input="more")
    assert e.value.code == "bridge.run_not_running"


async def test_stop_run(svc: BridgeService, planner: Planner, fake: FakeHermes, cc: Caller) -> None:
    v = await svc.start_run(cc, agent="hermes", input="x")
    stopped = await svc.stop_run(cc, v["run_id"])
    assert stopped["status"] == "stopping"
    assert len(_posts(fake, "/stop")) == 1

    planner.plan(frames=_plain)
    done = await svc.start_run(cc, agent="hermes", input="y")
    await svc.wait_run(cc, done["run_id"], timeout_s=5)
    with pytest.raises(BridgeError) as e:
        await svc.stop_run(cc, done["run_id"])
    assert e.value.code == "bridge.run_not_running"


async def test_stop_answer_with_a_terminal_status_is_not_copied(
    svc: BridgeService,
    planner: Planner,
    fake: FakeHermes,
    store: BridgeStore,
    cc: Caller,
    bus: EventBus,
) -> None:
    # Only the watcher's terminal frame finishes a run (bus event, inbox, timers).
    planner.stop_status = "completed"
    v = await svc.start_run(cc, agent="hermes", input="x")
    rid = v["run_id"]
    stopped = await svc.stop_run(cc, rid)
    assert stopped["status"] == "stopping"
    row = store.get_run(rid)
    assert row is not None and row.status == "stopping"
    assert not any(e["type"] == "agents.run_completed" for e in bus.ring)

    fake.script_events(rid, [(1, sse_frame(1, "run.completed", rid, completed=True, output="x"))])
    done = await svc.wait_run(cc, rid, timeout_s=3)
    assert done["status"] == "completed"
    await _bus_event(bus, "agents.run_completed")


async def test_stop_clears_a_pending_clarification_and_its_timer(
    svc_factory: SvcFactory, planner: Planner, fake: FakeHermes, cc: Caller
) -> None:
    svc = svc_factory(timing=WatcherTiming(backoff_s=(0.01,), clarify_timeout_s=0.1))
    planner.plan(frames=_clarify, pending_clarify="clr1")
    v = await svc.start_run(cc, agent="hermes", input="?")
    asked = await svc.wait_run(cc, v["run_id"], timeout_s=5)
    assert asked["status"] == "waiting_for_clarification"

    stopped = await svc.stop_run(cc, v["run_id"])
    assert stopped["status"] == "stopping" and stopped["pending"] is None
    later = await svc.wait_run(cc, v["run_id"], timeout_s=0.4)  # well past the clarify timeout
    assert later["timed_out"] is True and later["error"] is None
    assert len(_posts(fake, "/stop")) == 1  # the timer posted no second stop


async def test_stop_withdraws_a_pending_approval(
    svc: BridgeService,
    planner: Planner,
    store: BridgeStore,
    cc: Caller,
    queue: ApprovalQueue,
) -> None:
    rid = await _gated_run(svc, planner, store, cc)
    stopped = await svc.stop_run(cc, rid)
    assert stopped["status"] == "stopping" and stopped["pending"] is None
    assert queue.list_pending() == []
    [entry] = queue.list_all()
    assert entry["state"] == "failed" and "stopped" in entry["error"]


async def test_terminal_event_cancels_the_clarify_timer(
    svc_factory: SvcFactory, planner: Planner, fake: FakeHermes, store: BridgeStore, cc: Caller
) -> None:
    svc = svc_factory(timing=WatcherTiming(backoff_s=(0.01,), clarify_timeout_s=0.1))
    planner.plan(frames=_then(_clarify, ("run.completed", {"completed": True, "output": "ok"})))
    v = await svc.start_run(cc, agent="hermes", input="?")
    done = await _until(svc, store, v["run_id"], lambda r: r.status in TERMINAL_STATUSES)
    assert done.status == "completed" and done.pending_request_json is None
    await asyncio.sleep(0.25)  # past the clarify timeout
    final = await svc.get_run(cc, v["run_id"])
    assert final["status"] == "completed" and final["error"] is None
    assert _posts(fake, "/stop") == []


async def test_run_events_pages_the_ledger(
    svc: BridgeService, planner: Planner, cc: Caller
) -> None:
    planner.plan(frames=_plain)
    v = await svc.start_run(cc, agent="hermes", input="x")
    await svc.wait_run(cc, v["run_id"], timeout_s=5)
    events = await svc.run_events(cc, v["run_id"])
    assert [e["event"] for e in events] == [
        "message.text",
        "tool.started",
        "message.text",
        "run.completed",
    ]
    later = await svc.run_events(cc, v["run_id"], since_seq=2, limit=1)
    assert [e["seq"] for e in later] == [3]


async def test_an_events_window_says_when_it_ends_at_the_live_edge(
    svc: BridgeService, planner: Planner, cc: Caller
) -> None:
    """``after`` is None only when nothing is stored past the window (NB#2: no right context)."""
    planner.plan(frames=_plain)  # message.text, tool.started, message.text, run.completed
    v = await svc.start_run(cc, agent="hermes", input="x")
    await svc.wait_run(cc, v["run_id"], timeout_s=5)
    _before, page, after = await svc.run_events_window(cc, v["run_id"], since_seq=0, limit=1)
    assert [e["seq"] for e in page] == [1] and after == []  # closed by tool.started
    _before, page, after = await svc.run_events_window(cc, v["run_id"], since_seq=2, limit=1)
    assert [e["seq"] for e in page] == [3] and after == []  # run.completed follows
    _before, page, after = await svc.run_events_window(cc, v["run_id"], since_seq=3, limit=1)
    assert [e["seq"] for e in page] == [4] and after is None  # the newest stored event


# -- restart -----------------------------------------------------------------------------


def _live_row(run_id: str, **over: Any) -> RunRow:
    fields: dict[str, Any] = {
        "run_id": run_id,
        "client": "cc",
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
        "last_seq": 7,
    }
    fields.update(over)
    return RunRow(**fields)


@pytest.fixture
def store_with_live_run(store: BridgeStore, fake: FakeHermes) -> BridgeStore:
    rid = "run_live"
    frames = [(s, sse_frame(s, "tool.started", rid, tool="t")) for s in range(1, 8)]
    frames.append((8, sse_frame(8, "run.completed", rid, completed=True, output="back")))
    fake.seed_run(rid, statuses=("running",), frames=frames)
    store.insert_run(_live_row(rid))
    return store


async def test_reattach_resumes_live_runs(
    store_with_live_run: BridgeStore, fake: FakeHermes, svc_factory: SvcFactory, admin: Caller
) -> None:
    svc = svc_factory(store_with_live_run)
    assert await svc.reattach() == 1
    done = await svc.wait_run(admin, "run_live", timeout_s=3)
    assert done["status"] == "completed" and done["output"] == "back"
    assert fake.resume_headers[0] == "7"  # row.last_seq


async def test_reattach_does_not_let_a_hung_profile_hold_up_the_others(
    store_with_live_run: BridgeStore,
    svc_factory: SvcFactory,
    admin: Caller,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex on #3138: probes run at once, each with a short timeout; the watcher retries."""
    store_with_live_run.insert_run(_live_row("run_hung", profile="slow"))
    svc = svc_factory(store_with_live_run)
    gateway = svc._gateway
    real_get = gateway.get_run
    never = asyncio.Event()

    async def get_run(profile: str, run_id: str) -> dict[str, Any]:
        if run_id == "run_hung":
            await never.wait()  # a profile that accepts the connection and never answers
        return await real_get(profile, run_id)

    monkeypatch.setattr(gateway, "get_run", get_run)
    loop = asyncio.get_running_loop()
    began = loop.time()
    attached = await asyncio.wait_for(svc.reattach(probe_timeout_s=0.2), 3)
    assert loop.time() - began < 1.5  # one short timeout, not one per row in turn
    assert attached == 2
    assert "run_hung" in svc.watching()  # watched anyway: its reconnect loop keeps trying
    done = await svc.wait_run(admin, "run_live", timeout_s=3)
    assert done["status"] == "completed"


async def test_reattach_marks_runs_hermes_forgot_unknown(
    store: BridgeStore, svc_factory: SvcFactory, bus: EventBus
) -> None:
    store.insert_run(_live_row("run_gone"))
    svc = svc_factory()
    assert await svc.reattach() == 0
    row = store.get_run("run_gone")
    assert row is not None and row.status == "unknown"
    assert any(e["type"] == "agents.run_failed" for e in bus.ring)


async def test_reattach_mirrors_a_pending_approval_again(
    store: BridgeStore, fake: FakeHermes, svc_factory: SvcFactory, queue: ApprovalQueue
) -> None:
    rid = fake.seed_run(
        "run_gated",
        statuses=("waiting_for_approval",),
        frames=_approval("run_gated"),
        pending_approval="req1",
    )
    pending = json.loads(_approval(rid)[0][1].split("data: ", 1)[1])
    pending["approval_id"] = "lost-with-the-old-process"
    store.insert_run(
        _live_row(
            rid,
            status="waiting_for_approval",
            last_seq=1,
            pending_request_json=json.dumps(pending),
        )
    )
    svc = svc_factory()
    assert await svc.reattach() == 1
    [entry] = queue.list_pending()
    assert entry["args"]["run_id"] == rid and entry["args"]["request_id"] == "req1"
    row = store.get_run(rid)
    assert row is not None and row.as_dict()["pending"]["approval_id"] == entry["id"]


async def test_reattach_enforces_an_overdue_clarify_timeout(
    store: BridgeStore, fake: FakeHermes, svc_factory: SvcFactory
) -> None:
    rid = fake.seed_run(
        "run_asked", statuses=("running",), frames=_clarify("run_asked"), pending_clarify="clr1"
    )
    pending = json.loads(_clarify(rid)[0][1].split("data: ", 1)[1])
    pending["received_at"] = 1.0  # long ago
    store.insert_run(
        _live_row(
            rid,
            status="waiting_for_clarification",
            last_seq=1,
            pending_request_json=json.dumps(pending),
        )
    )
    svc = svc_factory(timing=WatcherTiming(backoff_s=(0.01,), clarify_timeout_s=60))
    assert await svc.reattach() == 1
    # The error and the outstanding stop are written first; the marker clears once
    # Hermes has taken the stop.
    row = await _until(svc, store, rid, lambda r: r.error is not None and r.pending_stop is None)
    assert row.error == "clarification unanswered after 60s"
    assert len(_posts(fake, "/stop")) == 1


async def test_a_clarify_timeout_stop_that_fails_is_retried_until_hermes_takes_it(
    svc_factory: SvcFactory,
    planner: Planner,
    fake: FakeHermes,
    store: BridgeStore,
    cc: Caller,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex on #3138: a transient stop failure left the run 'stopping' with no retry."""
    from hal0.bridge.hermes_client import HermesUnavailable

    svc = svc_factory(timing=WatcherTiming(backoff_s=(0.01,), clarify_timeout_s=0.05))
    planner.plan(frames=_clarify, pending_clarify="clr1")
    planner.on_stop = lambda rid: fake.script_events(
        rid, _then(_clarify, ("run.cancelled", {}))(rid)
    )
    gateway = svc._gateway
    real_stop = gateway.stop
    attempts: list[str] = []
    seen_marker: list[float | None] = []

    async def flaky_stop(profile: str, run_id: str) -> dict[str, Any]:
        attempts.append(run_id)
        row = store.get_run(run_id)
        seen_marker.append(None if row is None else row.pending_stop)
        if len(attempts) == 1:
            raise HermesUnavailable("Hermes is restarting")
        return await real_stop(profile, run_id)

    monkeypatch.setattr(gateway, "stop", flaky_stop)
    v = await svc.start_run(cc, agent="hermes", input="?")
    rid = v["run_id"]
    row = await _until(svc, store, rid, lambda r: r.status in TERMINAL_STATUSES)
    assert len(attempts) == 2  # failed once, then got through
    assert all(marker is not None for marker in seen_marker)  # persisted before each post
    assert row.status == "cancelled" and row.pending_stop is None
    assert row.error == "clarification unanswered after 0.05s"


async def test_an_outstanding_stop_is_delivered_after_a_restart(
    store: BridgeStore, fake: FakeHermes, svc_factory: SvcFactory
) -> None:
    rid = fake.seed_run("run_stopping", statuses=("running",), frames=[])
    store.insert_run(
        _live_row(
            rid,
            status="stopping",
            last_seq=0,
            error="clarification unanswered after 60s",
            pending_stop=1.0,
        )
    )
    svc = svc_factory()
    assert await svc.reattach() == 1
    row = await _until(svc, store, rid, lambda r: r.pending_stop is None)
    assert row.status != "running"
    assert len(_posts(fake, "/stop")) == 1


@pytest.mark.parametrize(("status", "code"), [(404, "run_not_found"), (409, "run_not_running")])
async def test_a_stop_hermes_refuses_as_not_needed_clears_the_marker(
    store: BridgeStore,
    fake: FakeHermes,
    svc_factory: SvcFactory,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    code: str,
) -> None:
    """404 (the run is gone) and 409 (it is already ending) are final: nothing is left to stop."""
    from hal0.bridge.hermes_client import HermesRefused

    rid = fake.seed_run("run_ending", statuses=("running",), frames=[])
    store.insert_run(_live_row(rid, status="stopping", last_seq=0, pending_stop=1.0))
    svc = svc_factory()
    calls: list[str] = []

    async def refuse(profile: str, run_id: str) -> dict[str, Any]:
        calls.append(run_id)
        raise HermesRefused(status, code, "nothing to stop")

    monkeypatch.setattr(svc._gateway, "stop", refuse)
    assert await svc.reattach() == 1
    row = await _until(svc, store, rid, lambda r: r.pending_stop is None)
    assert row.pending_stop is None and calls == [rid]  # cleared at once, no retry


@pytest.mark.parametrize(
    ("status", "code"),
    [(429, "rate_limited"), (401, None), (403, "forbidden"), (400, "bad_request")],
)
async def test_a_stop_refused_with_any_other_4xx_is_retried_until_hermes_takes_it(
    store: BridgeStore,
    fake: FakeHermes,
    svc_factory: SvcFactory,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    code: str | None,
) -> None:
    """After a 401 or 403 (a rotated key) or a 429 the run is still going in Hermes.

    Such a refusal cleared the marker as "nothing left to stop" and the run stayed
    ``stopping`` with no retry. It is retried like a 5xx; the marker clears only
    once Hermes takes the stop.
    """
    from hal0.bridge.hermes_client import HermesRefused

    rid = fake.seed_run("run_refused", statuses=("running",), frames=[])
    store.insert_run(_live_row(rid, status="stopping", last_seq=0, pending_stop=1.0))
    svc = svc_factory()
    marker_at_post: list[float | None] = []

    async def refuse_then_take(profile: str, run_id: str) -> dict[str, Any]:
        row = store.get_run(run_id)
        marker_at_post.append(None if row is None else row.pending_stop)
        if len(marker_at_post) == 1:
            raise HermesRefused(status, code, "not now")
        return {"run_id": run_id, "status": "stopping"}  # Hermes takes it (200)

    monkeypatch.setattr(svc._gateway, "stop", refuse_then_take)
    assert await svc.reattach() == 1
    row = await _until(svc, store, rid, lambda r: r.pending_stop is None)
    assert marker_at_post == [1.0, 1.0]  # still marked when the stop was posted again
    assert row.pending_stop is None


def _watch_tasks(run_id: str) -> list[asyncio.Task[Any]]:
    return [
        t for t in asyncio.all_tasks() if t.get_name() == f"bridge-watch:{run_id}" and not t.done()
    ]


async def test_overlapping_reattach_starts_one_watcher(
    store: BridgeStore, fake: FakeHermes, svc_factory: SvcFactory, queue: ApprovalQueue
) -> None:
    rid = fake.seed_run(
        "run_gated",
        statuses=("waiting_for_approval",),
        frames=_approval("run_gated"),
        pending_approval="req1",
    )
    pending = json.loads(_approval(rid)[0][1].split("data: ", 1)[1])
    store.insert_run(
        _live_row(
            rid,
            status="waiting_for_approval",
            last_seq=1,
            pending_request_json=json.dumps(pending),
        )
    )
    svc = svc_factory()
    counts = await asyncio.gather(svc.reattach(), svc.reattach())
    assert sorted(counts) == [0, 1]
    assert len(_watch_tasks(rid)) == 1 and svc.watching() == [rid]
    [entry] = queue.list_pending()
    assert entry["hit_count"] == 1  # mirrored into the inbox once
    await svc.close()
    assert _watch_tasks(rid) == []


async def test_start_and_reattach_after_close_are_unavailable(
    store: BridgeStore, fake: FakeHermes, svc_factory: SvcFactory, cc: Caller
) -> None:
    store.insert_run(_live_row("run_live"))
    fake.seed_run("run_live", statuses=("running",), frames=[])
    svc = svc_factory()
    await svc.close()
    await svc.close()  # closing again is harmless
    for call in (
        lambda: svc.start_run(cc, agent="hermes", input="x"),
        svc.reattach,
    ):
        with pytest.raises(BridgeError) as e:
            await call()
        assert e.value.code == "bridge.unavailable" and e.value.status == 503
    assert _posts(fake, "/v1/runs") == []  # nothing was started in Hermes
    assert svc.watching() == [] and _watch_tasks("run_live") == []


async def test_close_stops_every_watcher(svc: BridgeService, cc: Caller) -> None:
    await svc.start_run(cc, agent="hermes", input="a")
    await svc.start_run(cc, agent="hermes", input="b")
    assert len(svc.watching()) == 2
    await svc.close()
    assert svc.watching() == []


# -- agents ------------------------------------------------------------------------------


async def test_list_agents(svc_factory: SvcFactory, tmp_path: Path) -> None:
    for name in ("coder", "broken", "nokey"):
        (tmp_path / "profiles" / name).mkdir(parents=True)

    def probe(url: str) -> int:
        return 0 if "/p/broken/" in url else 200

    svc = svc_factory(probe=probe)
    listed = await svc.list_agents()
    hermes, pi = listed["agents"]
    assert pi == {"id": "pi", "kind": "cli", "runnable": False}
    assert hermes["id"] == "hermes" and hermes["kind"] == "daemon" and hermes["runnable"] is True
    assert hermes["profiles"] == [
        {
            "name": "default",
            "agent_id": "hermes",
            "health": 200,
            "keyed": True,
            "model_id": model_id_for("default", tmp_path),
        },
        {
            "name": "coder",
            "agent_id": "hermes/coder",
            "health": 200,
            "keyed": True,
            "model_id": "coder",
        },
        {
            "name": "nokey",
            "agent_id": "hermes/nokey",
            "health": 200,
            "keyed": False,
            "model_id": "nokey",
        },
    ]
