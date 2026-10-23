"""``POST /api/agents/hermes/repair``: the provisioner as a tracked job.

The route registers a job in the updater's registry and a background task runs
``hal0 agent bootstrap hermes --repair --verbose`` as a child. Nothing here
starts a real child, touches the real release tree or writes under
``/var/lib/hal0``: the spawn, the log directory and the release layout are
module attributes of :mod:`hal0.api.agents.repair` and the fixtures replace each
one, and ``HAL0_HOME`` points the job store at ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog.testing
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.agents.hermes_release import Layout, builder_lock
from hal0.agents.manager import AgentRecord
from hal0.api import create_app
from hal0.api.agents import overview as overview_route
from hal0.api.agents import repair as repair_route
from hal0.api.routes import agents as agents_routes
from hal0.api.routes import updater as updater_routes
from hal0.security.exposure import AuthClass, classify


class _Lines:
    """The child's stdout: ``readline()`` yields the given lines, then ``b""``."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""


class _FakeProc:
    def __init__(self, lines: list[bytes], rc: int) -> None:
        self.stdout = _Lines(lines)
        self.returncode: int | None = None
        self._rc = rc
        self.pid = 4242

    async def wait(self) -> int:
        self.returncode = self._rc
        return self._rc


class _FakeManager:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def list(self) -> list[AgentRecord]:
        return [
            AgentRecord(
                name=name,
                installed_at="2026-10-22T00:00:00Z",
                status="installed",
                data_dir=f"/var/lib/hal0/agents/{name}",
                config_path=f"/etc/hal0/agents/{name}.toml",
            )
            for name in self._names
        ]


def _drain_background(client: TestClient) -> None:
    """Wait for the update tasks the route spawned on the app's own event loop."""
    tasks = list(getattr(client.app.state, "_update_bg_tasks", ()))  # type: ignore[attr-defined]

    async def _wait() -> None:
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    client.portal.call(_wait)  # type: ignore[union-attr]


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Layout:
    """A release layout under ``tmp_path``, in place of the real Hermes venv's."""
    lay = Layout.for_venv(tmp_path / "venvs" / "hermes")
    monkeypatch.setattr(repair_route, "_layout", lambda: lay)
    return lay


@pytest.fixture
def log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "provision-logs"
    monkeypatch.setattr(repair_route, "_log_dir", lambda: directory)
    return directory


def _client(
    monkeypatch: pytest.MonkeyPatch, installed: list[str]
) -> Iterator[tuple[FastAPI, TestClient]]:
    monkeypatch.setattr(agents_routes, "_manager", lambda: _FakeManager(installed))
    app = create_app()
    with TestClient(app) as client:
        yield app, client


@pytest.fixture
def app_client(
    tmp_hal0_home: str, layout: Layout, log_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[FastAPI, TestClient]]:
    """The whole app with Hermes installed."""
    yield from _client(monkeypatch, ["hermes"])


@pytest.fixture
def agents_client(app_client: tuple[FastAPI, TestClient]) -> TestClient:
    return app_client[1]


@pytest.fixture
def agents_client_empty(
    tmp_hal0_home: str, layout: Layout, log_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """The whole app with nothing installed."""
    for _app, client in _client(monkeypatch, []):
        yield client


def _spawn_ok(
    monkeypatch: pytest.MonkeyPatch, lines: list[bytes] | None = None, rc: int = 0
) -> AsyncMock:
    spawn = AsyncMock(return_value=_FakeProc(lines if lines is not None else [], rc))
    monkeypatch.setattr(repair_route, "_spawn", spawn)
    return spawn


def test_repair_queues_job_and_records_steps(
    agents_client: TestClient, log_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawn = _spawn_ok(monkeypatch, [b"[run ] preflight\n", b"other\n", b"[run ] install\n"])

    r = agents_client.post("/api/agents/hermes/repair")

    assert r.status_code == 202
    job = r.json()
    assert job["phase"] == "repair:hermes" and job["state"] == "queued"
    _drain_background(agents_client)
    final = agents_client.get(f"/api/updates/status/{job['id']}").json()
    assert final["state"] == "applied"
    assert final["steps"] == ["preflight", "install"]
    assert final["exit_code"] == 0
    assert final["pid"] == 4242
    assert (log_dir / f"job-{job['id']}.log").read_text().splitlines() == [
        "[run ] preflight",
        "other",
        "[run ] install",
    ]
    argv = spawn.await_args.args
    assert argv[1:] == ("agent", "bootstrap", "hermes", "--repair", "--verbose")
    assert "HERMES_HOME" not in spawn.await_args.kwargs["env"]


def test_repair_returns_the_queued_job_snapshot(
    agents_client: TestClient, log_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _spawn_ok(monkeypatch)

    job = agents_client.post("/api/agents/hermes/repair").json()

    assert set(job) == {
        "id",
        "state",
        "phase",
        "kind",
        "agent",
        "created_at",
        "updated_at",
        "error",
        "steps",
        "log_path",
        "api_pid",
    }
    assert job["kind"] == "repair" and job["agent"] == "hermes"
    assert job["error"] is None and job["steps"] == []
    assert job["api_pid"] == os.getpid()
    assert job["log_path"] == str(log_dir / f"job-{job['id']}.log")
    _drain_background(agents_client)


def test_repair_job_is_persisted_for_the_status_route(
    agents_client: TestClient, log_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _spawn_ok(monkeypatch, [b"[run ] preflight\n"])

    job = agents_client.post("/api/agents/hermes/repair").json()
    _drain_background(agents_client)

    on_disk = json.loads((updater_routes._jobs_dir() / f"{job['id']}.json").read_text())
    assert on_disk["state"] == "applied" and on_disk["steps"] == ["preflight"]
    assert on_disk["phase"] == "repair:hermes"


def test_repair_failed_exit_marks_failed(
    agents_client: TestClient, log_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _spawn_ok(monkeypatch, [b"[run ] preflight\n"], rc=1)

    job = agents_client.post("/api/agents/hermes/repair").json()
    _drain_background(agents_client)

    final = agents_client.get(f"/api/updates/status/{job['id']}").json()
    assert final["state"] == "failed"
    assert final["error"] == "bootstrap exited 1"
    assert final["exit_code"] == 1


def test_repair_spawn_failure_marks_failed(
    agents_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        repair_route, "_spawn", AsyncMock(side_effect=FileNotFoundError("no such hal0"))
    )

    job = agents_client.post("/api/agents/hermes/repair").json()
    _drain_background(agents_client)

    final = agents_client.get(f"/api/updates/status/{job['id']}").json()
    assert final["state"] == "failed"
    assert final["error"].startswith("could not start bootstrap")
    assert "exit_code" not in final


def test_repair_child_env_is_sanitised(
    agents_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", "/somewhere/else")
    monkeypatch.setenv("HOME", "/root")
    monkeypatch.delenv("HAL0_HERMES_TERMINAL", raising=False)
    monkeypatch.setattr(repair_route, "hal0_service_home", lambda: "/srv/hal0")
    monkeypatch.setattr(repair_route, "running_hal0_bin", lambda: "/opt/hal0/bin/hal0")
    spawn = _spawn_ok(monkeypatch)

    agents_client.post("/api/agents/hermes/repair")
    _drain_background(agents_client)

    env = spawn.await_args.kwargs["env"]
    assert "HERMES_HOME" not in env
    assert env["HOME"] == "/srv/hal0"
    assert "HAL0_HERMES_TERMINAL" not in env
    assert env["PYTHONUNBUFFERED"] == "1"
    assert spawn.await_args.args[0] == "/opt/hal0/bin/hal0"
    # The daemon's own environment is untouched.
    assert os.environ["HERMES_HOME"] == "/somewhere/else"


def test_repair_409_when_update_job_in_flight(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = app_client
    spawn = _spawn_ok(monkeypatch)
    app.state.update_jobs = {"j1": {"id": "j1", "state": "running", "phase": "converge:hermes"}}

    r = client.post("/api/agents/hermes/repair")

    assert r.status_code == 409
    assert r.json()["error"]["code"] == "agent.busy"
    assert r.json()["error"]["details"]["job_id"] == "j1"
    spawn.assert_not_awaited()


def test_repair_409_when_builder_lock_held(
    agents_client: TestClient, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawn = _spawn_ok(monkeypatch)

    with builder_lock(layout, caller="cli", wait=False):
        r = agents_client.post("/api/agents/hermes/repair")

    assert r.status_code == 409
    assert r.json()["error"]["code"] == "agent.busy"
    assert r.json()["error"]["details"]["holder"]["caller"] == "cli"
    spawn.assert_not_awaited()


def test_repair_in_flight_job_is_reported_before_the_lock(
    app_client: tuple[FastAPI, TestClient], layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A converge in flight also holds the lock; the job id is the better answer."""
    app, client = app_client
    app.state.update_jobs = {"j1": {"id": "j1", "state": "queued", "phase": "converge:hermes"}}

    with builder_lock(layout, caller="converge", wait=False):
        r = client.post("/api/agents/hermes/repair")

    assert r.status_code == 409
    assert r.json()["error"]["details"] == {"job_id": "j1"}


def test_repair_409_when_builder_lock_cannot_be_opened(
    agents_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lock file cannot be created: not "free", and not a 500."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    unusable = Layout.for_venv(blocker / "venvs" / "hermes")
    monkeypatch.setattr(repair_route, "_layout", lambda: unusable)
    spawn = _spawn_ok(monkeypatch)

    r = agents_client.post("/api/agents/hermes/repair")

    assert r.status_code == 409
    body = r.json()["error"]
    assert body["code"] == "agent.builder_lock_unreadable"
    assert body["details"]["error"]
    spawn.assert_not_awaited()


def test_repair_404_when_not_installed(
    agents_client_empty: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawn = _spawn_ok(monkeypatch)

    r = agents_client_empty.post("/api/agents/hermes/repair")

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "agent.not_installed"
    spawn.assert_not_awaited()


@pytest.mark.parametrize("name", ["pi", "nope"])
def test_repair_404_for_other_agents(
    agents_client: TestClient, name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawn = _spawn_ok(monkeypatch)

    r = agents_client.post(f"/api/agents/{name}/repair")

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "agent.unknown"
    spawn.assert_not_awaited()


def test_repair_invalidates_the_overview_once_the_job_is_registered(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = app_client
    _spawn_ok(monkeypatch)
    seen: list[tuple[str, list[str]]] = []

    def _invalidate(name: str) -> None:
        seen.append((name, [j["phase"] for j in app.state.update_jobs.values()]))

    monkeypatch.setattr(overview_route, "invalidate", _invalidate)

    assert client.post("/api/agents/hermes/repair").status_code == 202

    assert seen == [("hermes", ["repair:hermes"])]
    _drain_background(client)


def test_repair_audits(
    agents_client: TestClient, log_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _spawn_ok(monkeypatch, [], rc=3)

    with structlog.testing.capture_logs() as logs:
        job = agents_client.post(
            "/api/agents/hermes/repair", headers={"x-hal0-agent": "ops-bot"}
        ).json()
        _drain_background(agents_client)

    events = [entry for entry in logs if entry["event"].startswith("agent.repair")]
    assert [e["event"] for e in events] == ["agent.repair.invoked", "agent.repair.finished"]
    assert all(e["actor"] == "ops-bot" and e["job_id"] == job["id"] for e in events)
    assert events[1]["exit_code"] == 3


def test_repair_is_an_admin_route() -> None:
    assert classify("POST", "/api/agents/hermes/repair") is AuthClass.ADMIN


# ── the runner on its own ────────────────────────────────────────────────────


def _job(job_id: str = "j1") -> dict[str, dict[str, Any]]:
    return {
        job_id: {
            "id": job_id,
            "state": "queued",
            "phase": "repair:hermes",
            "agent": "hermes",
            "error": None,
            "steps": [],
        }
    }


@pytest.fixture
def no_persist(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the job store write; the list collects the state at each write."""
    states: list[str] = []
    monkeypatch.setattr(repair_route, "_persist_job", lambda job: states.append(job["state"]))
    return states


async def test_runner_persists_each_step_as_it_arrives(
    tmp_path: Path, no_persist: list[str]
) -> None:
    jobs = _job()
    spawn = AsyncMock(
        return_value=_FakeProc([b"[run ] one\n", b"[run ] two\r\n", b"[run ] three\n"], 0)
    )

    await repair_route.run_repair_job(
        jobs, "j1", argv=["hal0"], env={}, log_path=tmp_path / "x.log", spawn=spawn
    )

    assert jobs["j1"]["steps"] == ["one", "two", "three"]
    # running (pid), one write per step, then the terminal state
    assert no_persist == ["running", "running", "running", "running", "applied"]


async def test_runner_without_a_usable_log_file_still_runs(
    tmp_path: Path, no_persist: list[str]
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    jobs = _job()
    spawn = AsyncMock(return_value=_FakeProc([b"[run ] one\n"], 0))

    await repair_route.run_repair_job(
        jobs, "j1", argv=["hal0"], env={}, log_path=blocker / "dir" / "x.log", spawn=spawn
    )

    assert jobs["j1"]["state"] == "applied"
    assert jobs["j1"]["steps"] == ["one"]
    assert jobs["j1"]["log_path"] is None


async def test_runner_marks_failed_when_reading_the_output_breaks(
    tmp_path: Path, no_persist: list[str]
) -> None:
    class _Broken(_FakeProc):
        def __init__(self) -> None:
            super().__init__([], 0)
            self.killed = False

        async def _boom(self) -> bytes:
            raise ValueError("Separator is not found, and chunk exceed the limit")

        def kill(self) -> None:
            self.killed = True

    proc = _Broken()
    proc.stdout.readline = proc._boom  # type: ignore[method-assign]
    jobs = _job()

    await repair_route.run_repair_job(
        jobs,
        "j1",
        argv=["hal0"],
        env={},
        log_path=tmp_path / "x.log",
        spawn=AsyncMock(return_value=proc),
    )

    assert jobs["j1"]["state"] == "failed"
    assert jobs["j1"]["error"].startswith("repair runner failed")
    assert proc.killed


async def test_runner_cancellation_kills_the_child_and_leaves_the_job_running(
    tmp_path: Path, no_persist: list[str]
) -> None:
    class _Hangs(_FakeProc):
        killed = False

        def kill(self) -> None:
            self.killed = True

    proc = _Hangs([], 0)

    async def _never() -> bytes:
        await asyncio.sleep(3600)
        return b""

    proc.stdout.readline = _never  # type: ignore[method-assign]
    jobs = _job()
    task = asyncio.create_task(
        repair_route.run_repair_job(
            jobs,
            "j1",
            argv=["hal0"],
            env={},
            log_path=tmp_path / "x.log",
            spawn=AsyncMock(return_value=proc),
        )
    )
    for _ in range(10):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert proc.killed
    assert jobs["j1"]["state"] == "running"
    assert no_persist == ["running"]
