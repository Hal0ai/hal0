"""The ``bridge`` boot phase and its retention loop (ADR-0043, Task 9).

The phase builds the :class:`~hal0.bridge.service.BridgeService` on
``app.state.bridge_service``, resumes every live run in the ledger, and starts
the hourly retention pass. It is non-fatal: whatever goes wrong, hal0-api boots
and the service is ``None``. The Hermes gateway comes from a module-level
factory (``hal0.api._make_bridge_gateway``) so these tests point it at
:class:`FakeHermes`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hal0.api import _bridge_maintenance_loop, create_app
from hal0.api import auth as auth_mod
from hal0.bridge.hermes_client import HermesGateway
from hal0.bridge.service import BridgeService
from hal0.bridge.store import BridgeStore, RunRow
from hal0.config.schema import BridgeConfig
from tests.bridge._fake_hermes import FakeHermes


def _write_hal0_toml(body: str) -> None:
    import os

    etc = Path(os.environ["HAL0_HOME"]) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text(body)


def _run(run_id: str, *, status: str = "running", created_at: float | None = None) -> RunRow:
    now = time.time() if created_at is None else created_at
    return RunRow(
        run_id=run_id,
        client="cc",
        agent="hermes",
        profile="default",
        session_key=None,
        status=status,
        created_at=now,
        updated_at=now,
        input_preview="ping",
        output=None,
        error=None,
        usage_json=None,
        admission_id=None,
        execution_generation=None,
        pending_request_json=None,
        last_seq=0,
    )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tmp_hal0_home: str) -> FakeHermes:
    hermes = FakeHermes()

    def _gateway() -> HermesGateway:
        return HermesGateway(home=tmp_path, environ=hermes.environ(), transport=hermes.transport())

    monkeypatch.setattr("hal0.api._make_bridge_gateway", _gateway)
    return hermes


@pytest.fixture(autouse=True)
def _no_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL0_REQUIRE_AUTH", raising=False)
    auth_mod._require_auth_cache = None


# ---------------------------------------------------------------------------
# The phase


def test_boot_builds_service_and_reattaches(fake: FakeHermes) -> None:
    """A live row in the ledger is picked up again: Hermes is asked about it and it is watched."""
    # The very store the boot phase opens (it is the auth module's process singleton).
    auth_mod.bridge_store().insert_run(_run("run_live"))
    fake.seed_run("run_live", statuses=["running"], frames=[])
    app = create_app()
    with TestClient(app) as http:
        assert http.get("/api/health").status_code == 200
        service = app.state.bridge_service
        assert isinstance(service, BridgeService)
        assert service.watching() == ["run_live"]
    # reattach asked Hermes about the run before it started watching it
    assert fake.calls[0].url.path == "/v1/runs/run_live"


def test_boot_reports_the_reattach_count(fake: FakeHermes, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    real = BridgeService.reattach

    async def spy(self: BridgeService) -> int:
        count = await real(self)
        seen.append(count)
        return count

    monkeypatch.setattr(BridgeService, "reattach", spy)
    auth_mod.bridge_store().insert_run(_run("run_live"))
    fake.seed_run("run_live", statuses=["running"], frames=[])
    with TestClient(create_app()):
        pass
    assert seen == [1]


def test_boot_wires_the_services_collaborators(fake: FakeHermes) -> None:
    app = create_app()
    with TestClient(app):
        service = app.state.bridge_service
        assert service._store is app.state.bridge_store
        assert service._events is app.state.events
        assert service.memory_dispatcher is getattr(app.state, "memory_dispatcher", None)
        assert isinstance(service._config_now(), BridgeConfig)


def test_service_config_is_read_live(fake: FakeHermes) -> None:
    """``[bridge]`` limits apply to the next request without a restart (ADR-0043 decision 6)."""
    app = create_app()
    with TestClient(app):
        service = app.state.bridge_service
        assert service._config_now().max_concurrent_runs_per_client == 2
        _write_hal0_toml("[bridge]\nmax_concurrent_runs_per_client = 5\n")
        assert service._config_now().max_concurrent_runs_per_client == 5
        # ``enabled`` is a boot-time switch: flipping it on disk does not stop the live service.
        _write_hal0_toml("[bridge]\nenabled = false\nmax_concurrent_runs_per_client = 5\n")
        assert service._config_now().enabled is True
        # A hal0.toml that cannot be read keeps the last good limits instead of failing the call.
        _write_hal0_toml("this is = not [valid toml")
        assert service._config_now().max_concurrent_runs_per_client == 2


def test_boot_disabled_leaves_state_none(fake: FakeHermes) -> None:
    _write_hal0_toml("[bridge]\nenabled = false\n")
    app = create_app()
    with TestClient(app) as http:
        assert http.get("/api/health").status_code == 200
        assert app.state.bridge_service is None
    assert fake.calls == []


def test_boot_without_a_store_leaves_state_none(
    fake: FakeHermes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> BridgeStore:
        raise OSError("var_lib is read-only")

    monkeypatch.setattr(auth_mod, "bridge_store", broken)
    app = create_app()
    with TestClient(app) as http:
        assert http.get("/api/health").status_code == 200
        assert app.state.bridge_store is None
        assert app.state.bridge_service is None


def test_boot_survives_a_gateway_failure(fake: FakeHermes, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> HermesGateway:
        raise RuntimeError("no hermes home")

    monkeypatch.setattr("hal0.api._make_bridge_gateway", boom)
    app = create_app()
    with TestClient(app) as http:
        assert http.get("/api/health").status_code == 200
        assert app.state.bridge_service is None


def test_boot_survives_a_reattach_failure(
    fake: FakeHermes, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(self: BridgeService) -> int:
        raise RuntimeError("ledger unreadable")

    closed: list[int] = []
    real_close = BridgeService.close

    async def close(self: BridgeService) -> None:
        closed.append(1)
        await real_close(self)

    monkeypatch.setattr(BridgeService, "reattach", boom)
    monkeypatch.setattr(BridgeService, "close", close)
    app = create_app()
    with TestClient(app) as http:
        assert http.get("/api/health").status_code == 200
        assert app.state.bridge_service is None
    assert closed == [1]  # the half-built service did not leak its watchers


def test_boot_does_not_wait_forever_for_a_slow_reattach(
    fake: FakeHermes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Hermes that never answers must not hold hal0-api's start; shutdown ends the reattach."""
    cancelled: list[int] = []

    async def hung(self: BridgeService) -> int:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(1)
            raise
        return 0

    monkeypatch.setattr(BridgeService, "reattach", hung)
    monkeypatch.setattr("hal0.api.BRIDGE_REATTACH_WAIT_S", 0.05)
    app = create_app()
    with TestClient(app) as http:
        assert http.get("/api/health").status_code == 200
        assert isinstance(app.state.bridge_service, BridgeService)
        assert cancelled == []
    assert cancelled == [1]


# ---------------------------------------------------------------------------
# Shutdown


def test_shutdown_cancels_the_maintenance_task_and_closes_the_service(
    fake: FakeHermes, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    async def loop(store: Any, config: Any, **_: Any) -> None:
        events.append("started")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            events.append("cancelled")
            raise

    closed: list[int] = []
    real_close = BridgeService.close

    async def close(self: BridgeService) -> None:
        events.append("closed")
        closed.append(1)
        await real_close(self)

    monkeypatch.setattr("hal0.api._bridge_maintenance_loop", loop)
    monkeypatch.setattr(BridgeService, "close", close)
    app = create_app()
    with TestClient(app):
        assert closed == []
    assert closed == [1]
    assert "started" in events
    assert events.index("cancelled") < events.index("closed")


def test_no_maintenance_task_when_the_bridge_is_disabled(
    fake: FakeHermes, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[int] = []

    async def loop(*_: Any, **__: Any) -> None:
        started.append(1)

    monkeypatch.setattr("hal0.api._bridge_maintenance_loop", loop)
    _write_hal0_toml("[bridge]\nenabled = false\n")
    with TestClient(create_app()):
        pass
    assert started == []


# ---------------------------------------------------------------------------
# The retention loop


@pytest.fixture
def store(tmp_path: Path) -> BridgeStore:
    bridge_store = BridgeStore(tmp_path / "bridge.db")
    bridge_store.open()
    return bridge_store


async def _until(predicate: Callable[[], bool], *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition never became true"
        await asyncio.sleep(0.01)


async def test_maintenance_prunes_runs_older_than_the_retention(store: BridgeStore) -> None:
    now = time.time()
    store.insert_run(_run("run_old", status="completed", created_at=now - 10 * 86400))
    store.insert_run(_run("run_new", status="completed", created_at=now - 2 * 86400))
    task = asyncio.create_task(
        _bridge_maintenance_loop(store, lambda: BridgeConfig(run_retention_days=7), interval_s=0.01)
    )
    try:
        await _until(lambda: store.get_run("run_old") is None)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert store.get_run("run_new") is not None


async def test_maintenance_reads_the_retention_each_pass(store: BridgeStore) -> None:
    now = time.time()
    store.insert_run(_run("run_a", status="completed", created_at=now - 3 * 86400))
    days = {"value": 7}
    task = asyncio.create_task(
        _bridge_maintenance_loop(
            store, lambda: BridgeConfig(run_retention_days=days["value"]), interval_s=0.01
        )
    )
    try:
        await asyncio.sleep(0.1)
        assert store.get_run("run_a") is not None  # inside 7 days
        days["value"] = 1  # the operator tightened it; no restart
        await _until(lambda: store.get_run("run_a") is None)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_maintenance_survives_a_failed_pass(store: BridgeStore) -> None:
    now = time.time()
    store.insert_run(_run("run_old", status="completed", created_at=now - 30 * 86400))
    real = store.prune_runs
    calls: list[int] = []

    def flaky(older_than: float) -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        return real(older_than)

    store.prune_runs = flaky  # type: ignore[method-assign]
    task = asyncio.create_task(
        _bridge_maintenance_loop(store, lambda: BridgeConfig(), interval_s=0.01)
    )
    try:
        await _until(lambda: store.get_run("run_old") is None)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(calls) >= 2


async def test_maintenance_interval_defaults_to_an_hour() -> None:
    from hal0.api import BRIDGE_MAINTENANCE_INTERVAL_S

    assert BRIDGE_MAINTENANCE_INTERVAL_S == 3600


def test_boot_hook_runs_reconcile_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from hal0.components import openwebui_arm

    calls: list[int] = []
    monkeypatch.setattr(
        openwebui_arm, "reconcile_openwebui_env_background", lambda **kw: calls.append(1)
    )
    _write_hal0_toml("")
    with TestClient(create_app()):
        pass
    assert calls == [1]
