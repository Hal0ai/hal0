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
    id, so a test can script what Hermes sends after a stop.
    """

    def __init__(self, fake: FakeHermes) -> None:
        self.fake = fake
        self.plans: list[Callable[[str], None]] = []
        self.on_stop: Callable[[str], None] | None = None

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
        resp = self.fake(request)
        path = request.url.path
        if request.method == "POST" and path.endswith("/v1/runs") and resp.status_code == 202:
            body = json.loads(resp.read())
            if not body["replayed"]:
                if not self.plans:
                    self.plan()
                self.plans.pop(0)(body["run_id"])
        if request.method == "POST" and path.endswith("/stop") and self.on_stop is not None:
            self.on_stop(path.split("/")[-2])
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


async def test_input_preview_is_capped(svc: BridgeService, cc: Caller) -> None:
    view = await svc.start_run(cc, agent="hermes", input="y" * 1000)
    assert view["input_preview"] == "y" * 200


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
    row = await _until(svc, store, rid, lambda r: r.error is not None)
    assert row.error == "clarification unanswered after 60s"
    assert len(_posts(fake, "/stop")) == 1


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
