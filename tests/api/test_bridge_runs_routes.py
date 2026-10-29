"""``/api/bridge/runs*`` (ADR-0043, Task 9): list, get, stop and the event stream.

The REST face of the run ledger the ``hal0-agents`` MCP tools share. These tests
run the real app (``create_app()`` with its lifespan, auth on) with the boot
phase's Hermes gateway swapped for one that talks to :class:`FakeHermes`, so the
route, the :class:`~hal0.bridge.service.BridgeService` behind it and the ledger
are all the production ones. Runs are seeded straight into ``bridge.db``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import auth as auth_mod
from hal0.api import create_app
from hal0.api.routes import bridge as bridge_routes
from hal0.bridge.hermes_client import HermesGateway
from hal0.bridge.service import BridgeService, Caller
from hal0.bridge.store import BridgeStore, RunRow
from hal0.config.schema import BridgeConfig
from tests.bridge._fake_hermes import FakeHermes, sse_frame

ADMIN_KEY = "admin-key-for-bridge-runs"


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def _write_hal0_toml(body: str) -> None:
    import os

    etc = Path(os.environ["HAL0_HOME"]) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text(body)


def make_run(
    run_id: str = "run_a",
    *,
    client: str = "cc",
    status: str = "running",
    created_at: float | None = None,
    output: str | None = None,
) -> RunRow:
    now = time.time() if created_at is None else created_at
    return RunRow(
        run_id=run_id,
        client=client,
        agent="hermes",
        profile="default",
        session_key=None,
        status=status,
        created_at=now,
        updated_at=now,
        input_preview="ping",
        output=output,
        error=None,
        usage_json=None,
        admission_id=None,
        execution_generation=None,
        pending_request_json=None,
        last_seq=0,
    )


def seed(
    http: TestClient,
    row: RunRow,
    events: tuple[tuple[int, str, dict[str, Any]], ...] = (),
) -> None:
    """Write a run (and its events) into the ledger the running app reads."""
    store: BridgeStore = http.app.state.bridge_store  # type: ignore[attr-defined]
    store.insert_run(row)
    for seq, event, payload in events:
        store.append_event(row.run_id, seq, 1_700_000_000.0 + seq, event, payload)


DONE_EVENTS: tuple[tuple[int, str, dict[str, Any]], ...] = (
    (1, "tool.started", {"tool": "terminal"}),
    (2, "message.text", {"text": "pong"}),
    (3, "run.completed", {"output": "pong"}),
)


@pytest.fixture(autouse=True)
def _auth_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")
    monkeypatch.setenv("HAL0_ADMIN_KEY", ADMIN_KEY)
    auth_mod._require_auth_cache = None
    yield
    auth_mod._require_auth_cache = None


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tmp_hal0_home: str) -> FakeHermes:
    """A scripted Hermes behind the boot phase's gateway factory."""
    hermes = FakeHermes()

    def _gateway() -> HermesGateway:
        return HermesGateway(home=tmp_path, environ=hermes.environ(), transport=hermes.transport())

    monkeypatch.setattr("hal0.api._make_bridge_gateway", _gateway)
    return hermes


@pytest.fixture
def admin_client(fake: FakeHermes) -> Iterator[TestClient]:
    _write_hal0_toml("")
    with TestClient(create_app(), headers=bearer(ADMIN_KEY)) as http:
        yield http


@pytest.fixture
def disabled_client(fake: FakeHermes) -> Iterator[TestClient]:
    _write_hal0_toml("[bridge]\nenabled = false\n")
    with TestClient(create_app(), headers=bearer(ADMIN_KEY)) as http:
        yield http


# ---------------------------------------------------------------------------
# List, get, stop


def test_runs_list_get_stop(admin_client: TestClient, fake: FakeHermes) -> None:
    fake.seed_run("run_live", statuses=["running"], frames=[])
    seed(admin_client, make_run("run_live", client="cc"))

    items = admin_client.get("/api/bridge/runs").json()["items"]
    assert items[0]["client"] == "cc"
    rid = items[0]["run_id"]
    assert admin_client.get(f"/api/bridge/runs/{rid}").json()["run_id"] == rid
    stopped = admin_client.post(f"/api/bridge/runs/{rid}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] in ("stopping", "cancelled")
    assert len(fake.requests_to("/stop")) == 1

    missing = admin_client.get("/api/bridge/runs/run_missing")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "bridge.run_not_found"


def test_run_view_shape(admin_client: TestClient) -> None:
    seed(admin_client, make_run("run_a", status="completed", output="pong"))
    view = admin_client.get("/api/bridge/runs/run_a").json()
    assert set(view) == {
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
        "output_offset",
        "output_total",
        "next_offset",
    }
    assert view["output"] == "pong"
    assert (view["output_offset"], view["output_total"], view["next_offset"]) == (0, 4, None)


def test_run_output_pages_in_16_kib_windows(admin_client: TestClient) -> None:
    """``?offset=`` pages the whole ``runs.output``, as ``agent_run_status`` does (ADR-0043)."""
    text = "".join(f"{i:05d}" for i in range(6_000))  # 30 000 bytes
    seed(admin_client, make_run("run_long", status="completed", output=text))
    first = admin_client.get("/api/bridge/runs/run_long").json()
    assert first["output"] == text[:16_384]
    assert (first["output_offset"], first["output_total"]) == (0, 30_000)
    second = admin_client.get(f"/api/bridge/runs/run_long?offset={first['next_offset']}").json()
    assert second["next_offset"] is None
    assert first["output"] + second["output"] == text
    assert admin_client.get("/api/bridge/runs/run_long?offset=-1").status_code == 422


def test_a_run_without_output_has_no_paging_fields(admin_client: TestClient) -> None:
    seed(admin_client, make_run("run_quiet", status="running"))
    view = admin_client.get("/api/bridge/runs/run_quiet").json()
    assert view["output"] is None and "next_offset" not in view


def test_runs_list_filters_by_client_status_and_limit(admin_client: TestClient) -> None:
    base = time.time()
    seed(admin_client, make_run("r1", client="cc", status="running", created_at=base))
    seed(admin_client, make_run("r2", client="cc", status="completed", created_at=base + 1))
    seed(admin_client, make_run("r3", client="ops", status="running", created_at=base + 2))

    def ids(query: str) -> list[str]:
        r = admin_client.get(f"/api/bridge/runs{query}")
        assert r.status_code == 200
        return [item["run_id"] for item in r.json()["items"]]

    assert ids("") == ["r3", "r2", "r1"]  # newest first, every client
    assert ids("?client=cc") == ["r2", "r1"]
    assert ids("?client=ops") == ["r3"]
    assert ids("?client=nobody") == []
    assert ids("?client=cc&status=completed") == ["r2"]
    assert ids("?status=running") == ["r3", "r1"]
    assert ids("?limit=1") == ["r3"]
    assert admin_client.get("/api/bridge/runs?limit=0").status_code == 422
    assert admin_client.get("/api/bridge/runs?limit=201").status_code == 422


def test_runs_list_items_preview_the_output_instead_of_carrying_it(
    admin_client: TestClient,
) -> None:
    """The list is polled; a run's whole output belongs to ``GET /runs/{run_id}``."""
    secret = "hal0bk_cc.s3cr3tvalue0123456789"
    long_output = f"Authorization: Bearer {secret} " + "y" * 30_000
    seed(admin_client, make_run("run_long", status="completed", output=long_output))
    seed(admin_client, make_run("run_quiet", status="running"))

    items = {i["run_id"]: i for i in admin_client.get("/api/bridge/runs").json()["items"]}

    long = items["run_long"]
    assert "output" not in long
    assert not {"output_offset", "output_total", "next_offset"} & set(long)
    assert len(long["output_preview"]) == 200
    assert long["output_preview"].startswith("Authorization: Bearer")
    assert secret not in long["output_preview"]
    assert "error" in long and "input_preview" in long
    assert items["run_quiet"]["output_preview"] is None
    # nothing but the preview stands in for the output
    view_keys = set(admin_client.get("/api/bridge/runs/run_quiet").json())
    assert set(long) == (view_keys - {"output"}) | {"output_preview"}

    # the whole output is still one GET away
    whole = admin_client.get("/api/bridge/runs/run_long").json()
    assert whole["output"].startswith("Authorization: Bearer") and whole["output_total"] > 30_000


def test_the_rest_list_and_the_mcp_list_carry_the_same_items(admin_client: TestClient) -> None:
    """One projection feeds both: the REST item is the MCP ``agent_runs_list`` item."""
    from hal0.mcp.agents import _list_item

    seed(admin_client, make_run("run_a", status="completed", output="pong " * 100))
    (rest,) = admin_client.get("/api/bridge/runs").json()["items"]
    stored = admin_client.get("/api/bridge/runs/run_a").json()
    view = {
        k: v for k, v in stored.items() if k not in ("output_offset", "output_total", "next_offset")
    }
    view["output"] = "pong " * 100
    assert _list_item(view) == rest


def test_stop_a_finished_run_is_409(admin_client: TestClient) -> None:
    seed(admin_client, make_run("run_done", status="completed"))
    r = admin_client.post("/api/bridge/runs/run_done/stop")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "bridge.run_not_running"


def test_stop_when_hermes_is_down_keeps_the_bridge_code(
    admin_client: TestClient, fake: FakeHermes
) -> None:
    fake.seed_run("run_live", statuses=["running"], frames=[])
    seed(admin_client, make_run("run_live"))
    fake.fail_next(500, "internal_error", "boom")
    r = admin_client.post("/api/bridge/runs/run_live/stop")
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "bridge.agent_refused"


# ---------------------------------------------------------------------------
# Event stream


def _frames(body: str) -> list[dict[str, str]]:
    """SSE text -> one dict of ``id`` / ``event`` / ``data`` per frame (comments skipped)."""
    frames = []
    for block in body.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if line.startswith(":") or ": " not in line:
                continue
            name, _, value = line.partition(": ")
            fields[name] = value
        if fields:
            frames.append(fields)
    return frames


def test_runs_events_sse_replays_then_closes(admin_client: TestClient) -> None:
    seed(admin_client, make_run("run_done", status="completed"), DONE_EVENTS)
    with admin_client.stream("GET", "/api/bridge/runs/run_done/events?since_seq=0") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["cache-control"] == "no-cache"
        body = "".join(r.iter_text())
    assert "event: run.completed" in body and body.index("id: 1") < body.index(
        "event: run.completed"
    )
    frames = _frames(body)
    assert [f.get("id") for f in frames] == ["1", "2", "3", None]
    assert [f["event"] for f in frames] == [
        "tool.started",
        "message.text",
        "run.completed",
        "run.closed",
    ]
    assert body.startswith("id: 1\nevent: tool.started\ndata: ")
    assert json.loads(frames[1]["data"]) == {
        "seq": 2,
        "ts": 1_700_000_002.0,
        "event": "message.text",
        "payload": {"text": "pong"},
    }
    assert json.loads(frames[3]["data"]) == {"run_id": "run_done", "status": "completed"}


def test_runs_events_since_seq_skips_what_the_client_has(admin_client: TestClient) -> None:
    seed(admin_client, make_run("run_done", status="completed"), DONE_EVENTS)
    with admin_client.stream("GET", "/api/bridge/runs/run_done/events?since_seq=2") as r:
        frames = _frames("".join(r.iter_text()))
    assert [f["event"] for f in frames] == ["run.completed", "run.closed"]


def test_runs_events_resumes_from_last_event_id(admin_client: TestClient) -> None:
    """EventSource reconnects send ``Last-Event-ID``; it stands in for ``since_seq``."""
    seed(admin_client, make_run("run_done", status="completed"), DONE_EVENTS)
    with admin_client.stream(
        "GET", "/api/bridge/runs/run_done/events", headers={"Last-Event-ID": "1"}
    ) as r:
        frames = _frames("".join(r.iter_text()))
    assert [f["event"] for f in frames] == ["message.text", "run.completed", "run.closed"]


def test_runs_events_of_a_run_without_events_just_closes(admin_client: TestClient) -> None:
    seed(admin_client, make_run("run_done", status="failed"))
    with admin_client.stream("GET", "/api/bridge/runs/run_done/events") as r:
        frames = _frames("".join(r.iter_text()))
    assert [f["event"] for f in frames] == ["run.closed"]
    assert json.loads(frames[0]["data"])["status"] == "failed"


def test_runs_events_unknown_run_is_a_json_404_not_a_stream(admin_client: TestClient) -> None:
    r = admin_client.get("/api/bridge/runs/run_missing/events")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["error"]["code"] == "bridge.run_not_found"


def test_runs_events_rejects_a_negative_since_seq(admin_client: TestClient) -> None:
    seed(admin_client, make_run("run_done", status="completed"))
    assert admin_client.get("/api/bridge/runs/run_done/events?since_seq=-1").status_code == 422


# -- the stream generator, driven directly (a sync TestClient cannot interleave a live run) --


class _StubRequest:
    """Enough of ``starlette.requests.Request`` for the stream generator."""

    def __init__(self, *, disconnect_after: int | None = None, app: FastAPI | None = None) -> None:
        self._calls = 0
        self._after = disconnect_after
        self.app = app

    async def is_disconnected(self) -> bool:
        self._calls += 1
        return self._after is not None and self._calls > self._after


@pytest.fixture
def store(tmp_path: Path) -> BridgeStore:
    bridge_store = BridgeStore(tmp_path / "bridge.db")
    bridge_store.open()
    return bridge_store


@pytest.fixture
async def service(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path
) -> AsyncIterator[BridgeService]:
    gateway = HermesGateway(home=tmp_path, environ=fake.environ(), transport=fake.transport())
    svc = BridgeService(
        store=store,
        gateway=gateway,
        config=BridgeConfig(),
        approval_queue=None,
        events=None,
    )
    yield svc
    await svc.close()


async def _collect(stream: AsyncIterator[str], *, until: str, timeout: float = 5.0) -> str:
    """Join stream chunks until one contains ``until`` (or the stream ends)."""
    seen = ""

    async def _drain() -> None:
        nonlocal seen
        async for chunk in stream:
            seen += chunk
            if until in seen:
                return

    await asyncio.wait_for(_drain(), timeout)
    return seen


async def test_stream_follows_a_live_run_until_it_ends(
    store: BridgeStore, service: BridgeService, fake: FakeHermes
) -> None:
    """Frames written while the stream is open arrive live; the stream closes at the end."""
    rid = fake.seed_run(
        "run_live",
        statuses=("running", "completed"),
        frames=[
            (1, sse_frame(1, "tool.started", "run_live", tool="terminal")),
            (2, sse_frame(2, "run.completed", "run_live", completed=True, output="pong")),
        ],
    )
    store.insert_run(make_run(rid, status="running"))
    admin = Caller("admin", "admin")
    stream = bridge_routes.run_event_frames(_StubRequest(), service, admin, rid, since_seq=0)  # type: ignore[arg-type]
    consumer = asyncio.ensure_future(_collect(stream, until="event: run.closed"))
    await asyncio.sleep(0.05)  # the stream is open and idle: nothing is watching the run yet
    assert not consumer.done()
    assert await service.reattach() == 1  # the watcher starts following it
    body = await consumer
    events = [f["event"] for f in _frames(body)]
    assert events[-1] == "run.closed"
    assert events.index("tool.started") < events.index("run.completed") < events.index("run.closed")


async def test_stream_sends_a_keepalive_while_idle(
    store: BridgeStore, service: BridgeService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bridge_routes, "STREAM_KEEPALIVE_S", 0.02)
    store.insert_run(make_run("run_idle", status="running"))
    stream = bridge_routes.run_event_frames(
        _StubRequest(),
        service,
        Caller("admin", "admin"),
        "run_idle",
        since_seq=0,  # type: ignore[arg-type]
    )
    first = await asyncio.wait_for(anext(stream), 2.0)
    assert first == ": keepalive\n\n"
    await stream.aclose()


async def test_stream_stops_when_the_client_disconnects(
    store: BridgeStore, service: BridgeService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bridge_routes, "STREAM_KEEPALIVE_S", 0.02)
    store.insert_run(make_run("run_idle", status="running"))
    stream = bridge_routes.run_event_frames(
        _StubRequest(disconnect_after=2),
        service,
        Caller("admin", "admin"),
        "run_idle",
        since_seq=0,  # type: ignore[arg-type]
    )
    chunks = [chunk async for chunk in stream]  # ends on its own; never closes the run
    assert chunks
    assert all(chunk == ": keepalive\n\n" for chunk in chunks)


async def test_stream_pages_through_a_long_ledger(
    store: BridgeStore, service: BridgeService
) -> None:
    store.insert_run(make_run("run_big", status="completed"))
    for seq in range(1, 251):
        store.append_event("run_big", seq, 1_700_000_000.0 + seq, "message.text", {"text": "x"})
    body = "".join(
        [
            chunk
            async for chunk in bridge_routes.run_event_frames(
                _StubRequest(),
                service,
                Caller("admin", "admin"),
                "run_big",
                since_seq=0,  # type: ignore[arg-type]
            )
        ]
    )
    ids = [int(f["id"]) for f in _frames(body) if "id" in f]
    assert ids == list(range(1, 251))
    assert body.endswith(
        "event: run.closed\ndata: "
        + json.dumps({"run_id": "run_big", "status": "completed"})
        + "\n\n"
    )


# ---------------------------------------------------------------------------
# Auth and availability


def test_bridge_key_cannot_read_runs_rest(admin_client: TestClient) -> None:
    key = admin_client.post("/api/bridge/clients", json={"name": "cc"}).json()["key"]
    seed(admin_client, make_run("run_a", client="cc"), DONE_EVENTS)
    for method, path in (
        ("GET", "/api/bridge/runs"),
        ("GET", "/api/bridge/runs/run_a"),
        ("GET", "/api/bridge/runs/run_a/events"),
        ("POST", "/api/bridge/runs/run_a/stop"),
    ):
        r = admin_client.request(method, path, headers=bearer(key))
        assert r.status_code == 403, (method, path, r.text)


def test_runs_routes_need_credentials(admin_client: TestClient) -> None:
    r = admin_client.get("/api/bridge/runs", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_runs_routes_404_when_the_bridge_is_disabled(disabled_client: TestClient) -> None:
    for method, path in (
        ("GET", "/api/bridge/runs"),
        ("GET", "/api/bridge/runs/run_a"),
        ("GET", "/api/bridge/runs/run_a/events"),
        ("POST", "/api/bridge/runs/run_a/stop"),
    ):
        r = disabled_client.request(method, path)
        assert r.status_code == 404, (method, path)
        assert r.json()["error"]["code"] == "bridge.disabled"


def test_runs_routes_503_when_the_service_is_not_running(admin_client: TestClient) -> None:
    """Enabled, but the boot phase left no service (Hermes wiring failed at start)."""
    seed(admin_client, make_run("run_a"))
    admin_client.app.state.bridge_service = None  # type: ignore[attr-defined]
    for method, path in (
        ("GET", "/api/bridge/runs"),
        ("GET", "/api/bridge/runs/run_a"),
        ("GET", "/api/bridge/runs/run_a/events"),
        ("POST", "/api/bridge/runs/run_a/stop"),
    ):
        r = admin_client.request(method, path)
        assert r.status_code == 503, (method, path)
        assert r.json()["error"]["code"] == "bridge.unavailable"


def test_unavailable_has_a_status_in_the_shared_table() -> None:
    from hal0.bridge.errors import BRIDGE_ERROR_STATUS, BridgeError

    assert BRIDGE_ERROR_STATUS["bridge.unavailable"] == 503
    assert BridgeError("bridge.unavailable", "x").status == 503


# ---------------------------------------------------------------------------
# The dock: the service's bus events reach the journal stream


async def test_run_started_reaches_the_journal_stream(
    fake: FakeHermes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``agents.run_started`` (emitted by the service) is replayed by ``/api/journal/stream``."""
    from hal0.api.routes.journal import stream_journal

    app: FastAPI = create_app()
    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        service: BridgeService = app.state.bridge_service
        assert service is not None
        view = await service.start_run(Caller("bridge", "cc"), agent="hermes", input="ping")

        resp = await stream_journal(
            _StubRequest(app=app),  # type: ignore[arg-type]
            source="merged",
            slot=None,
            level=None,
            q=None,
            since=None,
        )
        gen: AsyncIterator[Any] = resp.body_iterator
        seen = ""

        async def _drain() -> None:
            nonlocal seen
            async for chunk in gen:
                seen += chunk if isinstance(chunk, str) else chunk.decode()
                if "agents.run_started" in seen:
                    return

        try:
            await asyncio.wait_for(_drain(), 5.0)
        finally:
            await gen.aclose()
    frame = next(
        json.loads(block.removeprefix("data: "))
        for block in seen.split("\n\n")
        if "agents.run_started" in block
    )
    assert frame["source"] == "bridge:cc"
    assert frame["data"]["type"] == "agents.run_started"
    assert frame["data"]["run_id"] == view["run_id"]
    assert frame["data"]["client"] == "cc"
