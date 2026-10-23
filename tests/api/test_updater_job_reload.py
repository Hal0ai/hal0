"""``GET /api/updates/status/{id}`` for a job that comes back from disk.

Job tasks run inside hal0-api, and the registry (``app.state.update_jobs``) is
process-local, so a job found on disk but not in memory was started by a
process that is gone. One that is still ``queued`` or ``running`` was killed
with it and must read ``failed`` / ``api_restarted`` instead of ``running`` for
good (which refuses every later update, converge and repair with 409). A
terminal job reloads unchanged, and a restart that is part of a job's own plan
(apply and commit restart hal0-api after persisting ``applied``, #1540) never
leaves it unfinished.

A new process is simulated by emptying ``app.state.update_jobs`` after the job
file is written; nothing here starts a real child or restarts anything.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api.routes import updater as u_mod

_GENERIC_ERROR = (
    "hal0-api restarted before this job finished; check `hal0 update status` before retrying"
)
_BUILD_ERROR = "hal0-api restarted during the build; the live release is unchanged"
_REPAIR_ERROR = "hal0-api restarted during the repair; the live release is unchanged"


@pytest.fixture
def app_client(tmp_hal0_home: str) -> Iterator[tuple[FastAPI, TestClient]]:
    app = create_app()
    with TestClient(app) as client:
        yield app, client


def _job(job_id: str, state: str, phase: str | None, **extra: Any) -> dict[str, Any]:
    """A job record as the routes write it; the apply route sets no ``phase``."""
    job: dict[str, Any] = {
        "id": job_id,
        "state": state,
        "created_at": 1.0,
        "updated_at": 1.0,
        "error": None,
        **extra,
    }
    if phase is not None:
        job["phase"] = phase
    return job


def _persist_only(app: FastAPI, job: dict[str, Any]) -> None:
    """The job is on disk and nothing is in memory: a new hal0-api process."""
    u_mod._persist_job(job)
    app.state.update_jobs = {}


def _on_disk(job_id: str) -> dict[str, Any]:
    return json.loads((u_mod._jobs_dir() / f"{job_id}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("state", ["running", "queued"])
@pytest.mark.parametrize(
    ("phase", "message"),
    [
        ("converge:hermes", _BUILD_ERROR),
        # Only the Hermes release builder is atomic; the others converge in place.
        ("converge:openwebui", _GENERIC_ERROR),
        ("converge:hindsight", _GENERIC_ERROR),
        (None, _GENERIC_ERROR),  # the apply route sets no phase
        ("prepare", _GENERIC_ERROR),
        ("commit", _GENERIC_ERROR),
        ("something-new", _GENERIC_ERROR),
    ],
)
def test_an_unfinished_job_from_disk_reads_failed_api_restarted(
    app_client: tuple[FastAPI, TestClient], state: str, phase: str | None, message: str
) -> None:
    app, client = app_client
    _persist_only(app, _job("1000000000aa", state, phase, channel="stable"))

    final = client.get("/api/updates/status/1000000000aa").json()

    assert final["state"] == "failed"
    assert final["error_code"] == "api_restarted"
    assert final["error"] == message
    assert final["updated_at"] > 1.0
    assert final["channel"] == "stable"  # the rest of the record is as it was
    # Written down, and the registry the poll re-seeded says the same.
    assert _on_disk("1000000000aa")["error_code"] == "api_restarted"
    assert app.state.update_jobs["1000000000aa"]["state"] == "failed"
    # Reading it again does not touch it.
    assert client.get("/api/updates/status/1000000000aa").json() == final


@pytest.mark.parametrize(
    "job",
    [
        _job("1000000000aa", "applied", None, restarted=None, restart_error=None),
        _job(
            "1000000000aa", "applied", "converge:hermes", component_result={"status": "converged"}
        ),
        _job("1000000000aa", "failed", "commit", error="boom", error_code="UpdateError"),
        _job("1000000000aa", "failed", "converge:hermes", error="build failed"),
        _job(
            "1000000000aa",
            "prepared",
            "prepare",
            resolved_version="9.9.9",
            notes="release notes",
            channel="x",
        ),
    ],
    ids=["applied-apply", "applied-converge", "failed-commit", "failed-converge", "prepared"],
)
def test_a_finished_job_reloads_unchanged(
    app_client: tuple[FastAPI, TestClient], job: dict[str, Any]
) -> None:
    app, client = app_client
    _persist_only(app, dict(job))

    final = client.get("/api/updates/status/1000000000aa").json()

    assert final == job
    assert _on_disk("1000000000aa") == job


@pytest.mark.parametrize("state", ["running", "queued"])
@pytest.mark.parametrize(
    ("component", "result", "message"),
    [
        ("hermes", {"status": "build_failed", "error": "stub"}, _BUILD_ERROR),
        ("openwebui", {"status": "upgraded", "from": "a", "to": "b"}, _GENERIC_ERROR),
    ],
)
def test_a_converge_is_accepted_after_a_dead_one_was_polled(
    app_client: tuple[FastAPI, TestClient],
    state: str,
    component: str,
    result: dict[str, Any],
    message: str,
) -> None:
    """The poll that re-seeds the registry must not leave a ``running`` job in it."""
    app, client = app_client
    _persist_only(app, _job("1000000000aa", state, f"converge:{component}"))
    dead = client.get("/api/updates/status/1000000000aa").json()
    assert (dead["state"], dead["error_code"], dead["error"]) == (
        "failed",
        "api_restarted",
        message,
    )

    with patch("hal0.components.runner.converge_component", return_value=result):
        resp = client.post(f"/api/updates/components/{component}/converge")
        job_id = resp.json()["id"]
        for _ in range(100):
            job = client.get(f"/api/updates/status/{job_id}").json()
            if job["state"] in ("applied", "failed"):
                break
            time.sleep(0.05)

    assert resp.status_code == 202
    assert job["state"] in ("applied", "failed") and job["state"] != "queued"


def test_a_running_job_of_this_process_is_not_touched(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    live = _job("1000000000aa", "running", "converge:hermes")
    app.state.update_jobs = {"1000000000aa": live}
    u_mod._persist_job(live)

    final = client.get("/api/updates/status/1000000000aa").json()

    assert final["state"] == "running" and "error_code" not in final
    # And it still refuses the next one.
    assert client.post("/api/updates/components/hermes/converge").status_code == 409


def test_an_unknown_job_is_still_404(app_client: tuple[FastAPI, TestClient]) -> None:
    assert app_client[1].get("/api/updates/status/nope").status_code == 404


# ── a restart that is part of the job's own plan ─────────────────────────────


class _RestartKilledUs(Exception):
    """Stands for the process dying inside ``_try_restart_hal0_api`` (never caught by the job)."""


def _kill_at_restart(
    monkeypatch: pytest.MonkeyPatch, job_id: str, seen_on_disk: list[dict[str, Any]]
) -> None:
    """``_try_restart_hal0_api`` that records what is on disk, then dies with its process.

    ``asyncio.CancelledError`` is what the task sees when the process is torn
    down around it; it is raised from the worker thread the restart runs in.
    """

    def _restart() -> tuple[bool | None, str | None]:
        seen_on_disk.append(_on_disk(job_id))
        raise asyncio.CancelledError

    monkeypatch.setattr(u_mod, "_try_restart_hal0_api", _restart)


def test_apply_job_killed_by_its_own_restart_reloads_applied(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = app_client

    async def fake_apply(self: object, version: str | None) -> dict[str, Any]:
        return {"version": "9.9.9"}

    monkeypatch.setattr(u_mod.Updater, "apply", fake_apply)
    seen: list[dict[str, Any]] = []
    _kill_at_restart(monkeypatch, "2000000000aa", seen)
    jobs = {"2000000000aa": _job("2000000000aa", "queued", None, channel="stable", version=None)}
    u_mod._persist_job(jobs["2000000000aa"])

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(u_mod._run_apply_job(jobs, "2000000000aa", "stable", None))
    app.state.update_jobs = {}  # the new process

    # At the moment the restart killed us, "applied" was already on disk.
    assert [(row["state"], row.get("error_code")) for row in seen] == [("applied", None)]
    final = client.get("/api/updates/status/2000000000aa").json()
    assert final["state"] == "applied"
    assert "error_code" not in final and final["error"] is None


def test_commit_job_killed_by_its_own_restart_reloads_applied(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = app_client
    convergence = {"converged": True, "post_activation_migrations": {"ok": True}}

    async def fake_commit(self: object, version: str) -> dict[str, Any]:
        return {"version": "9.9.9", "installed_at": time.time(), "convergence": convergence}

    monkeypatch.setattr(u_mod.Updater, "commit", fake_commit)
    seen: list[dict[str, Any]] = []
    _kill_at_restart(monkeypatch, "3000000000aa", seen)
    jobs = {
        "3000000000aa": _job("3000000000aa", "queued", "commit", channel="stable", version="9.9.9")
    }
    u_mod._persist_job(jobs["3000000000aa"])

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(u_mod._run_commit_job(jobs, "3000000000aa", "stable", "9.9.9"))
    app.state.update_jobs = {}

    assert [(row["state"], row.get("convergence")) for row in seen] == [("applied", convergence)]
    final = client.get("/api/updates/status/3000000000aa").json()
    assert final["state"] == "applied" and "error_code" not in final
    assert final["convergence"] == convergence


def test_prepare_job_reloads_prepared(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = app_client

    async def fake_prepare(self: object, version: str | None) -> dict[str, Any]:
        return {"version": "9.9.9", "notes": "what changed"}

    monkeypatch.setattr(u_mod.Updater, "prepare", fake_prepare)
    jobs = {
        "4000000000aa": _job("4000000000aa", "queued", "prepare", channel="stable", version=None)
    }
    u_mod._persist_job(jobs["4000000000aa"])

    asyncio.run(u_mod._run_prepare_job(jobs, "4000000000aa", "stable", None))
    app.state.update_jobs = {}

    final = client.get("/api/updates/status/4000000000aa").json()
    assert (final["state"], final["resolved_version"], final["notes"]) == (
        "prepared",
        "9.9.9",
        "what changed",
    )
    assert "error_code" not in final


def test_component_job_reloads_applied(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Hermes converge restarts the agent and gateway units, never hal0-api."""
    app, client = app_client
    import hal0.components.registry as creg
    import hal0.components.runner as crun

    monkeypatch.setattr(creg, "component_by_id", lambda cid: object())
    monkeypatch.setattr(crun, "converge_component", lambda comp, **kw: {"status": "converged"})
    jobs = {"5000000000aa": _job("5000000000aa", "queued", "converge:openwebui")}
    u_mod._persist_job(jobs["5000000000aa"])

    asyncio.run(u_mod._run_component_job(jobs, "5000000000aa", "openwebui", {}))
    app.state.update_jobs = {}

    final = client.get("/api/updates/status/5000000000aa").json()
    assert final["state"] == "applied" and "error_code" not in final


def test_only_the_apply_and_commit_jobs_restart_hal0_api() -> None:
    """The planned restart is ``_try_restart_hal0_api``, called after ``applied`` is persisted.

    A source-level pin of what the reload rule relies on: the one place the
    updater restarts hal0-api is that helper, only the apply and commit jobs
    call it, and each persists ``applied`` first.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(u_mod))
    callers = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name != "_try_restart_hal0_api"
        and any(
            isinstance(inner, ast.Name) and inner.id == "_try_restart_hal0_api"
            for inner in ast.walk(node)
        )
    }
    assert callers == {"_run_apply_job", "_run_commit_job"}
    for runner in (u_mod._run_apply_job, u_mod._run_commit_job):
        body = inspect.getsource(runner)
        applied = body.index('job["state"] = "applied"')
        persist = body.index("_persist_job(job)", applied)
        assert persist < body.index("asyncio.to_thread(_try_restart_hal0_api)")


# ── the id in a job file is untrusted ────────────────────────────────────────


def test_a_job_file_naming_another_id_is_not_served_or_written_back(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """``update-jobs/x.json`` is read back and written under the ``id`` inside it."""
    app, client = app_client
    jobs_dir = u_mod._jobs_dir()
    jobs_dir.mkdir(parents=True, exist_ok=True)
    state_dir = jobs_dir.parent / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    crafted = {"id": "../state/escaped", "state": "running", "phase": "converge:hermes"}
    (jobs_dir / "1000000000aa.json").write_text(json.dumps(crafted))

    resp = client.get("/api/updates/status/1000000000aa")

    assert resp.status_code == 404
    assert not (state_dir / "escaped.json").exists()
    assert not list(jobs_dir.parent.parent.rglob("escaped*"))
    assert json.loads((jobs_dir / "1000000000aa.json").read_text()) == crafted
    assert "1000000000aa" not in app.state.update_jobs


@pytest.mark.parametrize(
    "bad_id",
    ["j1", "AAAAAAAAAAAA", "1000000000a", "1000000000aaa", "1000000000a-", "..", "x.json"],
)
def test_a_status_request_for_an_id_no_route_minted_is_404(
    app_client: tuple[FastAPI, TestClient], bad_id: str
) -> None:
    _app, client = app_client
    jobs_dir = u_mod._jobs_dir()
    jobs_dir.mkdir(parents=True, exist_ok=True)
    # Even a file that is named exactly that, with a matching id inside, is not read.
    (jobs_dir / f"{bad_id}.json").write_text(json.dumps({"id": bad_id, "state": "applied"}))

    assert client.get(f"/api/updates/status/{bad_id}").status_code == 404


def test_a_job_with_a_path_in_its_id_is_not_persisted(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    jobs_dir = u_mod._jobs_dir()
    escaped = jobs_dir.parent / "escaped.json"

    u_mod._persist_job({"id": "../escaped", "state": "queued"})
    u_mod._persist_job({"id": "../../escaped", "state": "queued"})

    assert not escaped.exists()
    assert not list(jobs_dir.parent.parent.rglob("escaped*"))
    with pytest.raises(ValueError):
        u_mod._job_file("../escaped")


def test_the_ids_the_routes_mint_are_valid_job_ids() -> None:
    import uuid

    for _ in range(50):
        assert u_mod._job_file(uuid.uuid4().hex[:12]).suffix == ".json"
