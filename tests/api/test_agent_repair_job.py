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
import contextlib
import json
import os
import signal
import stat
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

#: The real group-signalling function, kept so the one test that spawns real
#: processes can put it back after the autouse fixture below replaces it.
_REAL_SIGNAL_GROUP = repair_route._signal_group


class _Lines:
    """The child's stdout: ``readline()`` yields the given lines, then ``b""``."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""


class _OverlongOnce:
    """A child's stdout whose ``readline`` raises the given exceptions in sequence, then ends.

    ``items`` before the first exception are lines; an exception item is raised
    once; ``after`` are the lines that follow.
    """

    def __init__(self, items: list[bytes | BaseException], after: list[bytes]) -> None:
        self._items = [*items, *after]

    async def readline(self) -> bytes:
        if not self._items:
            return b""
        item = self._items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _FakeProc:
    def __init__(self, lines: list[bytes], rc: int) -> None:
        self.stdout = _Lines(lines)
        self.returncode: int | None = None
        self._rc = rc
        self.pid = 4242
        self.killed = False

    async def wait(self) -> int:
        self.returncode = self._rc
        return self._rc

    def kill(self) -> None:
        self.killed = True


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


@pytest.fixture(autouse=True)
def group_signals(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, signal.Signals]]:
    """No test signals a real process group (the fake child's pid is arbitrary).

    Records each ``(pgid, signal)`` and answers "no such group", so the runner
    falls back to killing the child itself. A test that wants the group path sets
    ``group_signals.found = True``.
    """

    class _Recorder(list):  # type: ignore[type-arg]
        found = False

    calls = _Recorder()

    def _fake(pgid: int, sig: signal.Signals) -> bool:
        calls.append((pgid, sig))
        return calls.found

    monkeypatch.setattr(repair_route, "_signal_group", _fake)
    return calls


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
    assert final["audit_finished"] is True  # its `finished` event was logged
    assert (log_dir / f"job-{job['id']}.log").read_text().splitlines() == [
        "[run ] preflight",
        "other",
        "[run ] install",
    ]
    argv = spawn.await_args.args
    assert argv[1:] == ("agent", "bootstrap", "hermes", "--repair", "--verbose")
    assert "HERMES_HOME" not in spawn.await_args.kwargs["env"]
    # Its own session, so the whole process group can be signalled with it.
    assert spawn.await_args.kwargs["start_new_session"] is True


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
        "actor",
    }
    assert job["kind"] == "repair" and job["agent"] == "hermes"
    assert job["error"] is None and job["steps"] == []
    assert job["api_pid"] == os.getpid()
    assert job["actor"] == "hal0-dashboard"  # no X-hal0-Agent header
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

    with structlog.testing.capture_logs() as logs:
        job = agents_client.post("/api/agents/hermes/repair").json()
        _drain_background(agents_client)

    final = agents_client.get(f"/api/updates/status/{job['id']}").json()
    assert final["state"] == "failed"
    assert final["error"].startswith("could not start bootstrap")
    assert "exit_code" not in final
    on_disk = json.loads((updater_routes._jobs_dir() / f"{job['id']}.json").read_text())
    assert on_disk["state"] == "failed" and on_disk["error"] == final["error"]
    events = [e for e in logs if e["event"].startswith("agent.repair")]
    assert [e["event"] for e in events] == ["agent.repair.invoked", "agent.repair.finished"]
    assert events[1]["state"] == "failed" and events[1]["exit_code"] is None


def test_repair_step_without_a_trailing_newline_at_eof_is_recorded(
    agents_client: TestClient, log_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _spawn_ok(monkeypatch, [b"[run ] one\n", b"[run ] final"])

    job = agents_client.post("/api/agents/hermes/repair").json()
    _drain_background(agents_client)

    final = agents_client.get(f"/api/updates/status/{job['id']}").json()
    assert final["steps"] == ["one", "final"]
    assert (log_dir / f"job-{job['id']}.log").read_text().splitlines()[-1] == "[run ] final"


def test_repair_blank_step_names_are_skipped(
    agents_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _spawn_ok(monkeypatch, [b"[run ]   \n", b"[run ] one\n", b"[run ] \r\n"])

    job = agents_client.post("/api/agents/hermes/repair").json()
    _drain_background(agents_client)

    assert agents_client.get(f"/api/updates/status/{job['id']}").json()["steps"] == ["one"]


def test_repair_transcript_is_not_readable_by_other_users(
    agents_client: TestClient, log_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _spawn_ok(monkeypatch, [b"[run ] one\n"])

    job = agents_client.post("/api/agents/hermes/repair").json()
    _drain_background(agents_client)

    log_file = log_dir / f"job-{job['id']}.log"
    # 0640 and 0750 before the umask: nothing for others, no group write.
    assert stat.S_IMODE(log_file.stat().st_mode) & 0o137 == 0
    assert stat.S_IMODE(log_dir.stat().st_mode) & 0o027 == 0


def test_repair_times_out_a_child_that_never_finishes(
    agents_client: TestClient,
    group_signals: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stalled build must not leave the job running: it 409s every later job."""
    proc = _FakeProc([], 0)

    async def _stalled() -> bytes:
        await asyncio.sleep(3600)
        return b""

    proc.stdout.readline = _stalled  # type: ignore[method-assign]
    monkeypatch.setattr(repair_route, "_spawn", AsyncMock(return_value=proc))
    monkeypatch.setattr(repair_route, "_BOOTSTRAP_TIMEOUT_S", 0.05)
    group_signals.found = True

    with structlog.testing.capture_logs() as logs:
        job = agents_client.post("/api/agents/hermes/repair").json()
        _drain_background(agents_client)

    final = agents_client.get(f"/api/updates/status/{job['id']}").json()
    assert final["state"] == "failed"
    assert final["error"] == f"bootstrap timed out after {int(repair_route._BOOTSTRAP_TIMEOUT_S)}s"
    assert final["exit_code"] == proc.returncode
    assert group_signals == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]
    events = [e for e in logs if e["event"].startswith("agent.repair")]
    assert [e["event"] for e in events] == ["agent.repair.invoked", "agent.repair.finished"]
    assert events[1]["state"] == "failed" and events[1]["exit_code"] == proc.returncode
    # And the registry is free again.
    _spawn_ok(monkeypatch)
    assert agents_client.post("/api/agents/hermes/repair").status_code == 202
    _drain_background(agents_client)


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


#: Daemon secrets and operator settings that must never reach the provisioner.
_PLANTED_SECRETS = {
    "HF_TOKEN": "hf_planted",
    "HUGGING_FACE_HUB_TOKEN": "hf_planted2",
    "OPENAI_API_KEY": "sk-planted",
    "HAL0_ADMIN_KEY": "admin-planted",
    "HAL0_CLIENT_KEY": "client-planted",
    "HAL0_HERMES_API_KEY__CODER": "hermes-planted",
    "HAL0_SENTRY_DSN": "https://planted@sentry.invalid/1",
    "HAL0_HERMES_TERMINAL": "local",
    "HERMES_HOME": "/somewhere/else",
    "SSL_CERT_FILE": "/etc/planted.pem",
    "PIP_INDEX_URL": "https://user:pw@index.invalid/simple",
}


def _child_env_for(monkeypatch: pytest.MonkeyPatch, **extra: str) -> dict[str, str]:
    for key in list(os.environ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(repair_route, "hal0_service_home", lambda: "/srv/hal0")
    for key, value in {**_PLANTED_SECRETS, **extra}.items():
        monkeypatch.setenv(key, value)
    return repair_route._child_env()


def test_repair_child_env_drops_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _child_env_for(monkeypatch, HAL0_FUTURE_SECRET="s3cret", ANOTHER_UPSTREAM_KEY="k")
    for name in (*_PLANTED_SECRETS, "HAL0_FUTURE_SECRET", "ANOTHER_UPSTREAM_KEY"):
        assert name not in env, name
    assert "s3cret" not in env.values()


@pytest.mark.parametrize(
    "name",
    [
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "ALL_PROXY",
        "HAL0_HOME",
        "HAL0_API_URL",
        "HAL0_DASHBOARD_URL",
        "HAL0_BIND_HOST",
        "HAL0_PORT",
        "HAL0_HOSTNAME",
        "HAL0_HERMES_PYTHON",
        "HAL0_HERMES_LIVE_RESOLVE",
        "HAL0_INFERENCE_BASE",
        "HAL0_LOG_LEVEL",
    ],
)
def test_repair_child_env_passes_allow_listed(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    assert _child_env_for(monkeypatch, **{name: "kept"})[name] == "kept"


def test_repair_child_env_keeps_what_the_repair_needs(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _child_env_for(
        monkeypatch, PATH="/usr/bin:/bin", HAL0_API_URL="http://127.0.0.1:8080", HOME="/root"
    )
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HAL0_API_URL"] == "http://127.0.0.1:8080"
    assert env["HOME"] == "/srv/hal0"
    assert env["PYTHONUNBUFFERED"] == "1"
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


def test_repair_500_when_builder_lock_cannot_be_opened(
    agents_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lock file cannot be created: a server-side fault, not a 409.

    The cards UI reads every 409 from this route as "a build is running"; a
    permissions problem must not look like a busy builder for good.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    unusable = Layout.for_venv(blocker / "venvs" / "hermes")
    monkeypatch.setattr(repair_route, "_layout", lambda: unusable)
    spawn = _spawn_ok(monkeypatch)

    r = agents_client.post("/api/agents/hermes/repair")

    assert r.status_code == 500
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


def test_repair_does_not_invalidate_the_overview_when_it_is_refused(
    app_client: tuple[FastAPI, TestClient], layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 404 and a 409 change nothing the card shows."""
    app, client = app_client
    _spawn_ok(monkeypatch)
    seen: list[str] = []
    monkeypatch.setattr(overview_route, "invalidate", seen.append)

    assert client.post("/api/agents/nope/repair").status_code == 404
    app.state.update_jobs = {"j1": {"id": "j1", "state": "running", "phase": "converge:hermes"}}
    assert client.post("/api/agents/hermes/repair").status_code == 409
    app.state.update_jobs = {}
    with builder_lock(layout, caller="cli", wait=False):
        assert client.post("/api/agents/hermes/repair").status_code == 409

    assert seen == []


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

    def _record(job: dict[str, Any]) -> None:
        states.append(job["state"])

    monkeypatch.setattr(repair_route, "_persist_job", _record)
    monkeypatch.setattr(updater_routes, "_persist_job", _record)  # the reconcile's own write
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
    proc = _FakeProc([], 7)

    async def _boom() -> bytes:
        raise RuntimeError("the pipe broke")

    proc.stdout.readline = _boom  # type: ignore[method-assign]
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
    # The code of the child it reaped, not a guess.
    assert jobs["j1"]["exit_code"] == proc.returncode == 7


async def test_runner_skips_an_overlong_output_line_and_keeps_the_build_going(
    tmp_path: Path, no_persist: list[str]
) -> None:
    """``StreamReader.readline`` raises ``ValueError`` for a line over the limit: not fatal."""

    proc = _FakeProc([], 0)
    proc.stdout = _OverlongOnce(  # type: ignore[assignment]
        [b"[run ] before\n", ValueError("Separator is not found, and chunk exceed the limit")],
        [b"[run ] after\n"],
    )
    jobs = _job()
    log = tmp_path / "x.log"

    with structlog.testing.capture_logs() as logs:
        await repair_route.run_repair_job(
            jobs,
            "j1",
            argv=["hal0"],
            env={},
            log_path=log,
            spawn=AsyncMock(return_value=proc),
        )

    assert jobs["j1"]["state"] == "applied"
    assert jobs["j1"]["steps"] == ["before", "after"]
    assert log.read_text().splitlines() == [
        "[run ] before",
        f"[hal0] a line over {repair_route._STREAM_LIMIT} bytes was skipped",
        "[run ] after",
    ]
    assert [e["event"] for e in logs if e["event"] == "agent.repair.line_skipped"] == [
        "agent.repair.line_skipped"
    ]


async def test_runner_survives_a_real_stream_reader_overrun(
    tmp_path: Path, no_persist: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same with asyncio's own reader: the long line is dropped, the next line is read."""
    reader = asyncio.StreamReader(limit=64)
    reader.feed_data(b"[run ] first\n" + b"x" * 500 + b"\n[run ] second\n")
    reader.feed_eof()
    proc = _FakeProc([], 0)
    proc.stdout = reader  # type: ignore[assignment]
    jobs = _job()

    await repair_route.run_repair_job(
        jobs,
        "j1",
        argv=["hal0"],
        env={},
        log_path=tmp_path / "x.log",
        spawn=AsyncMock(return_value=proc),
    )

    assert jobs["j1"]["state"] == "applied"
    assert jobs["j1"]["steps"] == ["first", "second"]
    assert (tmp_path / "x.log").read_text().count("was skipped") == 1


async def test_runner_skips_a_limit_overrun_error_too(
    tmp_path: Path, no_persist: list[str]
) -> None:
    proc = _FakeProc([], 0)
    proc.stdout = _OverlongOnce(  # type: ignore[assignment]
        [asyncio.LimitOverrunError("Separator is found, but chunk is longer than limit", 99)],
        [b"[run ] next\n"],
    )
    jobs = _job()

    await repair_route.run_repair_job(
        jobs,
        "j1",
        argv=["hal0"],
        env={},
        log_path=tmp_path / "x.log",
        spawn=AsyncMock(return_value=proc),
    )

    assert jobs["j1"]["state"] == "applied" and jobs["j1"]["steps"] == ["next"]


async def _run_hanging(
    tmp_path: Path, jobs: dict[str, dict[str, Any]], proc: _FakeProc | None, spawn: Any = None
) -> asyncio.Task[None]:
    """Start the runner on a child whose output never ends, and let it get going."""
    if proc is not None:

        async def _never() -> bytes:
            await asyncio.sleep(3600)
            return b""

        proc.stdout.readline = _never  # type: ignore[method-assign]
    task = asyncio.create_task(
        repair_route.run_repair_job(
            jobs,
            "j1",
            argv=["hal0"],
            env={},
            log_path=tmp_path / "x.log",
            spawn=spawn if spawn is not None else AsyncMock(return_value=proc),
            actor="tester",
        )
    )
    for _ in range(10):
        await asyncio.sleep(0)
    return task


async def test_runner_cancellation_kills_the_child_and_leaves_the_job_running(
    tmp_path: Path, no_persist: list[str], group_signals: Any
) -> None:
    proc = _FakeProc([], 0)
    jobs = _job()
    task = await _run_hanging(tmp_path, jobs, proc)

    with structlog.testing.capture_logs() as logs:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert proc.killed  # no process group in this test: the child itself
    assert jobs["j1"]["state"] == "running"
    assert no_persist[-1] == "running"  # still running on disk, for the reconcile
    assert jobs["j1"]["audit_finished"] is True  # but its `finished` event was logged
    finished = [e for e in logs if e["event"] == "agent.repair.finished"]
    assert len(finished) == 1
    assert finished[0]["state"] == "cancelled" and finished[0]["exit_code"] is None
    assert finished[0]["actor"] == "tester"


async def test_runner_cancelled_before_it_started_is_persisted_failed(
    tmp_path: Path, no_persist: list[str]
) -> None:
    async def _slow_spawn(*args: Any, **kwargs: Any) -> _FakeProc:
        await asyncio.sleep(3600)
        raise AssertionError("not reached")

    jobs = _job()
    task = await _run_hanging(tmp_path, jobs, None, spawn=_slow_spawn)

    with structlog.testing.capture_logs() as logs:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert jobs["j1"]["state"] == "failed"
    assert jobs["j1"]["error"] == "repair cancelled"
    assert no_persist == ["failed"]
    finished = [e for e in logs if e["event"] == "agent.repair.finished"]
    assert [e["state"] for e in finished] == ["cancelled"]


async def test_terminate_signals_the_group_then_kills_it_and_never_hangs(
    monkeypatch: pytest.MonkeyPatch, group_signals: Any
) -> None:
    """A child that ignores SIGTERM and never exits is still given up on."""
    monkeypatch.setattr(repair_route, "_TERM_GRACE_S", 0.05)
    group_signals.found = True
    proc = _FakeProc([], 0)

    async def _never_exits() -> int:
        await asyncio.sleep(3600)
        return 0

    proc.wait = _never_exits  # type: ignore[method-assign]

    await asyncio.wait_for(repair_route._terminate(proc), 5)

    assert group_signals == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]
    assert proc.killed  # the direct kill of the child, as the last resort


async def test_terminate_without_a_group_falls_back_to_the_child(group_signals: Any) -> None:
    proc = _FakeProc([], 0)

    await repair_route._terminate(proc)

    assert proc.killed
    assert [sig for _pgid, sig in group_signals] == [signal.SIGTERM, signal.SIGKILL]


@pytest.mark.skipif(
    not hasattr(os, "killpg") or not Path("/proc").is_dir(),
    reason="needs process groups and /proc to see a grandchild die",
)
async def test_terminate_stops_descendants_of_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the real signals: a grandchild that holds the pipe dies with the group."""
    monkeypatch.setattr(repair_route, "_signal_group", _REAL_SIGNAL_GROUP)
    proc = await asyncio.create_subprocess_exec(
        "sh",
        "-c",
        "sleep 300 & echo $!; wait",
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert proc.stdout is not None
        grandchild = int((await asyncio.wait_for(proc.stdout.readline(), 10)).decode())

        def _running(pid: int) -> bool:
            try:
                state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
            except OSError:
                return False
            return state != "Z"

        assert _running(grandchild)
        await asyncio.wait_for(repair_route._terminate(proc), 15)
        for _ in range(100):
            if not _running(grandchild):
                break
            await asyncio.sleep(0.05)

        assert not _running(grandchild)
        assert proc.returncode is not None
    finally:
        # A failed assertion above must not leave ``sh`` and ``sleep 300`` behind.
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), 5)


# ── a repair that hal0-api restarted under ───────────────────────────────────

#: Above Linux's ``pid_max`` (2**22), so no process has it and ``os.kill(pid, 0)``
#: answers "no such process".
_DEAD_PID = 4_194_305

_RESTARTED_ERROR = "hal0-api restarted during the repair; the live release is unchanged"


def _stale_job(state: str = "running", job_id: str = "6000000000aa") -> dict[str, Any]:
    """A repair job the hal0-api before this one left in flight."""
    job: dict[str, Any] = {
        "id": job_id,
        "state": state,
        "phase": "repair:hermes",
        "kind": "repair",
        "agent": "hermes",
        "api_pid": _DEAD_PID,
        "steps": ["preflight"],
        "created_at": 1.0,
        "updated_at": 1.0,
        "error": None,
        "log_path": None,
    }
    if state == "running":
        job["pid"] = _DEAD_PID
    return job


def _persisted(job_id: str) -> dict[str, Any]:
    return json.loads((updater_routes._jobs_dir() / f"{job_id}.json").read_text())


@pytest.mark.parametrize("state", ["running", "queued"])
def test_stale_repair_job_marked_api_restarted_on_a_status_read(
    app_client: tuple[FastAPI, TestClient], state: str
) -> None:
    app, client = app_client
    updater_routes._persist_job(_stale_job(state))
    app.state.update_jobs = {}  # a fresh process: nothing in memory

    with structlog.testing.capture_logs() as logs:
        final = client.get("/api/updates/status/6000000000aa").json()

    assert final["state"] == "failed"
    assert final["error_code"] == "api_restarted"
    assert final["error"] == _RESTARTED_ERROR
    assert final["steps"] == ["preflight"]
    # Written down, and the in-memory copy the status poll re-seeded says the same.
    assert _persisted("6000000000aa")["error_code"] == "api_restarted"
    assert app.state.update_jobs["6000000000aa"]["state"] == "failed"
    finished = [e for e in logs if e["event"] == "agent.repair.finished"]
    assert [(e["job_id"], e["state"], e["exit_code"], e["error_code"]) for e in finished] == [
        ("6000000000aa", "failed", None, "api_restarted")
    ]
    # A second poll does not reconcile it again.
    with structlog.testing.capture_logs() as again:
        assert client.get("/api/updates/status/6000000000aa").json()["state"] == "failed"
    assert not [e for e in again if e["event"] == "agent.repair.finished"]


def test_stale_repair_job_in_memory_is_marked_on_a_status_read(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    app.state.update_jobs = {"6000000000aa": _stale_job()}

    final = client.get("/api/updates/status/6000000000aa").json()

    assert (final["state"], final["error_code"]) == ("failed", "api_restarted")


def test_status_read_leaves_other_jobs_alone(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    converge = {
        "id": "c1",
        "state": "running",
        "phase": "converge:hermes",
        "api_pid": _DEAD_PID,
        "pid": _DEAD_PID,
    }
    app.state.update_jobs = {"c1": dict(converge)}

    assert client.get("/api/updates/status/c1").json()["state"] == "running"


def test_a_reseeded_stale_repair_job_does_not_block_the_next_repair(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The poll that re-seeds the registry is the one that must not leave ``running`` behind."""
    app, client = app_client
    _spawn_ok(monkeypatch)
    updater_routes._persist_job(_stale_job())
    app.state.update_jobs = {}
    assert client.get("/api/updates/status/6000000000aa").json()["state"] == "failed"

    r = client.post("/api/agents/hermes/repair")

    assert r.status_code == 202
    _drain_background(client)


def test_a_repair_job_reloaded_from_disk_is_failed_whatever_its_pids_say(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing in memory means no runner of ours: even a matching ``api_pid`` and a live ``pid``."""
    app, client = app_client
    _spawn_ok(monkeypatch)
    job = _stale_job()
    job["api_pid"] = os.getpid()
    job["pid"] = os.getpid()
    updater_routes._persist_job(job)
    app.state.update_jobs = {}

    final = client.get("/api/updates/status/6000000000aa").json()

    assert (final["state"], final["error_code"]) == ("failed", "api_restarted")
    assert final["error"] == _RESTARTED_ERROR
    assert client.post("/api/agents/hermes/repair").status_code == 202
    _drain_background(client)


def test_repair_post_over_a_persisted_running_job_with_a_dead_api_pid(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = app_client
    _spawn_ok(monkeypatch)
    updater_routes._persist_job(_stale_job())
    app.state.update_jobs = {}

    r = client.post("/api/agents/hermes/repair")

    assert r.status_code == 202
    _drain_background(client)
    old = client.get("/api/updates/status/6000000000aa").json()
    assert (old["state"], old["error_code"]) == ("failed", "api_restarted")
    assert client.get(f"/api/updates/status/{r.json()['id']}").json()["state"] == "applied"


@pytest.mark.parametrize("state", ["running", "queued"])
def test_repair_post_reconciles_a_stale_job_before_the_busy_check(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    """In the registry already (no status poll in between), a stale repair is not "in flight"."""
    app, client = app_client
    spawn = _spawn_ok(monkeypatch)
    app.state.update_jobs = {"6000000000aa": _stale_job(state)}

    r = client.post("/api/agents/hermes/repair")

    assert r.status_code == 202
    _drain_background(client)
    spawn.assert_awaited_once()
    assert app.state.update_jobs["6000000000aa"]["state"] == "failed"
    assert app.state.update_jobs["6000000000aa"]["error_code"] == "api_restarted"
    assert _persisted("6000000000aa")["error_code"] == "api_restarted"


def test_repair_post_still_refuses_a_job_this_process_is_running(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = app_client
    spawn = _spawn_ok(monkeypatch)
    live = _stale_job("queued", "7000000000aa")
    live["api_pid"] = os.getpid()
    app.state.update_jobs = {"7000000000aa": live}

    r = client.post("/api/agents/hermes/repair")

    assert r.status_code == 409
    assert r.json()["error"]["details"] == {"job_id": "7000000000aa"}
    assert app.state.update_jobs["7000000000aa"]["state"] == "queued"
    spawn.assert_not_awaited()


def test_reconcile_leaves_live_job_alone() -> None:
    job = {"state": "running", "phase": "repair:hermes", "api_pid": os.getpid(), "pid": 1}

    out = repair_route.reconcile_stale_repair_job(job, pid_alive=lambda p: True)

    assert out is job
    assert job["state"] == "running"
    assert "error_code" not in job


def test_reconcile_marks_a_job_of_another_api_process_failed(
    no_persist: list[str],
) -> None:
    job = {
        "id": "6000000000aa",
        "state": "running",
        "phase": "repair:hermes",
        "api_pid": 7,
        "pid": 1,
    }

    out = repair_route.reconcile_stale_repair_job(
        job,
        current_pid=8,
        pid_alive=lambda p: True,  # the child lives; its owner is gone
    )

    assert out is job
    assert (job["state"], job["error_code"], job["error"]) == (
        "failed",
        "api_restarted",
        _RESTARTED_ERROR,
    )
    assert no_persist == ["failed"]


def test_reconcile_marks_a_job_whose_child_is_gone_failed(no_persist: list[str]) -> None:
    """The new hal0-api got the old one's pid: ``api_pid`` matches, the child does not exist."""
    job = {
        "id": "6000000000aa",
        "state": "running",
        "phase": "repair:hermes",
        "api_pid": 8,
        "pid": 1,
    }

    repair_route.reconcile_stale_repair_job(job, current_pid=8, pid_alive=lambda p: False)

    assert (job["state"], job["error_code"]) == ("failed", "api_restarted")


def test_reconcile_leaves_a_job_of_this_process_whose_runner_is_alive_alone(
    no_persist: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child exited and was reaped; the runner has not yet read its last line."""
    job = {
        "id": "6000000000aa",
        "state": "running",
        "phase": "repair:hermes",
        "api_pid": 8,
        "pid": 1,
    }
    monkeypatch.setattr(repair_route, "_live_runners", {"6000000000aa"})

    repair_route.reconcile_stale_repair_job(job, current_pid=8, pid_alive=lambda p: False)

    assert job["state"] == "running"
    assert no_persist == []


def test_reconcile_leaves_a_queued_job_of_this_process_alone(no_persist: list[str]) -> None:
    job = {"id": "6000000000aa", "state": "queued", "phase": "repair:hermes", "api_pid": 8}

    repair_route.reconcile_stale_repair_job(job, current_pid=8, pid_alive=lambda p: False)

    assert job["state"] == "queued"


@pytest.mark.parametrize(
    "job",
    [
        {"state": "applied", "phase": "repair:hermes", "api_pid": 7, "pid": 1},
        {"state": "failed", "phase": "repair:hermes", "api_pid": 7, "pid": 1},
        {"state": "running", "phase": "converge:hermes", "api_pid": 7, "pid": 1},
        {"state": "running", "phase": "apply"},
    ],
)
def test_reconcile_leaves_terminal_and_other_jobs_alone(
    job: dict[str, Any], no_persist: list[str]
) -> None:
    before = dict(job)

    out = repair_route.reconcile_stale_repair_job(job, current_pid=8, pid_alive=lambda p: False)

    assert out is job and job == before
    assert no_persist == []


@pytest.mark.parametrize("pid", [None, 0, -1, True, "12", 2**31, 2**70])
def test_pid_alive_is_false_for_what_is_not_a_pid(pid: object) -> None:
    assert repair_route._pid_alive(pid) is False


def test_a_corrupt_child_pid_fails_the_job_instead_of_the_status_route(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``os.kill(2**31, 0)`` raises ``OverflowError``: not a 500 on the status route or the POST."""
    app, client = app_client
    _spawn_ok(monkeypatch)
    job = _stale_job()
    job["api_pid"] = os.getpid()
    job["pid"] = 2**31
    app.state.update_jobs = {"6000000000aa": job}

    final = client.get("/api/updates/status/6000000000aa").json()

    assert (final["state"], final["error_code"]) == ("failed", "api_restarted")
    app.state.update_jobs = {
        "7000000000aa": {**_stale_job(job_id="7000000000aa"), "api_pid": os.getpid(), "pid": 2**31}
    }
    assert client.post("/api/agents/hermes/repair").status_code == 202
    _drain_background(client)


def test_pid_alive_sees_this_process_and_not_a_dead_one() -> None:
    assert repair_route._pid_alive(os.getpid()) is True
    assert repair_route._pid_alive(_DEAD_PID) is False


async def test_runner_that_cannot_open_its_log_does_not_stay_a_live_runner(
    no_persist: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected error before the job runs must not pin its id in ``_live_runners``."""

    def _boom(log_path: Path, job: dict[str, Any]) -> Any:
        raise RuntimeError("not an OSError")

    monkeypatch.setattr(repair_route, "_open_log", _boom)
    spawn = AsyncMock()

    with pytest.raises(RuntimeError):
        await repair_route.run_repair_job(
            _job(), "j1", argv=["hal0"], env={}, log_path=tmp_path / "job.log", spawn=spawn
        )

    assert "j1" not in repair_route._live_runners
    spawn.assert_not_awaited()


async def test_runner_is_a_live_runner_only_while_it_runs(
    no_persist: list[str], tmp_path: Path
) -> None:
    jobs = _job()
    seen: list[bool] = []

    class _Watching(_Lines):
        async def readline(self) -> bytes:
            seen.append("j1" in repair_route._live_runners)
            return await super().readline()

    proc = _FakeProc([], 0)
    proc.stdout = _Watching([b"[run ] preflight\n"])  # type: ignore[assignment]
    spawn = AsyncMock(return_value=proc)

    await repair_route.run_repair_job(
        jobs, "j1", argv=["hal0"], env={}, log_path=tmp_path / "job.log", spawn=spawn
    )

    assert seen and all(seen)
    assert "j1" not in repair_route._live_runners


# ── the actor and the single `finished` event ────────────────────────────────


def test_repair_job_records_who_asked_for_it(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = app_client
    _spawn_ok(monkeypatch)

    job = client.post("/api/agents/hermes/repair", headers={"x-hal0-agent": "ops-bot"}).json()

    assert job["actor"] == "ops-bot"
    assert _persisted(job["id"])["actor"] == "ops-bot"
    _drain_background(client)


def test_a_reconciled_repair_job_logs_finished_with_the_actor_it_recorded(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    job = _stale_job()
    job["actor"] = "ops-bot"
    updater_routes._persist_job(job)
    app.state.update_jobs = {}

    with structlog.testing.capture_logs() as logs:
        assert client.get("/api/updates/status/6000000000aa").json()["state"] == "failed"

    (finished,) = [e for e in logs if e["event"] == "agent.repair.finished"]
    assert finished["actor"] == "ops-bot"
    assert (finished["state"], finished["exit_code"], finished["error_code"]) == (
        "failed",
        None,
        "api_restarted",
    )


def test_reconcile_does_not_log_finished_twice(no_persist: list[str]) -> None:
    """The runner that was cancelled by the shutdown already logged its ending."""
    job = {
        "id": "6000000000aa",
        "state": "running",
        "phase": "repair:hermes",
        "api_pid": 7,
        "actor": "ops-bot",
        "audit_finished": True,
    }

    with structlog.testing.capture_logs() as logs:
        repair_route.reconcile_stale_repair_job(job, current_pid=8)

    assert job["state"] == "failed" and job["error_code"] == "api_restarted"
    assert [e for e in logs if e["event"] == "agent.repair.finished"] == []


async def test_a_runner_cancelled_by_shutdown_and_the_reconcile_log_one_finished_between_them(
    tmp_path: Path, no_persist: list[str]
) -> None:
    proc = _FakeProc([], 0)
    jobs = _job()
    jobs["j1"]["actor"] = "ops-bot"
    task = await _run_hanging(tmp_path, jobs, proc)
    with structlog.testing.capture_logs() as logs:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # hal0-api restarts; the new process reads the job back from disk.
        persisted = dict(jobs["j1"])
        repair_route.reconcile_stale_repair_job(persisted, reloaded=True)

    assert persisted["state"] == "failed" and persisted["error_code"] == "api_restarted"
    finished = [e for e in logs if e["event"] == "agent.repair.finished"]
    assert [(e["state"], e["actor"]) for e in finished] == [("cancelled", "tester")]


# ── what the child and the transcript can do to the runner ──────────────────


def test_a_transcript_path_that_is_a_symlink_is_not_followed(
    tmp_path: Path,
) -> None:
    target = tmp_path / "elsewhere"
    target.write_text("untouched")
    link = tmp_path / "job-x.log"
    link.symlink_to(target)
    job: dict[str, Any] = {"log_path": str(link)}

    assert repair_route._open_log(link, job) is None

    assert job["log_path"] is None
    assert target.read_text() == "untouched"


@pytest.mark.parametrize("pgid", [1, 0, -1, -4242])
def test_signal_group_never_signals_init_or_our_own_group(
    pgid: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _must_not_be_called(*args: Any) -> None:
        raise AssertionError("os.killpg was called")

    monkeypatch.setattr(os, "killpg", _must_not_be_called)

    assert _REAL_SIGNAL_GROUP(pgid, signal.SIGKILL) is False


def test_signal_group_still_signals_a_real_group(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: sent.append((pgid, sig)))

    assert _REAL_SIGNAL_GROUP(4242, signal.SIGTERM) is True
    assert sent == [(4242, signal.SIGTERM)]


# ── what a running repair does to the other routes ──────────────────────────


def test_a_converge_is_refused_while_a_repair_is_running(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """The registry is the shared busy signal: the converge route sees a repair job."""
    app, client = app_client
    live = _stale_job()
    live["api_pid"] = os.getpid()
    app.state.update_jobs = {live["id"]: live}

    resp = client.post("/api/updates/components/hermes/converge")

    assert resp.status_code == 409
    assert app.state.update_jobs[live["id"]]["state"] == "running"  # and it was not "reconciled"
