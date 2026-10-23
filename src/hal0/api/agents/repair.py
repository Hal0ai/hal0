"""``POST /api/agents/{agent_id}/repair``: re-run the Hermes provisioner as a tracked job.

The Hermes card's Repair button. The route registers a job in the updater's
registry (``app.state.update_jobs``, mirrored to ``update-jobs/<id>.json``) with
``phase: "repair:hermes"`` and returns the queued snapshot with ``202``; a
background task then runs ``hal0 agent bootstrap hermes --repair --verbose`` and
moves the job through ``running`` to ``applied`` or ``failed``. Progress is read
through the existing ``GET /api/updates/status/{id}``, so the CLI, the Services
page and the card all see the same job, and a repair and a converge refuse each
other (the registry is the shared busy signal).

Why a subprocess
----------------
The provisioner takes minutes, prints to its console and mutates ``os.environ``.
Run in-process it would hold all of that inside hal0-api. As a child it runs the
same code path as ``hal0 agent reprovision hermes --repair``, and the API only
reads its stdout line by line: every line goes to
``<state>/agents/hermes/provision-logs/job-<id>.log`` and every ``[run ] <step>``
line is appended to ``job["steps"]``, so the status route serves a coarse
checklist while ``provision.json`` still holds the previous report.

The child is the running tree's own ``hal0`` binary
(:func:`hal0.system.seam.running_hal0_bin`), with ``HERMES_HOME`` unset and
``HOME`` set to the hal0 service home, the way ``_run_as_hal0`` builds its
``env`` prefix. It runs as the daemon's own user; there is no privilege change.
``HAL0_HERMES_TERMINAL`` is never set, so the existing posture is kept.

Known limit
-----------
The child lives in hal0-api's cgroup, so a restart of hal0-api during a repair
kills the build. The release builder is atomic (the ``current`` link swaps only
at the very end), so the live release is untouched. The job is left ``running``
on disk; the status route is to record it ``failed`` (``error_code:
api_restarted``) once it sees the recorded ``api_pid`` is gone (the marker in
``update_status``). Until that lands, such a job stays ``running``.

Layering
--------
``hal0.api`` never imports ``hal0.cli``. Everything this module needs from the
CLI side lives in :mod:`hal0.system.seam`. No Hermes module is imported at module
level (golden path GP15, ``tests/golden_paths/test_gp15_no_hermes.py``): the
release layout and the venv default are imported by the functions that use them.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import structlog
from fastapi import APIRouter, Request

from hal0.api.agents.restart import _resolve_actor
from hal0.api.routes import agents as agents_routes
from hal0.api.routes.updater import _persist_job, _spawn_update_task, _update_jobs
from hal0.config import paths
from hal0.errors import Conflict, NotFound
from hal0.system.seam import hal0_service_home, running_hal0_bin

if TYPE_CHECKING:
    from hal0.agents.hermes_release import Layout

log = structlog.get_logger(__name__)
audit_log = structlog.get_logger("hal0.agents.audit")
router = APIRouter()

#: Agents with a repair job. Separate from the restart registry: a future bundled
#: agent gains repair only when it has a provisioner to re-run.
_REPAIR_AGENTS: Final[frozenset[str]] = frozenset({"hermes"})

#: The registry phase. ``converge:hermes`` is the update arm's; the overview route
#: reads both to show the card's ``working`` phase.
_PHASE: Final[str] = "repair:hermes"

#: What the child is asked to do, after the binary.
_BOOTSTRAP_ARGS: Final[tuple[str, ...]] = ("agent", "bootstrap", "hermes", "--repair", "--verbose")

#: A ``--verbose`` provisioner prints this before each install step.
_STEP_PREFIX: Final[str] = "[run ] "

#: Longest child output line the reader accepts (asyncio's default is 64 KiB; a
#: pip or git transcript line can run past it).
_STREAM_LIMIT: Final[int] = 1 << 20

_JOB_STATES_IN_FLIGHT: Final[tuple[str, str]] = ("queued", "running")

# Test seams, module attributes on purpose: the tests replace them and nothing
# else here touches a subprocess, the real release tree or /var/lib.
_spawn: Callable[..., Any] = asyncio.create_subprocess_exec


def _log_dir() -> Path:
    """Where the per-job transcripts live (the provisioner's own log directory)."""
    return paths.var_lib() / "state" / "agents" / "hermes" / "provision-logs"


def _layout() -> Layout:
    """The release layout the repair rebuilds, for the builder-lock probe."""
    from hal0.agents.hermes_provision import HERMES_VENV_DEFAULT
    from hal0.agents.hermes_release import Layout

    return Layout.for_venv(HERMES_VENV_DEFAULT)


def _child_env() -> dict[str, str]:
    """The environment of the provisioner child.

    A copy of this process' environment without ``HERMES_HOME`` and with ``HOME``
    at the hal0 service home. ``PYTHONUNBUFFERED`` keeps the child's ``[run ]``
    lines from sitting in a pipe buffer until the run ends, which would leave the
    step list empty for the whole install.
    """
    env = {key: value for key, value in os.environ.items() if key != "HERMES_HOME"}
    env["HOME"] = hal0_service_home()
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _require_installed(agent_id: str) -> None:
    """404 unless ``agent_id`` is a repairable agent that is installed."""
    if agent_id not in _REPAIR_AGENTS:
        raise NotFound(
            f"unknown agent {agent_id!r}",
            code="agent.unknown",
            details={"agent_id": agent_id},
        )
    if not any(rec.name == agent_id for rec in agents_routes._manager().list()):
        raise NotFound(
            f"{agent_id} is not installed",
            code="agent.not_installed",
            details={"agent_id": agent_id},
        )


@router.post("/{agent_id}/repair", status_code=202)
async def repair_agent(agent_id: str, request: Request) -> dict[str, Any]:
    """Queue a repair of ``agent_id`` (Hermes) and return the queued job snapshot.

    Poll ``GET /api/updates/status/{id}`` for ``queued`` -> ``running`` ->
    ``applied`` | ``failed``; ``steps`` fills as the provisioner reports them and
    ``log_path`` names the transcript.

    * 404 ``agent.unknown`` for any agent but Hermes; 404 ``agent.not_installed``
      when Hermes is not installed.
    * 409 ``agent.busy`` when an update or converge job is queued or running
      (``details.job_id``: the converge route's own rule, so the two see each
      other) or when the Hermes builder lock is held by another process
      (``details.holder``: the lock's holder record, advisory, since it may name
      a holder that has since died; the decision is the ``flock``, never the
      record).
    * 409 ``agent.builder_lock_unreadable`` when the lock file cannot be created
      or opened, so no answer about the lock can be given; ``details.error``
      carries the OS error.

    Known limit: the build runs as a child of hal0-api and shares its cgroup, so
    restarting hal0-api during a repair kills it. The release builder is atomic
    (``current`` swaps only at the end), so the live release is never half
    written; the job is left ``running`` on disk. The status route is to record
    it ``failed`` (``api_restarted``) once it finds the job's ``api_pid`` gone
    (marker in ``update_status``); until that lands it stays ``running``.
    """
    _require_installed(agent_id)

    # Check, probe, register and spawn without an ``await`` in between: two
    # concurrent POSTs must not both find the registry idle (the converge route's
    # reasoning). The lock probe is a mkdir, an open and a non-blocking flock on a
    # local file, short enough to run on the loop.
    jobs = _update_jobs(request)
    for other in jobs.values():
        if other.get("state") in _JOB_STATES_IN_FLIGHT:
            raise Conflict(
                "an update, converge or repair job is already in flight",
                code="agent.busy",
                details={"job_id": other.get("id")},
            )

    from hal0.agents.hermes_release import builder_lock_free

    try:
        free, holder = builder_lock_free(_layout())
    except OSError as exc:
        raise Conflict(
            f"the Hermes builder lock cannot be opened: {exc}",
            code="agent.builder_lock_unreadable",
            details={"error": str(exc)},
        ) from exc
    if not free:
        raise Conflict(
            "a Hermes build is already running",
            code="agent.busy",
            details={"holder": holder},
        )

    actor = _resolve_actor(request)
    job_id = uuid.uuid4().hex[:12]
    log_path = _log_dir() / f"job-{job_id}.log"
    now = time.time()
    jobs[job_id] = {
        "id": job_id,
        "state": "queued",
        "phase": _PHASE,
        "kind": "repair",
        "agent": agent_id,
        "created_at": now,
        "updated_at": now,
        "error": None,
        "steps": [],
        "log_path": str(log_path),
        "api_pid": os.getpid(),
    }
    _persist_job(jobs[job_id])
    audit_log.info("agent.repair.invoked", agent_id=agent_id, job_id=job_id, actor=actor)
    _spawn_update_task(
        request,
        run_repair_job(
            jobs,
            job_id,
            argv=[running_hal0_bin(), *_BOOTSTRAP_ARGS],
            env=_child_env(),
            log_path=log_path,
            spawn=_spawn,
            actor=actor,
        ),
    )

    # The card's next overview poll must see the job, not a cached build from
    # before it (the cache key already carries the running job; this drops a
    # build that was in flight when the job was registered).
    from hal0.api.agents.overview import invalidate

    invalidate(agent_id)
    return dict(jobs[job_id])


def _touch(job: dict[str, Any]) -> None:
    job["updated_at"] = time.time()
    _persist_job(job)


def _open_log(log_path: Path, job: dict[str, Any]) -> Any:
    """Open the transcript for append; ``None`` (and ``job["log_path"] = None``) when it cannot be."""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return log_path.open("a", encoding="utf-8")
    except OSError as exc:
        log.warning("agent.repair.log_unavailable", path=str(log_path), error=str(exc))
        job["log_path"] = None
        return None


async def _terminate(proc: Any) -> None:
    """Kill the child if it is still running and reap it. Never raises."""
    if getattr(proc, "returncode", None) is not None:
        return
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.kill()
    with contextlib.suppress(Exception):
        await proc.wait()


async def run_repair_job(
    jobs: dict[str, dict[str, Any]],
    job_id: str,
    *,
    argv: list[str],
    env: dict[str, str],
    log_path: Path,
    spawn: Callable[..., Any] = asyncio.create_subprocess_exec,
    actor: str | None = None,
) -> None:
    """Run ``argv`` for job ``job_id`` and record the outcome in ``jobs``.

    Sets the job ``running`` with the child's ``pid``, copies the child's output
    line by line to ``log_path`` and appends the step name of every ``[run ] ``
    line to ``job["steps"]`` (persisting each time). The job ends ``applied`` on
    exit code 0 and ``failed`` otherwise (``error = "bootstrap exited <rc>"``),
    with ``exit_code`` set; a child that cannot be started, or a failure while
    reading its output, also ends ``failed``. Whatever the outcome the job is
    persisted and ``agent.repair.finished`` is emitted with the ``exit_code``
    (``None`` when there was none).

    If the task is cancelled (hal0-api shutting down) the child is killed and the
    job is left as it was: ``running`` on disk, for the status route to reconcile.
    """
    job = jobs[job_id]
    agent_id = str(job.get("agent", "hermes"))
    exit_code: int | None = None
    log_file = _open_log(log_path, job)
    proc: Any = None
    try:
        try:
            proc = await spawn(
                *argv,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                limit=_STREAM_LIMIT,
            )
        except OSError as exc:
            job["state"] = "failed"
            job["error"] = f"could not start bootstrap: {exc}"
            return

        job["state"] = "running"
        job["pid"] = proc.pid
        _touch(job)

        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if log_file is not None:
                log_file.write(line + "\n")
                log_file.flush()
            if line.startswith(_STEP_PREFIX):
                job["steps"].append(line[len(_STEP_PREFIX) :].strip())
                _touch(job)

        exit_code = await proc.wait()
        job["exit_code"] = exit_code
        if exit_code == 0:
            job["state"] = "applied"
        else:
            job["state"] = "failed"
            job["error"] = f"bootstrap exited {exit_code}"
    except asyncio.CancelledError:
        if proc is not None:
            await _terminate(proc)
        raise
    except Exception as exc:
        log.warning("agent.repair.runner_failed", job_id=job_id, error=str(exc))
        if proc is not None:
            await _terminate(proc)
        job["state"] = "failed"
        job["error"] = f"repair runner failed: {exc}"
    finally:
        if log_file is not None:
            log_file.close()
        # A cancelled run is neither applied nor failed: leave it for the status
        # route to reconcile.
        if job["state"] in ("applied", "failed"):
            _touch(job)
            emit = audit_log.info if job["state"] == "applied" else audit_log.warning
            emit(
                "agent.repair.finished",
                agent_id=agent_id,
                job_id=job_id,
                actor=actor,
                exit_code=exit_code,
                state=job["state"],
            )


__all__ = ["repair_agent", "router", "run_repair_job"]
