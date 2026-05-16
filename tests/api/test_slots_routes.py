"""Tests for the /api/slots route surface after Step 2 wiring.

Covers:
  - list-merged: real SlotManager entries + synthetic upstream entries
    coexist; real wins on name collision.
  - load-success-path: POST /api/slots/{name}/load drives the SlotManager
    through STARTING → WARMING → READY with systemctl shelled out via the
    fake-process stub.
  - state-stream-shape: GET /api/slots/{name}/state/stream emits an
    SSE ``event: state`` frame with a JSON payload carrying the slot
    name + lifecycle state.

systemctl is intercepted by monkeypatching
``hal0.slots.manager.asyncio.create_subprocess_exec`` — identical
pattern to ``tests/slots/test_manager.py`` so the route layer doesn't
need a live systemd to exercise the lifecycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.slots import manager as mgr_mod
from hal0.slots.manager import SlotManager
from hal0.upstreams.registry import Upstream

# ── fakes (mirrors tests/slots/test_manager.py) ─────────────────────────────


class _FakeProc:
    def __init__(self, rc: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = rc
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode


@pytest.fixture
def systemctl_stub(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Intercept systemctl subprocess calls and record them.

    Mirrors the stub in tests/slots/test_manager.py so the route layer
    exercises the same code path as the manager unit tests.
    """
    state: dict[str, Any] = {
        "calls": [],
        "is_active_state": "inactive",
        "force_rc": {},
    }

    async def fake_create(*args: str, **_: Any) -> _FakeProc:
        cmd = list(args)
        state["calls"].append(cmd)
        if cmd[:1] != ["systemctl"]:
            raise AssertionError(f"unexpected subprocess: {cmd}")
        action = cmd[1] if len(cmd) > 1 else ""
        service = cmd[2] if len(cmd) > 2 else ""
        key = (action, service)
        if key in state["force_rc"]:
            return _FakeProc(rc=state["force_rc"][key])
        if action == "is-active":
            return _FakeProc(rc=0 if state["is_active_state"] == "active" else 3)
        if action == "start":
            state["is_active_state"] = "active"
            return _FakeProc(rc=0)
        if action == "stop":
            state["is_active_state"] = "inactive"
            return _FakeProc(rc=0)
        if action == "daemon-reload":
            return _FakeProc(rc=0)
        return _FakeProc(rc=0)

    monkeypatch.setattr(mgr_mod.asyncio, "create_subprocess_exec", fake_create)
    return state


@pytest.fixture
def stub_await_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the real HTTP health probe so routes don't sleep on a probe.

    Issue #31 made ``_await_ready`` return a ``SlotState`` (READY for the
    happy path, IDLE when the upstream is alive but has no model).
    Callers of this stub want the READY path.
    """
    from hal0.slots.state import SlotState

    async def _ok(self: SlotManager, slot_name: str, port: int, provider: str) -> SlotState:
        return SlotState.READY

    monkeypatch.setattr(SlotManager, "_await_ready", _ok)


@pytest.fixture
def slot_root(tmp_hal0_home: str) -> Path:
    """Write a primary.toml in the HAL0_HOME slot config dir."""
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    (root / "primary.toml").write_text(
        "\n".join(
            [
                'name = "primary"',
                "port = 8081",
                'backend = "vulkan"',
                'provider = "llama-server"',
                "enabled = true",
                "[model]",
                'default = "qwen2.5-0.5b-instruct-q4_k_m"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture
def isolated_app(tmp_hal0_home: str) -> FastAPI:
    """A FastAPI app whose lifespan resolves paths under tmp_hal0_home.

    The shared ``client`` fixture in conftest.py builds the app *before*
    the per-test monkeypatch sets HAL0_HOME — so we instantiate inside
    the test instead, after tmp_hal0_home is in place.
    """
    return create_app()


@pytest.fixture
def isolated_client(isolated_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(isolated_app) as c:
        yield c


# ── list-merged ─────────────────────────────────────────────────────────────


def test_list_merges_real_and_synthetic(
    slot_root: Path,
    isolated_client: TestClient,
    isolated_app: FastAPI,
) -> None:
    """Real SlotManager entries appear alongside synthetic upstream-backed ones."""
    # Inject a remote upstream so the synthesizer has something to emit.
    isolated_app.state.upstreams.upsert(
        Upstream(
            name="haloai",
            kind="remote",
            url="http://10.0.1.220:8080/v1",
            auth_style="none",
        )
    )

    r = isolated_client.get("/api/slots")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list)

    by_name = {entry["name"]: entry for entry in body}
    # primary is a real local slot from slot_root's primary.toml
    assert "primary" in by_name, f"primary missing from {by_name.keys()}"
    assert by_name["primary"]["kind"] == "local"
    assert by_name["primary"].get("_synthetic") is not True, (
        "real slot must NOT carry _synthetic=True"
    )
    # haloai is the remote upstream — synthetic until a local slot named
    # 'haloai' is installed.
    assert "haloai" in by_name
    assert by_name["haloai"]["_synthetic"] is True
    assert by_name["haloai"]["kind"] == "remote"


def test_list_real_wins_on_name_collision(
    slot_root: Path,
    isolated_client: TestClient,
    isolated_app: FastAPI,
) -> None:
    """When a real slot and a synthetic share a name, the real one wins."""
    # Register an upstream named 'primary' — same name as the local slot.
    isolated_app.state.upstreams.upsert(
        Upstream(
            name="primary",
            kind="remote",
            url="http://10.0.1.220:8080/v1",
            auth_style="none",
        )
    )

    r = isolated_client.get("/api/slots")
    assert r.status_code == 200
    body = r.json()
    primaries = [e for e in body if e["name"] == "primary"]
    assert len(primaries) == 1, f"expected exactly one 'primary' row, got {primaries}"
    # The surviving entry must be the real one.
    assert primaries[0]["kind"] == "local"
    assert primaries[0].get("_synthetic") is not True


# ── lifespan auto-register ─────────────────────────────────────────────────


def test_lifespan_autoregisters_local_slot_as_upstream(
    slot_root: Path,
    isolated_client: TestClient,
    isolated_app: FastAPI,
) -> None:
    """A slot TOML on disk produces a matching ``kind=slot`` upstream entry.

    Without this, a fresh install with only a slot TOML can't route
    ``model: <slot_name>`` requests — the dispatcher resolves via the
    upstream registry, and SlotManager doesn't auto-mirror its slots
    there.  The lifespan hook closes that gap.
    """
    # ``slot_root`` writes /etc/hal0/slots/primary.toml with port=8081.
    upstream = isolated_app.state.upstreams.get("primary")
    assert upstream is not None, "primary slot should be auto-registered as an upstream"
    assert upstream.kind == "slot"
    assert upstream.slot_name == "primary"
    assert upstream.url == "http://127.0.0.1:8081/v1"
    assert upstream.warmup_strategy == "lazy"


def test_lifespan_autoregister_skips_when_explicit_upstream_exists(
    slot_root: Path,
    tmp_hal0_home: str,
) -> None:
    """An explicit upstreams.toml entry beats auto-register on name collision.

    Operator-supplied URLs (e.g. pointing at a reverse-proxy in front of
    the slot) must survive lifespan startup unchanged.
    """
    # Write upstreams.toml claiming a different URL for the 'primary' slot.
    cfg_dir = Path(tmp_hal0_home) / "etc" / "hal0"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "upstreams.toml").write_text(
        "\n".join(
            [
                "[[upstream]]",
                'name = "primary"',
                'kind = "slot"',
                'url = "http://10.0.0.2:9000/v1"',
                'slot_name = "primary"',
                'auth_style = "none"',
                'warmup_strategy = "eager"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    app = create_app()
    with TestClient(app) as _client:
        upstream = app.state.upstreams.get("primary")
        assert upstream is not None
        # Explicit URL survives — auto-register must have skipped this name.
        assert upstream.url == "http://10.0.0.2:9000/v1"
        assert upstream.warmup_strategy == "eager"


# ── load-success-path ──────────────────────────────────────────────────────


def test_load_success_path_drives_systemctl(
    slot_root: Path,
    systemctl_stub: dict[str, Any],
    stub_await_ready: None,
    isolated_client: TestClient,
) -> None:
    """POST /api/slots/primary/load goes through systemctl daemon-reload + start."""
    r = isolated_client.post("/api/slots/primary/load")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "primary"
    assert body["state"] == "ready"
    assert body["status"] == "ready"
    assert body["kind"] == "local"

    actions = [c[1] for c in systemctl_stub["calls"]]
    assert "daemon-reload" in actions
    assert "start" in actions


def test_load_unknown_slot_returns_typed_envelope(
    slot_root: Path,
    systemctl_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """Loading a slot with no TOML returns the typed slot.not_found envelope."""
    r = isolated_client.post("/api/slots/nope/load")
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "slot.not_found"


# ── issue #35: get_config on unknown slot → 404 not 400 ─────────────────────


def test_get_config_unknown_slot_returns_404(
    slot_root: Path,
    isolated_client: TestClient,
) -> None:
    """GET /api/slots/doesntexist/config → 404 slot.not_found.

    Pre-issue-#35 the route surfaced 400 'slot.config_error' because the
    underlying _load_slot_config conflated "missing config file" with a
    parse error. A UI distinguishing "slot doesn't exist" from "slot
    exists but config is bad" got ambiguous signals. SlotManager.get_config
    now raises SlotNotFound (404) when neither the TOML nor an in-memory
    state record exists for the slot name.
    """
    r = isolated_client.get("/api/slots/doesntexist/config")
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "slot.not_found"


def test_get_config_invalid_toml_still_returns_400(
    slot_root: Path,
    isolated_client: TestClient,
) -> None:
    """An EXISTING slot with malformed TOML still surfaces 400 slot.config_error.

    Acceptance for issue #35: the SlotNotFound mapping only applies to the
    "no config and no state" branch. A real parse failure on a config that
    is present on disk is still the operator's problem to fix, and the 400
    code communicates "this slot exists but its config is broken".
    """
    # Write a syntactically broken TOML for a real slot name.
    (slot_root / "broken.toml").write_text("name = \nport = not_an_int [oops", encoding="utf-8")
    r = isolated_client.get("/api/slots/broken/config")
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "slot.config_error"


def test_unload_after_load_transitions_to_offline(
    slot_root: Path,
    systemctl_stub: dict[str, Any],
    stub_await_ready: None,
    isolated_client: TestClient,
) -> None:
    """Verifies the /unload lifecycle route reaches the SlotManager."""
    isolated_client.post("/api/slots/primary/load")
    r = isolated_client.post("/api/slots/primary/unload")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "offline"
    assert "stop" in [c[1] for c in systemctl_stub["calls"]]


# ── state-stream-shape ─────────────────────────────────────────────────────


def test_state_endpoint_returns_lifecycle_snapshot(
    slot_root: Path,
    systemctl_stub: dict[str, Any],
    stub_await_ready: None,
    isolated_client: TestClient,
) -> None:
    """GET /api/slots/primary/state returns the lightweight state shape."""
    isolated_client.post("/api/slots/primary/load")
    r = isolated_client.get("/api/slots/primary/state")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "primary"
    assert body["state"] == "ready"
    assert body["port"] == 8081


async def test_state_stream_404_on_unknown_slot(
    slot_root: Path,
    systemctl_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """The SSE endpoint must 404 on an unknown slot (Team I gap #2).

    Per the gap brief: "404 if slot doesn't exist." The handler calls
    sm.status() before opening the long-lived stream so the failure is
    fast + synchronous and surfaces the typed envelope.
    """
    r = isolated_client.get("/api/slots/nope/state/stream")
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "slot.not_found"


async def test_state_stream_emits_transition_event(
    slot_root: Path,
    systemctl_stub: dict[str, Any],
    stub_await_ready: None,
    isolated_app: FastAPI,
) -> None:
    """Driving a state change through the manager pushes a frame to the stream.

    Subscribes to /api/slots/primary/state/stream after the slot is
    READY, then drives an unload transition and asserts the SSE consumer
    sees the OFFLINE frame. Mirrors the wire shape the dashboard's
    useSlotState composable consumes (Team I gap #2).

    Timing note: the SSE generator yields the initial snapshot eagerly,
    then awaits sm.state_stream() — which only registers a subscriber
    queue when it's first iterated. So we have to nudge the async
    scheduler past the `async for` setup before driving the transition,
    otherwise the unload broadcasts to zero subscribers and the test
    hangs on the second __anext__.
    """
    with TestClient(isolated_app):
        sm: SlotManager = isolated_app.state.slot_manager
        # Drive into READY first so we've got a concrete starting state.
        await sm.load("primary")

        from hal0.api.routes.slots import slot_state_stream

        class _ReqShim:
            class _AppShim:
                state = isolated_app.state

            app = _AppShim()

        response = await slot_state_stream("primary", _ReqShim())  # type: ignore[arg-type]
        agen = response.body_iterator
        # Drain the initial snapshot frame.
        first = await asyncio.wait_for(agen.__anext__(), timeout=1.0)
        if isinstance(first, bytes):
            first = first.decode("utf-8")
        assert "data: " in first, f"missing data line in initial frame: {first!r}"

        # Kick off the next __anext__ — this enters the `async for rec in
        # sm.state_stream()` loop, registers the subscriber queue, and
        # parks on queue.get(). We then drive the transition.
        next_frame_task = asyncio.create_task(agen.__anext__())
        # Yield until the subscriber registration has happened. Polling on
        # the subscriber count beats a fixed sleep — robust to slow CI.
        for _ in range(50):
            if len(sm._subscribers) > 0:
                break
            await asyncio.sleep(0.01)
        assert len(sm._subscribers) > 0, "subscriber never registered"

        await sm.unload("primary")

        next_frame = await asyncio.wait_for(next_frame_task, timeout=2.0)
        if isinstance(next_frame, bytes):
            next_frame = next_frame.decode("utf-8")
        await agen.aclose()

        assert next_frame.startswith("event: state\n"), f"bad SSE prefix: {next_frame!r}"
        data_line = next(line for line in next_frame.splitlines() if line.startswith("data: "))
        payload = json.loads(data_line[len("data: ") :])
        assert payload["name"] == "primary"
        # Unload runs READY → UNLOADING → OFFLINE; either intermediate
        # frame is a valid first observation depending on scheduler order.
        # What we're really asserting is "a transition fired and the
        # subscriber saw it" — the SSE wire shape carries the full
        # lifecycle metadata the UI surfaces in the slot card.
        assert payload["state"] in ("unloading", "offline"), payload
        assert "port" in payload
        assert "model_id" in payload
        assert "updated_at" in payload


async def test_state_stream_subscriber_cleaned_up_on_close(
    slot_root: Path,
    systemctl_stub: dict[str, Any],
    stub_await_ready: None,
    isolated_app: FastAPI,
) -> None:
    """Closing the SSE generator deregisters the SlotManager subscriber.

    The state machine fans transitions out to a list of asyncio.Queues —
    if an aborted SSE consumer doesn't clean up, the queue leaks and a
    QueueFull eventually starves transitions for everyone (Tier-3 risk).
    """
    with TestClient(isolated_app):
        sm: SlotManager = isolated_app.state.slot_manager
        await sm.load("primary")
        from hal0.api.routes.slots import slot_state_stream

        class _ReqShim:
            class _AppShim:
                state = isolated_app.state

            app = _AppShim()

        before = len(sm._subscribers)
        response = await slot_state_stream("primary", _ReqShim())  # type: ignore[arg-type]
        agen = response.body_iterator
        # The generator yields the initial snapshot eagerly; the subscriber
        # only registers on the next __anext__ when the inner `async for`
        # runs. Schedule that, wait until the queue appears in the list,
        # then close cleanly.
        await asyncio.wait_for(agen.__anext__(), timeout=1.0)  # initial snapshot
        next_task = asyncio.create_task(agen.__anext__())
        for _ in range(50):
            if len(sm._subscribers) == before + 1:
                break
            await asyncio.sleep(0.01)
        assert len(sm._subscribers) == before + 1, (
            f"subscriber not registered: {len(sm._subscribers)} (was {before})"
        )
        # Cancel the pending __anext__ so closing the generator doesn't
        # raise from a still-awaiting queue.get().
        next_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
            await next_task
        await agen.aclose()
        assert len(sm._subscribers) == before, (
            f"subscriber leaked: {len(sm._subscribers)} (was {before})"
        )


async def test_state_stream_emits_sse_event_shape(
    slot_root: Path,
    systemctl_stub: dict[str, Any],
    stub_await_ready: None,
    isolated_app: FastAPI,
) -> None:
    """The SSE stream's first frame is the current snapshot in the expected shape.

    Uses the SlotManager + the route handler directly rather than the
    TestClient, because TestClient blocks on a non-terminating SSE stream
    and there's no clean way to read N events from it without spawning a
    thread per frame. Driving the handler in-process keeps the test
    deterministic.
    """
    # Lifespan-driven init wires app.state.slot_manager.
    with TestClient(isolated_app):
        sm: SlotManager = isolated_app.state.slot_manager

        # Pre-load so the snapshot reflects READY.
        await sm.load("primary")

        from hal0.api.routes.slots import slot_state_stream

        # Build a Request-like shim with the only attribute the handler uses.
        class _ReqShim:
            class _AppShim:
                state = isolated_app.state

            app = _AppShim()

        response = await slot_state_stream("primary", _ReqShim())  # type: ignore[arg-type]
        # Pull the first event off the async generator.
        agen = response.body_iterator
        first = await asyncio.wait_for(agen.__anext__(), timeout=1.0)
        # Some StreamingResponse generators yield bytes; some yield str.
        if isinstance(first, bytes):
            first = first.decode("utf-8")
        assert first.startswith("event: state\n"), f"unexpected SSE prefix: {first!r}"
        # Extract the data: line and JSON-parse it.
        data_line = next(line for line in first.splitlines() if line.startswith("data: "))
        payload = json.loads(data_line[len("data: ") :])
        assert payload["name"] == "primary"
        assert payload["state"] == "ready"
        assert payload["port"] == 8081
        # Close the generator so the SlotManager removes its subscriber.
        await agen.aclose()
