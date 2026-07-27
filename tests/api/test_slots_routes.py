"""Tests for the /api/slots route surface (container runtime).

Covers:
  - list-merged: real SlotManager entries + synthetic upstream entries
    coexist; real wins on name collision.
  - load-success-path: POST /api/slots/{name}/load drives the SlotManager
    through STARTING → WARMING → READY against a mocked ContainerProvider.
  - state-stream-shape: GET /api/slots/{name}/state/stream emits an
    SSE ``event: state`` frame with a JSON payload carrying the slot
    name + lifecycle state.
  - config-field enrichment on /api/slots (drawer seeds, coresident
    grouping, declared backend) via ``hal0.slot_view.config_enrichment``.

Phase E (#687): SlotManager dispatches through ``ContainerProvider``;
the route tests mock the container seam (``container_provider()``)
instead of an HTTP daemon. The ``container_stub`` fixture patches the
module-level factory so SlotManager's lazy lookups find the fake.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.slots.manager import SlotManager
from hal0.upstreams.registry import Upstream

# ── container stub ───────────────────────────────────────────────────────────


@pytest.fixture
def container_stub() -> Iterator[dict[str, Any]]:
    """Patch ``container_provider()`` with a stateful fake; yield its state.

    The fake tracks ``load_calls`` / ``unload_calls`` and maintains an
    ``active`` set so ``is_active`` reflects load/unload history —
    SlotManager's status reconciliation then behaves like a live host.
    """
    state: dict[str, Any] = {
        "active": set(),
        "load_calls": [],
        "unload_calls": [],
    }

    provider = MagicMock()

    def _load_sync(cfg: dict[str, Any], model_info: dict[str, Any]) -> None:
        state["load_calls"].append({"cfg": dict(cfg), "model_info": dict(model_info)})
        state["active"].add(str(cfg.get("name", "")))

    def _unload_sync(cfg: dict[str, Any]) -> None:
        state["unload_calls"].append(dict(cfg))
        state["active"].discard(str(cfg.get("name", "")))

    provider.load_sync = MagicMock(side_effect=_load_sync)
    provider.unload_sync = MagicMock(side_effect=_unload_sync)
    provider.wait_ready = AsyncMock(return_value=None)
    provider.is_active = MagicMock(side_effect=lambda name: name in state["active"])
    provider.health = AsyncMock(side_effect=lambda port: {"ok": True, "status": "healthy"})
    provider.running_image = MagicMock(return_value=None)
    provider.running_argv = MagicMock(side_effect=lambda name: state.get("running_argv"))
    provider.expected_argv = MagicMock(
        side_effect=lambda cfg, model_info: state.get("expected_argv")
    )
    provider.image_present = MagicMock(return_value=True)

    with patch("hal0.providers.container.container_provider", return_value=provider):
        yield state


def _seed_slot_toml(home: str, name: str, lines: list[str]) -> Path:
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def slot_root(tmp_hal0_home: str) -> Path:
    """Write a chat.toml in the HAL0_HOME slot config dir."""
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    (root / "chat.toml").write_text(
        "\n".join(
            [
                'name = "chat"',
                "port = 8081",
                'device = "gpu-vulkan"',
                'provider = "llama-server"',
                'runtime = "container"',
                'profile = "vulkan-radv"',
                "enabled = true",
                "[model]",
                'default = "qwen3-4b-q4_k_m"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture
def npu_trio_slot_root(tmp_hal0_home: str) -> Path:
    """Lay down the NPU FLM trio (agent + stt-npu + embed-npu) on disk."""
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    _seed_slot_toml(
        tmp_hal0_home,
        "agent",
        [
            'name = "agent"',
            "port = 8082",
            'device = "npu"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "gemma3-1b"',
        ],
    )
    _seed_slot_toml(
        tmp_hal0_home,
        "stt-npu",
        [
            'name = "stt-npu"',
            "port = 8084",
            'device = "npu"',
            'type = "transcription"',
            "enabled = true",
            "[model]",
            'default = "whisper-v3"',
        ],
    )
    _seed_slot_toml(
        tmp_hal0_home,
        "embed-npu",
        [
            'name = "embed-npu"',
            "port = 8085",
            'device = "npu"',
            'type = "embedding"',
            "enabled = true",
            "[model]",
            'default = "embed-gemma"',
        ],
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
    container_stub: dict[str, Any],
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
    # chat is a real local slot from slot_root's chat.toml
    assert "chat" in by_name, f"chat missing from {by_name.keys()}"
    assert by_name["chat"]["kind"] == "local"
    assert by_name["chat"].get("_synthetic") is not True, "real slot must NOT carry _synthetic=True"
    # haloai is the remote upstream — synthetic until a local slot named
    # 'haloai' is installed.
    assert "haloai" in by_name
    assert by_name["haloai"]["_synthetic"] is True
    assert by_name["haloai"]["kind"] == "remote"


def test_list_real_wins_on_name_collision(
    slot_root: Path,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
    isolated_app: FastAPI,
) -> None:
    """When a real slot and a synthetic share a name, the real one wins."""
    # Register an upstream named 'chat' — same name as the local slot.
    isolated_app.state.upstreams.upsert(
        Upstream(
            name="chat",
            kind="remote",
            url="http://10.0.1.220:8080/v1",
            auth_style="none",
        )
    )

    r = isolated_client.get("/api/slots")
    assert r.status_code == 200
    body = r.json()
    primaries = [e for e in body if e["name"] == "chat"]
    assert len(primaries) == 1, f"expected exactly one 'chat' row, got {primaries}"
    # The surviving entry must be the real one.
    assert primaries[0]["kind"] == "local"
    assert primaries[0].get("_synthetic") is not True


# ── lifespan auto-register ─────────────────────────────────────────────────


def test_lifespan_registers_no_pseudo_upstream_for_the_composite(
    slot_root: Path,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
    isolated_app: FastAPI,
) -> None:
    """P2-composite rebuild: no ``hal0`` Upstream is registered at startup.

    Chat dispatch fans out through hal0-api itself; registering one
    Upstream per slot (the pre-R4-H2 layout) produced duplicate registry
    entries pointing at the same URL, and ``/v1/models`` dedup credited
    whichever iterated first. R4 H2 fixed this with a single composite
    ``hal0`` Upstream — itself a fake registry entry (url pointing back at
    hal0-api's own :8080) that forced dispatch to detect-and-skip it at
    every resolution step. That composite has since been deleted: the
    aggregated catalogue is primed directly into ``model_cache["hal0"]``
    (see ``_prime_hal0_composite_cache``) with no pseudo-upstream in the
    routing table at all.
    """
    # ``slot_root`` writes /etc/hal0/slots/chat.toml with port=8081.
    assert isolated_app.state.upstreams.get("hal0") is None
    # No legacy per-slot duplicate either.
    assert isolated_app.state.upstreams.get("chat") is None
    # The direct-read catalogue is primed at startup regardless.
    assert "hal0" in isolated_app.state.model_cache


def test_lifespan_hydrate_keeps_explicit_hal0_upstream(
    slot_root: Path,
    container_stub: dict[str, Any],
    tmp_hal0_home: str,
) -> None:
    """An explicit upstreams.toml entry for ``hal0`` survives startup unchanged.

    Operator-supplied URLs (e.g. pointing at a reverse-proxy in front of
    hal0-api) must survive lifespan startup unchanged — and priming the
    direct-read composite catalogue must skip rather than clobber it.
    """
    cfg_dir = Path(tmp_hal0_home) / "etc" / "hal0"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "upstreams.toml").write_text(
        "\n".join(
            [
                "[[upstream]]",
                'name = "hal0"',
                'kind = "remote"',
                'url = "https://hal0.thinmint.dev/v1"',
                'auth_style = "none"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    app = create_app()
    with TestClient(app) as _client:
        upstream = app.state.upstreams.get("hal0")
        assert upstream is not None
        # Explicit URL survives — auto-register must have skipped this name.
        assert upstream.url == "https://hal0.thinmint.dev/v1"
        assert upstream.kind == "remote"


# ── load-success-path ──────────────────────────────────────────────────────


def test_load_success_path_dispatches_via_container(
    slot_root: Path,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """POST /api/slots/chat/load goes through ContainerProvider.load_sync."""
    r = isolated_client.post("/api/slots/chat/load")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "chat"
    assert body["state"] == "ready"
    assert body["status"] == "ready"
    assert body["kind"] == "local"

    # ContainerProvider.load_sync was called with the slot's model.
    load_calls = container_stub["load_calls"]
    assert load_calls, "expected at least one load_sync call"
    assert load_calls[0]["cfg"]["name"] == "chat"
    assert load_calls[0]["model_info"]["_model_key"] == "qwen3-4b-q4_k_m"


def test_agent_hermes_slot_name_resolves_to_agent(
    tmp_hal0_home: str,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """ADR-0023: POST /api/slots/agent-hermes/load resolves via the surviving
    hidden alias to the ``agent`` slot (agent.toml) and loads its model.

    Under ADR-0023 ``primary`` is no longer an alias (it passes through and
    404s when no ``primary`` slot exists). The one remaining back-compat alias
    is ``agent-hermes`` → ``agent``."""
    _seed_slot_toml(
        tmp_hal0_home,
        "agent",
        [
            'name = "agent"',
            "port = 8081",
            'device = "gpu-vulkan"',
            'provider = "llama-server"',
            'runtime = "container"',
            'profile = "vulkan-radv"',
            "enabled = true",
            "[model]",
            'default = "qwen3-4b-q4_k_m"',
        ],
    )
    r = isolated_client.post("/api/slots/agent-hermes/load")
    assert r.status_code == 200, r.text
    body = r.json()
    # The alias resolved to the canonical agent slot.
    assert body["name"] == "agent"
    assert body["state"] == "ready"
    load_calls = container_stub["load_calls"]
    assert load_calls and load_calls[0]["model_info"]["_model_key"] == "qwen3-4b-q4_k_m"


def test_load_unknown_slot_returns_typed_envelope(
    slot_root: Path,
    container_stub: dict[str, Any],
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
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """Verifies the /unload lifecycle route reaches the SlotManager."""
    isolated_client.post("/api/slots/chat/load")
    r = isolated_client.post("/api/slots/chat/unload")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "offline"
    # ContainerProvider.unload_sync was invoked.
    assert container_stub["unload_calls"], "expected unload_sync to be called"


# ── Spec 1 / Component 2: enabled transition safety ─────────────────────────


def test_disable_running_slot_stops_it(
    slot_root: Path,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """PUT /config {enabled: false} on a RUNNING slot persists the flag AND stops it.

    The faded card must match reality — a disabled slot should not keep a
    container child resident, so the config write is followed by an unload.
    """
    isolated_client.post("/api/slots/chat/load")
    # Clear the load-path bookkeeping so we assert only the disable's unload.
    container_stub["unload_calls"].clear()

    r = isolated_client.put("/api/slots/chat/config", json={"enabled": False})
    assert r.status_code == 200, r.text
    # The persisted flag is off …
    cfg = isolated_client.get("/api/slots/chat/config").json()
    assert cfg.get("enabled") is False
    # … and the running child was actually stopped.
    assert container_stub["unload_calls"], "disabling a running slot must stop its container unit"


def test_disable_offline_slot_does_not_unload(
    slot_root: Path,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """Disabling an already-offline slot writes the flag with no unload call."""
    # The slot was never loaded: the container stub reports it inactive,
    # so the adoption probe never marks chat running.
    r = isolated_client.put("/api/slots/chat/config", json={"enabled": False})
    assert r.status_code == 200, r.text
    assert container_stub["unload_calls"] == [], (
        "an offline slot needs no stop — the write alone suffices"
    )


def test_delete_forwards_force_query_param(
    slot_root: Path,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """DELETE ?force=true binds + forwards to the manager and echoes ``forced``.

    'chat' is a non-seeded slot, so it deletes with or without force — this
    covers the query-param wiring; the seeded-guard bypass itself is unit-tested
    on SlotManager.delete.
    """
    r = isolated_client.delete("/api/slots/chat?force=true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] is True
    assert body["forced"] is True
    # The config is gone; a follow-up GET 404s.
    assert isolated_client.get("/api/slots/chat/config").status_code == 404


def test_invalid_enable_surfaces_conflict(
    tmp_hal0_home: str,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """Enabling a 2nd NPU LLM anchor → 409 with the exclusivity message, no write."""
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    for nm in ("agent", "agent2"):
        (root / f"{nm}.toml").write_text(
            "\n".join(
                [
                    f'name = "{nm}"',
                    'device = "npu"',
                    'type = "llm"',
                    f"enabled = {'true' if nm == 'agent' else 'false'}",
                    "[model]",
                    'default = "gemma3-1b"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
    # agent is enabled; enabling agent2 would land a second NPU LLM anchor.
    r = isolated_client.put("/api/slots/agent2/config", json={"enabled": True})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "slot.npu_exclusivity_violation"
    # The rejected write must not have flipped agent2 on.
    cfg = isolated_client.get("/api/slots/agent2/config").json()
    assert cfg.get("enabled") is False


# ── state-stream-shape ─────────────────────────────────────────────────────


def test_state_endpoint_returns_lifecycle_snapshot(
    slot_root: Path,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """GET /api/slots/chat/state returns the lightweight state shape."""
    isolated_client.post("/api/slots/chat/load")
    r = isolated_client.get("/api/slots/chat/state")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "chat"
    assert body["state"] == "ready"
    assert body["port"] == 8081


async def test_state_stream_404_on_unknown_slot(
    slot_root: Path,
    container_stub: dict[str, Any],
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
    container_stub: dict[str, Any],
    isolated_app: FastAPI,
) -> None:
    """Driving a state change through the manager pushes a frame to the stream.

    Subscribes to /api/slots/chat/state/stream after the slot is
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
        await sm.load("chat")

        from hal0.api.routes.slots import slot_state_stream

        class _ReqShim:
            class _AppShim:
                state = isolated_app.state

            app = _AppShim()

        response = await slot_state_stream("chat", _ReqShim())  # type: ignore[arg-type]
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

        await sm.unload("chat")

        next_frame = await asyncio.wait_for(next_frame_task, timeout=2.0)
        if isinstance(next_frame, bytes):
            next_frame = next_frame.decode("utf-8")
        await agen.aclose()

        assert next_frame.startswith("event: state\n"), f"bad SSE prefix: {next_frame!r}"
        data_line = next(line for line in next_frame.splitlines() if line.startswith("data: "))
        payload = json.loads(data_line[len("data: ") :])
        assert payload["name"] == "chat"
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
    container_stub: dict[str, Any],
    isolated_app: FastAPI,
) -> None:
    """Closing the SSE generator deregisters the SlotManager subscriber.

    The state machine fans transitions out to a list of asyncio.Queues —
    if an aborted SSE consumer doesn't clean up, the queue leaks and a
    QueueFull eventually starves transitions for everyone (Tier-3 risk).
    """
    with TestClient(isolated_app):
        sm: SlotManager = isolated_app.state.slot_manager
        await sm.load("chat")
        from hal0.api.routes.slots import slot_state_stream

        class _ReqShim:
            class _AppShim:
                state = isolated_app.state

            app = _AppShim()

        before = len(sm._subscribers)
        response = await slot_state_stream("chat", _ReqShim())  # type: ignore[arg-type]
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
    container_stub: dict[str, Any],
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
        await sm.load("chat")

        from hal0.api.routes.slots import slot_state_stream

        # Build a Request-like shim with the only attribute the handler uses.
        class _ReqShim:
            class _AppShim:
                state = isolated_app.state

            app = _AppShim()

        response = await slot_state_stream("chat", _ReqShim())  # type: ignore[arg-type]
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
        assert payload["name"] == "chat"
        assert payload["state"] == "ready"
        assert payload["port"] == 8081
        # Close the generator so the SlotManager removes its subscriber.
        await agen.aclose()


# ── _scrape_llama_metrics — Prometheus + /slots synthesis ────────────────────


class _StubResponse:
    """Minimal httpx.Response stand-in for the scrape tests.

    Implements just the surface ``_scrape_llama_metrics`` reaches into:
    ``status_code``, ``text``, ``json()``. Using a hand-rolled stub keeps
    these tests free of network shims and lets us swap the httpx client
    out with a one-line monkeypatch.
    """

    def __init__(
        self,
        *,
        status_code: int = 200,
        text: str = "",
        json_data: Any = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self._json = json_data

    def json(self) -> Any:
        if isinstance(self._json, Exception):
            raise self._json
        return self._json


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, metrics: _StubResponse, slots: _StubResponse):
    """Patch httpx.AsyncClient used inside slots._scrape_llama_metrics.

    Routes the two URLs the scrape hits to the supplied stubs and leaves
    every other call short-circuited so an accidentally widened scrape
    fails loudly in the test.
    """

    class _Client:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def get(self, url: str) -> _StubResponse:
            if url.endswith("/metrics"):
                return metrics
            if url.endswith("/slots"):
                return slots
            raise AssertionError(f"unexpected scrape URL: {url}")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


@pytest.mark.asyncio
async def test_scrape_llama_metrics_synthesises_kv_from_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Newer llama-server (b9279+) drops kv_cache_usage_ratio from
    /metrics but still exposes per-slot n_prompt_tokens + n_ctx in
    /slots; the scrape should synthesise ``kv_cache_usage`` as
    max(n_prompt_tokens) / n_ctx."""
    from hal0.api.routes.slots import _scrape_llama_metrics

    metrics_text = (
        "# HELP llamacpp:requests_processing gauge\n"
        "# TYPE llamacpp:requests_processing gauge\n"
        "llamacpp:requests_processing 1\n"
        "llamacpp:requests_deferred 0\n"
    )
    slots_json = [
        {"id": 0, "n_ctx": 4096, "is_processing": False},
        {"id": 1, "n_ctx": 4096, "is_processing": True, "n_prompt_tokens": 2048},
    ]
    _patch_httpx(
        monkeypatch,
        _StubResponse(text=metrics_text),
        _StubResponse(json_data=slots_json),
    )

    out = await _scrape_llama_metrics(8081)
    assert out["requests_processing"] == 1
    assert out["requests_deferred"] == 0
    # 2048 / 4096 = 0.5
    assert out["kv_cache_usage"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_scrape_llama_metrics_prefers_native_ratio_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a future llama.cpp reintroduces ``llamacpp:kv_cache_usage_ratio``
    we use the native gauge as-is and skip the /slots synthesis."""
    from hal0.api.routes.slots import _scrape_llama_metrics

    metrics_text = (
        "llamacpp:requests_processing 0\n"
        "llamacpp:requests_deferred 0\n"
        "llamacpp:kv_cache_usage_ratio 0.73\n"
    )
    # /slots would otherwise synthesise 0.25; the native gauge should win.
    slots_json = [{"id": 0, "n_ctx": 4096, "n_prompt_tokens": 1024}]
    _patch_httpx(
        monkeypatch,
        _StubResponse(text=metrics_text),
        _StubResponse(json_data=slots_json),
    )

    out = await _scrape_llama_metrics(8081)
    assert out["kv_cache_usage"] == pytest.approx(0.73)


@pytest.mark.asyncio
async def test_scrape_llama_metrics_omits_kv_when_slots_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idle /slots payload (no n_prompt_tokens on any sub-slot) leaves
    ``kv_cache_usage`` absent so the UI renders '—' rather than 0%."""
    from hal0.api.routes.slots import _scrape_llama_metrics

    metrics_text = "llamacpp:requests_processing 0\nllamacpp:requests_deferred 0\n"
    slots_json = [
        {"id": 0, "n_ctx": 4096, "is_processing": False},
        {"id": 1, "n_ctx": 4096, "is_processing": False},
    ]
    _patch_httpx(
        monkeypatch,
        _StubResponse(text=metrics_text),
        _StubResponse(json_data=slots_json),
    )

    out = await _scrape_llama_metrics(8081)
    assert out.get("requests_processing") == 0
    assert "kv_cache_usage" not in out


@pytest.mark.asyncio
async def test_scrape_llama_metrics_clamps_overrun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """n_prompt_tokens can briefly exceed n_ctx during shift; clamp the
    synthesised ratio at 1.0 instead of surfacing >100%."""
    from hal0.api.routes.slots import _scrape_llama_metrics

    metrics_text = "llamacpp:requests_processing 1\n"
    slots_json = [{"id": 0, "n_ctx": 4096, "n_prompt_tokens": 5000}]
    _patch_httpx(
        monkeypatch,
        _StubResponse(text=metrics_text),
        _StubResponse(json_data=slots_json),
    )

    out = await _scrape_llama_metrics(8081)
    assert out["kv_cache_usage"] == pytest.approx(1.0)


# ── synthetic composite slot: truthful "serving" status ─────────────────────


def test_synthetic_composite_slot_offline_when_nothing_loaded(
    slot_root: Path,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
    isolated_app: FastAPI,
) -> None:
    """The synthetic composite ``hal0`` slot must derive ``status`` from
    the live loaded set (dispatchable slots' model ids), NOT from
    catalogue-cache cardinality.

    Regression: the catalogue cache lists every configured chat model, so
    the old ``"serving" if models else "offline"`` rule reported the
    composite as serving forever — even when every slot was stopped.
    The dashboard then showed models "loaded" that were not resident.
    With nothing loaded, the slot must read ``offline``.
    """
    # Catalogue still advertises a chat model ...
    isolated_app.state.model_cache["hal0"] = ["qwen3-4b-q4_k_m"]
    # ... but no slot is running (container stub reports everything inactive).

    r = isolated_client.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    assert "hal0" in by_name, f"composite hal0 slot missing: {sorted(by_name)}"
    hal0 = by_name["hal0"]
    assert hal0.get("_synthetic") is True
    assert hal0["advertised_models"] >= 1, "catalogue should still advertise the model"
    assert hal0["status"] == "offline"


def test_synthetic_composite_slot_serving_when_model_loaded(
    slot_root: Path,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
    isolated_app: FastAPI,
) -> None:
    """Counterpart to the offline case: when a dispatchable slot holds the
    catalogued model, the composite slot reads ``serving``."""
    isolated_app.state.model_cache["hal0"] = ["qwen3-4b-q4_k_m"]
    # Drive the chat slot READY so its model joins the live loaded set.
    r0 = isolated_client.post("/api/slots/chat/load")
    assert r0.status_code == 200, r0.text

    r = isolated_client.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    assert "hal0" in by_name, f"composite hal0 slot missing: {sorted(by_name)}"
    assert by_name["hal0"]["status"] == "serving"


def test_patch_defaults_preserves_model_default(
    slot_root: Path,
    isolated_client: TestClient,
) -> None:
    """Regression: the slot-edit Save's ``PATCH /defaults`` must not wipe the
    model name.

    The dashboard edit drawer saves ctx_size / n_gpu_layers via
    ``PATCH /api/slots/{name}/defaults`` with a partial ``[model]`` body.
    A shallow merge replaced the whole ``[model]`` table, silently dropping
    ``[model].default`` — so after a restart the slot had no resolvable model
    and the Start button became a silent no-op. The partial update must touch
    only the keys it carries.
    """
    # Sanity: the seeded slot starts with a model default.
    before = isolated_client.get("/api/slots/chat/config")
    assert before.status_code == 200, before.text
    assert before.json()["model"]["default"] == "qwen3-4b-q4_k_m"

    r = isolated_client.patch("/api/slots/chat/defaults", json={"ctx_size": 8192})
    assert r.status_code == 200, r.text

    after = isolated_client.get("/api/slots/chat/config").json()
    # The ctx value lands; #585 normalizes the alias to canonical context_size.
    assert after["model"]["context_size"] == 8192
    # The model name survived the partial defaults write.
    assert after["model"]["default"] == "qwen3-4b-q4_k_m"


def test_patch_defaults_canonicalizes_ctx_size_key(
    slot_root: Path,
    isolated_client: TestClient,
) -> None:
    """#585: the dashboard writes ``ctx_size``; it must persist as the canonical
    ``context_size`` with no lingering alias, so the container load path (which
    reads context_size) actually honors a ctx set from the UI.
    """
    r = isolated_client.patch("/api/slots/chat/defaults", json={"ctx_size": 32768})
    assert r.status_code == 200, r.text

    model = isolated_client.get("/api/slots/chat/config").json()["model"]
    assert model["context_size"] == 32768
    assert "ctx_size" not in model


# ── config-field enrichment (ported from the retired enrichment suite) ──────
#
# /api/slots enriches every entry with TOML-derived fields via
# ``hal0.slot_view.config_enrichment``: drawer seeds (enable_thinking,
# n_gpu_layers, rope_freq_base, idle_timeout_s, workers, llamacpp_args),
# persona-surface fields (type, model_default, labels, enabled), the
# normalized ``declared_backend`` token, and ``coresident_group`` for the
# NPU FLM trio.


# ── coresident_group field ──────────────────────────────────────────────────


def test_list_slots_emits_coresident_group_for_npu_trio(
    npu_trio_slot_root: Path,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """When NPU LLM anchor is enabled, all three trio slots get coresident_group."""
    r = isolated_client.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    for slot_name in ("agent", "stt-npu", "embed-npu"):
        assert by_name[slot_name].get("coresident_group") == "npu-flm-trio", (
            f"slot {slot_name} missing coresident_group: {by_name[slot_name]}"
        )


def test_list_slots_coresident_group_uses_device_not_legacy_names(
    tmp_hal0_home: str,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """coresident_group must key off device==npu, not the legacy slot names.

    Deployment uses the real names ``npu``/``stt``/``embed`` (not the seed
    names ``agent``/``stt-npu``/``embed-npu``). The dead ``_FLM_TRIO_SLOTS``
    frozenset only matched the seed names, so the trio badge never rendered
    in production. Device-based detection fixes that.
    """
    _seed_slot_toml(
        tmp_hal0_home,
        "npu",
        [
            'name = "npu"',
            "port = 8082",
            'device = "npu"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "gemma3-4b-FLM"',
        ],
    )
    _seed_slot_toml(
        tmp_hal0_home,
        "stt",
        [
            'name = "stt"',
            "port = 8084",
            'device = "npu"',
            'type = "transcription"',
            "enabled = true",
            "[model]",
            'default = "whisper-v3"',
        ],
    )
    _seed_slot_toml(
        tmp_hal0_home,
        "embed",
        [
            'name = "embed"',
            "port = 8085",
            'device = "npu"',
            'type = "embedding"',
            "enabled = true",
            "[model]",
            'default = "embed-gemma"',
        ],
    )
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    for slot_name in ("npu", "stt", "embed"):
        assert by_name[slot_name].get("coresident_group") == "npu-flm-trio", (
            f"slot {slot_name} missing coresident_group: {by_name[slot_name]}"
        )


def test_list_slots_no_coresident_group_when_npu_anchor_disabled(
    tmp_hal0_home: str,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """Disabled NPU LLM anchor → no trio markers on the sibling slots."""
    _seed_slot_toml(
        tmp_hal0_home,
        "agent",
        [
            'name = "agent"',
            "port = 8082",
            'device = "npu"',
            'type = "llm"',
            "enabled = false",
            "[model]",
            'default = "gemma3-1b"',
        ],
    )
    _seed_slot_toml(
        tmp_hal0_home,
        "stt-npu",
        [
            'name = "stt-npu"',
            "port = 8084",
            'device = "npu"',
            'type = "transcription"',
            "enabled = true",
            "[model]",
            'default = "whisper-v3"',
        ],
    )
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    assert by_name["stt-npu"].get("coresident_group") is None


def test_list_slots_skips_coresident_for_disabled_sibling(
    tmp_hal0_home: str,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """A disabled sibling slot doesn't claim coresident membership."""
    _seed_slot_toml(
        tmp_hal0_home,
        "agent",
        [
            'name = "agent"',
            "port = 8082",
            'device = "npu"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "gemma3-1b"',
        ],
    )
    _seed_slot_toml(
        tmp_hal0_home,
        "stt-npu",
        [
            'name = "stt-npu"',
            "port = 8084",
            'device = "npu"',
            'type = "transcription"',
            "enabled = false",
            "[model]",
            'default = "whisper-v3"',
        ],
    )
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    # Anchor still marked.
    assert by_name["agent"].get("coresident_group") == "npu-flm-trio"
    # Disabled sibling is NOT marked.
    assert by_name["stt-npu"].get("coresident_group") is None


# ── config-field exposure (Spec 1 / Component 1) ───────────────────────────
#
# The slot-edit panel seeds its card + drawer controls from the slot list
# payload. Two SlotConfig fields must ride along so the UI doesn't have to
# fetch /config per slot: ``enabled`` (top-level), ``n_gpu_layers`` (from
# [model]). ``enable_thinking`` moved off the slot (spec-hw-slot-ownership
# §1 — it is ``ModelDefaults.enable_thinking`` now) and is deliberately no
# longer lifted here, even when a pre-migration TOML still carries the key.


def test_list_slots_exposes_n_gpu_layers(
    tmp_hal0_home: str,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """A slot's [model].n_gpu_layers rides along in the payload."""
    _seed_slot_toml(
        tmp_hal0_home,
        "chat",
        [
            'name = "chat"',
            "port = 8081",
            'device = "gpu-rocm"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "qwen3"',
            "n_gpu_layers = 99",
        ],
    )
    r = isolated_client.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    primary = by_name["chat"]
    assert primary["n_gpu_layers"] == 99
    assert primary["enabled"] is True


def test_list_slots_omits_enable_thinking_even_when_pre_migration_debris_present(
    tmp_hal0_home: str,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """spec-hw-slot-ownership §1: a stray slot-level ``enable_thinking`` (pre-
    migration debris, still tolerated by SlotConfig's extra="allow" on load)
    must NOT be lifted onto the /api/slots payload — reasoning is model-owned
    now, read from /api/models instead."""
    _seed_slot_toml(
        tmp_hal0_home,
        "chat",
        [
            'name = "chat"',
            "port = 8081",
            'device = "gpu-rocm"',
            'type = "llm"',
            "enabled = true",
            "enable_thinking = true",
            "[model]",
            'default = "qwen3"',
        ],
    )
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    primary = by_name["chat"]
    assert "enable_thinking" not in primary
    # n_gpu_layers absent from [model] → field still present, default sentinel
    assert "n_gpu_layers" in primary


# ── Spec 1 / Component 2 (issue #587) ──────────────────────────────────────
#
# The slot-edit drawer seeds idle_timeout_s / workers / llamacpp_args from
# the list payload. Before #587 the list omitted all three so the drawer
# used hardcoded constants (900 / 1 / "--flash-attn on --no-mmap") and
# clobbered the on-disk values on every Save. After the fix the payload
# carries the slot's real on-disk values so the drawer (and its dirty-
# tracking) can leave untouched fields alone.


def test_list_slots_exposes_idle_timeout_workers_llamacpp_args(
    tmp_hal0_home: str,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """idle_timeout_s / workers / llamacpp_args ride along on /api/slots.

    The on-disk shape is:
      - ``workers`` + ``idle_timeout_s`` are flat top-level SlotConfig
        fields (hoisted from the [slot] TOML table by the loader).
      - ``llamacpp_args`` is the dashboard's wire name; the on-disk field
        lives under ``[server].extra_args`` (ServerConfig). The list
        payload maps to the dashboard's key so the drawer can seed
        directly.
    """
    _seed_slot_toml(
        tmp_hal0_home,
        "chat",
        [
            'name = "chat"',
            "port = 8081",
            'device = "gpu-rocm"',
            'type = "llm"',
            "enabled = true",
            "workers = 4",
            "idle_timeout_s = 1200",
            "[model]",
            'default = "qwen3"',
            "[server]",
            'extra_args = "--threads 6 --no-mmap"',
        ],
    )
    r = isolated_client.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    primary = by_name["chat"]
    assert primary["idle_timeout_s"] == 1200
    assert primary["workers"] == 4
    assert primary["llamacpp_args"] == "--threads 6 --no-mmap"


def test_list_slots_llamacpp_args_none_when_server_table_absent(
    tmp_hal0_home: str,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """Slot with no [server] table → payload's llamacpp_args is null.

    Mirror the existing enable_thinking behaviour: absent on-disk → null
    in the wire payload (effective unset), not omitted. The dashboard
    uses null to skip sending the field on Save.
    """
    _seed_slot_toml(
        tmp_hal0_home,
        "chat",
        [
            'name = "chat"',
            "port = 8081",
            'device = "gpu-rocm"',
            'type = "llm"',
            "enabled = true",
            "workers = 2",
            "idle_timeout_s = 600",
            "[model]",
            'default = "qwen3"',
        ],
    )
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    primary = by_name["chat"]
    assert primary["idle_timeout_s"] == 600
    assert primary["workers"] == 2
    assert primary["llamacpp_args"] is None


# ── Issue #548: rope_freq_base ───────────────────────────────────────────────
#
# rope_freq_base is a [model] float field. The list payload must expose it so
# the Edit drawer can dirty-track and avoid clobbering the on-disk value.
# The PUT round-trip must persist it through update_config's deep merge.


def test_list_slots_exposes_rope_freq_base(
    tmp_hal0_home: str,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """rope_freq_base set on disk → exposed in /api/slots payload."""
    _seed_slot_toml(
        tmp_hal0_home,
        "chat",
        [
            'name = "chat"',
            "port = 8081",
            'device = "gpu-rocm"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "qwen3"',
            "rope_freq_base = 500000.0",
        ],
    )
    r = isolated_client.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    primary = by_name["chat"]
    assert primary["rope_freq_base"] == 500000.0


def test_list_slots_rope_freq_base_null_when_absent(
    tmp_hal0_home: str,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """rope_freq_base absent on disk → payload carries null (not 0.0)."""
    _seed_slot_toml(
        tmp_hal0_home,
        "chat",
        [
            'name = "chat"',
            "port = 8081",
            'device = "gpu-rocm"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "qwen3"',
        ],
    )
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    primary = by_name["chat"]
    assert primary["rope_freq_base"] is None


def test_put_config_rope_freq_base_roundtrip(
    tmp_hal0_home: str,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """PUT /api/slots/{name}/config with model.rope_freq_base persists the value
    and leaves the model default key intact (deep-merge, not table replacement).
    """
    _seed_slot_toml(
        tmp_hal0_home,
        "chat",
        [
            'name = "chat"',
            "port = 8081",
            'device = "gpu-rocm"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "qwen3-4b"',
        ],
    )
    r = isolated_client.put(
        "/api/slots/chat/config",
        json={"model": {"rope_freq_base": 1000000.0}},
    )
    assert r.status_code == 200, r.text

    # The list payload should now reflect the written value.
    r2 = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r2.json()}
    assert by_name["chat"]["rope_freq_base"] == 1000000.0

    # Deep-merge must not have wiped the model default key.
    cfg = isolated_client.get("/api/slots/chat/config").json()
    assert cfg["model"]["rope_freq_base"] == 1000000.0
    assert cfg["model"]["default"] == "qwen3-4b"


# ── Per-slot endpoint enrichment ───────────────────────────────────────────


def test_get_slot_includes_config_enrichment(
    npu_trio_slot_root: Path,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """GET /api/slots/{name} is enriched same shape as the list endpoint."""
    r = isolated_client.get("/api/slots/agent")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["coresident_group"] == "npu-flm-trio"
    assert body["type"] == "llm"
    assert body["model_default"] == "gemma3-1b"


def test_get_slot_includes_config_drift_when_requested(
    slot_root: Path,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """#863: single-slot status includes argv drift without burdening list()."""
    isolated_client.post("/api/slots/chat/load")
    container_stub["running_argv"] = ["--ctx-size", "4096", "-b", "512"]
    container_stub["expected_argv"] = ["--ctx-size", "131072", "-b", "2048"]

    r = isolated_client.get("/api/slots/chat")

    assert r.status_code == 200, r.text
    drift = r.json()["config_drift"]
    assert drift["drifted"] is True
    assert drift["diffs"] == [
        {"key": "--ctx-size", "running": "4096", "rendered": "131072"},
        {"key": "-b", "running": "512", "rendered": "2048"},
    ]


# ── Backwards compatibility ────────────────────────────────────────────────


def test_legacy_fields_still_present(
    npu_trio_slot_root: Path,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """v0.1.x clients consuming /api/slots see every legacy key unchanged."""
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    legacy_keys = {"name", "status", "port", "model_id", "backend", "kind"}
    for slot in ("agent", "stt-npu", "embed-npu"):
        present = set(by_name[slot].keys())
        missing = legacy_keys - present
        assert not missing, f"slot {slot} missing legacy keys: {missing}"


# ── PR-18 persona-surface fields ───────────────────────────────────────────


def test_list_slots_emits_type_and_model_default_for_persona_dropdown(
    npu_trio_slot_root: Path,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """PR-18: each entry carries ``type`` + ``model_default`` + ``enabled``.

    The dashboard's persona dropdown filters /api/slots to ``type=llm``
    rows and uses ``model_default`` as the value posted in
    ``body.model``. Without these fields the dropdown would need a
    second per-slot config fetch — adding them at the list level keeps
    page-load to a single round trip.
    """
    r = isolated_client.get("/api/slots")
    assert r.status_code == 200
    by_name = {e["name"]: e for e in r.json()}

    # The chat anchor is type=llm with a default model.
    agent = by_name["agent"]
    assert agent["type"] == "llm"
    assert agent["model_default"] == "gemma3-1b"
    assert agent["enabled"] is True

    # The transcription sibling is type=transcription — the dashboard's
    # persona dropdown filters this row OUT.
    stt = by_name["stt-npu"]
    assert stt["type"] == "transcription"
    assert stt["model_default"] == "whisper-v3"


def test_list_slots_emits_labels_for_tool_calling_gate(
    tmp_hal0_home: str,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """PR-18: ``labels`` is lifted from ``[model] labels = [...]``.

    The dashboard's OmniRouter toggle is auto-enabled when the active
    persona's model advertises ``tool-calling``. The label list arrives
    on the list endpoint so the UI doesn't need a per-slot /config
    fetch to decide whether to show the toggle.
    """
    _seed_slot_toml(
        tmp_hal0_home,
        "chat",
        [
            'name = "chat"',
            "port = 8081",
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "qwen3-4b"',
            'labels = ["tool-calling", "vision"]',
        ],
    )
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    assert by_name["chat"]["labels"] == ["tool-calling", "vision"]
    assert by_name["chat"]["type"] == "llm"
    assert by_name["chat"]["model_default"] == "qwen3-4b"


def test_list_slots_omits_labels_when_none_declared(
    npu_trio_slot_root: Path,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """Labels list is omitted (not empty) when the slot config has no
    ``model.labels`` entry. Keeps the wire payload tight and matches
    the existing pattern of only emitting fields with content.
    """
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    # The NPU trio TOMLs in the fixture don't carry a labels field.
    assert "labels" not in by_name["agent"]


def test_list_degrades_when_container_probe_fails(
    npu_trio_slot_root: Path,
    isolated_client: TestClient,
) -> None:
    """A failing container health probe doesn't break /api/slots.

    The enrichment swallows provider errors and degrades the entry to
    ``container_status="stopped"`` / ``container_health=False`` rather
    than surfacing a 500.
    """
    with (
        patch(
            "hal0.providers.container.ContainerProvider.is_active",
            return_value=True,
        ),
        patch(
            "hal0.providers.container.ContainerProvider.health",
            new_callable=AsyncMock,
            side_effect=OSError("podman exploded"),
        ),
    ):
        r = isolated_client.get("/api/slots")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    by_name = {e["name"]: e for e in body}
    assert "agent" in by_name
    assert by_name["agent"]["container_status"] == "stopped"
    assert by_name["agent"]["container_health"] is False


# ── B2: declared backend enrichment (ADR-0022) ──────────────────────────────


def test_list_slots_emits_declared_backend_from_device(
    tmp_hal0_home: str,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """A slot carries declared_backend (normalized token) derived from its
    configured ``device`` — regardless of load state. Image facts
    (``actual_image`` / ``image_mismatch``) only appear when a container
    is actually running, so they're absent here."""
    _seed_slot_toml(
        tmp_hal0_home,
        "chat",
        [
            'name = "chat"',
            "port = 8081",
            'device = "gpu-vulkan"',
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "qwen3-4b"',
        ],
    )
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    primary = by_name["chat"]
    assert primary["declared_backend"] == "vulkan"
    # No running container → actual_image + image_mismatch are absent (not null).
    assert "actual_image" not in primary
    assert "image_mismatch" not in primary


def test_list_slots_declared_backend_flm_for_npu_device(
    npu_trio_slot_root: Path,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """device=npu slots surface declared_backend='flm' (the FLM recipe)."""
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    assert by_name["agent"]["declared_backend"] == "flm"


def test_list_slots_omits_declared_backend_when_no_device(
    tmp_hal0_home: str,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """A slot with no ``device`` in TOML carries no declared_backend key."""
    _seed_slot_toml(
        tmp_hal0_home,
        "chat",
        [
            'name = "chat"',
            "port = 8081",
            'type = "llm"',
            "enabled = true",
            "[model]",
            'default = "qwen3-4b"',
        ],
    )
    r = isolated_client.get("/api/slots")
    by_name = {e["name"]: e for e in r.json()}
    assert "declared_backend" not in by_name["chat"]


def test_json_serialisation_roundtrips(
    npu_trio_slot_root: Path,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """The enriched body must be valid JSON (no exotic types leaked)."""
    r = isolated_client.get("/api/slots")
    # text + parsing both succeed → no infinite floats or set leaks
    body = json.loads(r.text)
    assert isinstance(body, list)


def test_config_profile_change_keeps_slot_owned_device_via_route(
    tmp_hal0_home: str,
    container_stub: dict[str, Any],
    isolated_client: TestClient,
) -> None:
    """PUT /config with a new profile does not re-derive device (drawer path).

    Hardware is slot-owned in the 1.0 pivot. Re-pointing a vulkan slot at a
    profile changes only the template binding; device remains the slot's typed
    hardware fact.
    """
    _seed_slot_toml(
        tmp_hal0_home,
        "utility",
        [
            'name = "utility"',
            "port = 8081",
            'device = "gpu-vulkan"',
            'provider = "llama-server"',
            'runtime = "container"',
            'profile = "vulkan"',
            "enabled = true",
            "[model]",
            'default = "m"',
        ],
    )

    r = isolated_client.put("/api/slots/utility/config", json={"profile": "rocm"})
    assert r.status_code == 200, r.text

    cfg = isolated_client.get("/api/slots/utility/config").json()
    assert cfg["profile"] == "rocm"
    assert cfg["device"] == "gpu-vulkan"


# ── spec-hw-slot-ownership §2/§4: HW-grid round-trip + fit-check warn ─────────


def test_put_config_round_trips_hw_grid_fields(
    tmp_hal0_home: str,
    isolated_client: TestClient,
) -> None:
    """The slot HW grid (NGL / THREADS / BINARY / image_pin) is top-level slot
    config: a PUT /config write persists it and both /config and the serialized
    /api/slots read surface it (config_enrichment lift)."""
    _seed_slot_toml(
        tmp_hal0_home,
        "hw",
        [
            'name = "hw"',
            "port = 8081",
            'device = "gpu-rocm"',
            'provider = "llama-server"',
            'runtime = "container"',
            "enabled = true",
            "[model]",
            'default = "m"',
        ],
    )
    body = {
        "n_gpu_layers": 99,
        "threads": 8,
        "binary": "rocmfpx",
        "image_pin": "ghcr.io/owner/repo:dbg",
    }
    r = isolated_client.put("/api/slots/hw/config", json=body)
    assert r.status_code == 200, r.text

    # Raw TOML round-trips every field.
    cfg = isolated_client.get("/api/slots/hw/config").json()
    assert cfg["n_gpu_layers"] == 99
    assert cfg["threads"] == 8
    assert cfg["binary"] == "rocmfpx"
    assert cfg["image_pin"] == "ghcr.io/owner/repo:dbg"

    # The serialized slot read (config_enrichment lift) surfaces them too, so
    # the UI edit-drawer + card seed from /api/slots without a /config fetch.
    view = isolated_client.get("/api/slots/hw").json()
    assert view["n_gpu_layers"] == 99
    assert view["threads"] == 8
    assert view["binary"] == "rocmfpx"
    assert view["image_pin"] == "ghcr.io/owner/repo:dbg"


def test_put_config_fit_check_warns_on_incompatible_pair(
    tmp_hal0_home: str,
    isolated_client: TestClient,
) -> None:
    """spec-hw-slot-ownership §4: a (device, BINARY) pair whose device backend
    is not in the runner's supported_backends WARNS (non-blocking) — the write
    still succeeds (200) and the response carries a ``fit_warning``."""
    _seed_slot_toml(
        tmp_hal0_home,
        "mismatch",
        [
            'name = "mismatch"',
            "port = 8081",
            'device = "gpu-rocm"',
            'provider = "llama-server"',
            'runtime = "container"',
            "enabled = true",
            "[model]",
            'default = "m"',
        ],
    )
    # binary "cpu" supports only ("cpu",); device gpu-rocm → backend rocm ∉ that.
    r = isolated_client.put("/api/slots/mismatch/config", json={"binary": "cpu"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert "fit_warning" in out
    assert "cpu" in out["fit_warning"]
    assert "rocm" in out["fit_warning"]


def test_put_config_no_fit_warning_when_compatible(
    tmp_hal0_home: str,
    isolated_client: TestClient,
) -> None:
    """A compatible (device, BINARY) pair carries no fit_warning key."""
    _seed_slot_toml(
        tmp_hal0_home,
        "ok",
        [
            'name = "ok"',
            "port = 8081",
            'device = "gpu-rocm"',
            'provider = "llama-server"',
            'runtime = "container"',
            "enabled = true",
            "[model]",
            'default = "m"',
        ],
    )
    # rocmfpx supports ("rocm", "vulkan") → gpu-rocm fits.
    r = isolated_client.put("/api/slots/ok/config", json={"binary": "rocmfpx"})
    assert r.status_code == 200, r.text
    assert "fit_warning" not in r.json()


# ── pure fit-check helpers ───────────────────────────────────────────────────


def test_device_backend_strips_gpu_prefix() -> None:
    from hal0.api.routes.slots import _device_backend

    assert _device_backend("gpu-rocm") == "rocm"
    assert _device_backend("gpu-vulkan") == "vulkan"
    assert _device_backend("gpu-cuda") == "cuda"
    assert _device_backend("cpu") == "cpu"
    assert _device_backend("npu") == "npu"
    assert _device_backend("") == ""
    assert _device_backend(None) == ""


def test_fit_check_warning_paths() -> None:
    from hal0.api.routes.slots import _fit_check_warning

    # rocmfpx supports rocm+vulkan → gpu-rocm/gpu-vulkan fit, cpu does not.
    assert _fit_check_warning("gpu-rocm", "rocmfpx") is None
    assert _fit_check_warning("gpu-vulkan", "rocmfpx") is None
    warn = _fit_check_warning("cpu", "rocmfpx")
    assert warn is not None and "cpu" in warn
    # Empty BINARY = HW-gated default from device → no check.
    assert _fit_check_warning("gpu-rocm", "") is None
    assert _fit_check_warning("gpu-rocm", None) is None
    # Unknown BINARY key → no check (launch path reports its own error).
    assert _fit_check_warning("gpu-rocm", "does-not-exist") is None
