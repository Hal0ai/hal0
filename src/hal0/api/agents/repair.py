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
``<state>/agents/hermes/provision-logs/job-<id>.log`` (mode 0640, directory 0750;
the transcripts are not pruned) and every ``[run ] <step>`` line is appended to
``job["steps"]``, so the status route serves a coarse checklist while
``provision.json`` still holds the previous report.

The child is the running tree's own ``hal0`` binary
(:func:`hal0.system.seam.running_hal0_bin`), with ``HERMES_HOME`` unset and
``HOME`` set to the hal0 service home, the way ``_run_as_hal0`` builds its
``env`` prefix. It runs as the daemon's own user; there is no privilege change.
``HAL0_HERMES_TERMINAL`` is never set, so the existing posture is kept.

Bounded
-------
The run is limited to :data:`_BOOTSTRAP_TIMEOUT_S` (reading the output and waiting
for the exit together). A stalled ``pip`` or ``git``, or a grandchild that keeps
the stdout pipe open after the child exits, would otherwise leave the job
``running`` for good, and a ``running`` job refuses every converge and repair
until hal0-api restarts. On timeout the child's whole process group is
terminated and the job ends ``failed``. The child starts in its own session so
its ``uv``/``pip``/``git`` descendants can be signalled with it, on a timeout or
a cancellation.

Known limit
-----------
The child lives in hal0-api's cgroup, so a restart of hal0-api during a repair
kills the build. The release builder is atomic (the ``current`` link swaps only
at the very end), so the live release is untouched. The job is left ``queued`` or
``running`` on disk. :func:`reconcile_stale_repair_job` then records it ``failed``
(``error_code: api_restarted``) when the process that registered it is gone: the
status route calls it for every job it serves, whether found in memory or
re-seeded from disk, and the repair route calls it before deciding that a job is
in flight, so a killed repair never blocks the next one.

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
import signal
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import structlog
from fastapi import APIRouter, Request

from hal0.api.agents.restart import _resolve_actor, require_installed
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

#: How long a repair may run, reading and waiting together. A cold release build
#: that downloads a uv-managed Python takes 15-20 minutes on a slow box; this is
#: more than twice that.
_BOOTSTRAP_TIMEOUT_S: Final[float] = 45 * 60

#: After SIGTERM to the child's process group, how long it has to exit before the
#: group is sent SIGKILL.
_TERM_GRACE_S: Final[float] = 5.0

#: Transcript modes: the log may carry paths and pip output, not for other users.
_LOG_FILE_MODE: Final[int] = 0o640
_LOG_DIR_MODE: Final[int] = 0o750

_JOB_STATES_IN_FLIGHT: Final[tuple[str, str]] = ("queued", "running")

#: What a repair job reads after hal0-api restarted under it.
_API_RESTARTED_CODE: Final[str] = "api_restarted"
_API_RESTARTED_ERROR: Final[str] = (
    "hal0-api restarted during the repair; the live release is unchanged"
)

#: Ids of the jobs whose runner is executing in this process, so a job this
#: process is still running is never mistaken for one an earlier process left.
_live_runners: set[str] = set()

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
    require_installed(agent_id)


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
    written; the job is left ``running`` on disk and reads ``failed`` with
    ``error_code: api_restarted`` the next time anything looks at it (the status
    route, or this route's own busy check:
    :func:`reconcile_stale_repair_job`).
    """
    _require_installed(agent_id)

    # Check, probe, register and spawn without an ``await`` in between: two
    # concurrent POSTs must not both find the registry idle (the converge route's
    # reasoning). The lock probe is a mkdir, an open and a non-blocking flock on a
    # local file, short enough to run on the loop.
    jobs = _update_jobs(request)
    for other in jobs.values():
        # A repair an earlier hal0-api left ``running`` is not in flight.
        reconcile_stale_repair_job(other)
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


def _pid_alive(pid: object) -> bool:
    """Whether a process with this pid exists (``os.kill(pid, 0)``); not a valid pid is not alive."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # EPERM: it exists, under another user
    return True


def reconcile_stale_repair_job(
    job: dict[str, Any],
    *,
    current_pid: int | None = None,
    pid_alive: Callable[[object], bool] = _pid_alive,
) -> dict[str, Any]:
    """Record a repair job that hal0-api restarted under as ``failed``; return ``job``.

    A repair job that is ``queued`` or ``running`` belongs to the hal0-api process
    that registered it (``api_pid``). It is stale when that is not this process
    (the runner and the child died with the old one: the child shares hal0-api's
    cgroup), or when it is ``running`` here with no runner of ours behind it and
    its child's ``pid`` is gone (the process id was reused by the new hal0-api).
    The job is then set ``failed`` with ``error_code: "api_restarted"``, persisted,
    and ``agent.repair.finished`` is emitted for it, since the runner that would
    have is gone. The release builder swaps ``current`` only at its very end, so
    the live release is unchanged either way.

    Anything else (another phase, a terminal state, a job this process is still
    running, a ``queued`` job of this process whose runner has not started) comes
    back untouched. ``job`` is changed in place, so the caller's registry entry
    reads ``failed`` afterwards.
    """
    if job.get("phase") != _PHASE or job.get("state") not in _JOB_STATES_IN_FLIGHT:
        return job
    me = os.getpid() if current_pid is None else current_pid
    stale = job.get("api_pid") != me
    if (
        not stale
        and job.get("state") == "running"
        and job.get("id") not in _live_runners
        and not pid_alive(job.get("pid"))
    ):
        stale = True
    if not stale:
        return job

    job["state"] = "failed"
    job["error"] = _API_RESTARTED_ERROR
    job["error_code"] = _API_RESTARTED_CODE
    _touch(job)
    log.warning("agent.repair.api_restarted", job_id=job.get("id"), api_pid=job.get("api_pid"))
    audit_log.warning(
        "agent.repair.finished",
        agent_id=str(job.get("agent", "hermes")),
        job_id=job.get("id"),
        actor=None,
        exit_code=None,
        state="failed",
        error_code=_API_RESTARTED_CODE,
    )
    return job


def _open_log(log_path: Path, job: dict[str, Any]) -> Any:
    """Open the transcript for append; ``None`` (and ``job["log_path"] = None``) when it cannot be."""
    try:
        log_path.parent.mkdir(mode=_LOG_DIR_MODE, parents=True, exist_ok=True)
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, _LOG_FILE_MODE)
        return os.fdopen(fd, "a", encoding="utf-8")
    except OSError as exc:
        log.warning("agent.repair.log_unavailable", path=str(log_path), error=str(exc))
        job["log_path"] = None
        return None


def _signal_group(pgid: int, sig: signal.Signals) -> bool:
    """Send ``sig`` to process group ``pgid``; ``False`` when there is no such group.

    The child is spawned with ``start_new_session=True``, so its pid is the id of a
    group that holds it and everything it started. The group outlives a reaped
    leader while any member lives, and its id cannot be reused until then.
    """
    try:
        os.killpg(pgid, sig)
    except OSError:
        return False
    return True


async def _terminate(proc: Any) -> None:
    """Stop the child and its descendants, and reap the child. Never raises.

    SIGTERM to the process group (the child alone if there is no such group), up to
    :data:`_TERM_GRACE_S` for the child to exit, then SIGKILL to the group for a
    child that ignored the first signal and for descendants that outlived it.
    Safe to call on a child that already exited: its group may still have members.
    """
    pid = getattr(proc, "pid", None)
    try:
        if pid is None or not _signal_group(pid, signal.SIGTERM):
            with contextlib.suppress(Exception):
                if proc.returncode is None:
                    proc.kill()
        if proc.returncode is None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), _TERM_GRACE_S)
    finally:
        if pid is not None:
            _signal_group(pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            if proc.returncode is None:
                proc.kill()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), _TERM_GRACE_S)


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
    with ``exit_code`` set. It also ends ``failed`` when the child cannot be
    started, when reading its output breaks, and when it has not finished within
    :data:`_BOOTSTRAP_TIMEOUT_S` (``error = "bootstrap timed out after <n>s"``);
    in the last two the child's process group is terminated and ``exit_code`` is
    the reaped child's return code. Whatever the outcome the job is persisted
    where it changed and ``agent.repair.finished`` is emitted with the
    ``exit_code`` (``None`` when there was none).

    If the task is cancelled (hal0-api shutting down) the child's process group is
    terminated and ``agent.repair.finished`` is emitted with ``state="cancelled"``.
    A job that was already ``running`` is left so, on disk, for
    :func:`reconcile_stale_repair_job` to record as ``api_restarted`` once the
    process is gone; one cancelled before it started is persisted ``failed``
    (``error = "repair cancelled"``).
    """
    job = jobs[job_id]
    agent_id = str(job.get("agent", "hermes"))
    exit_code: int | None = None
    cancelled = False
    _live_runners.add(job_id)
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
                start_new_session=True,
            )
        except OSError as exc:
            job["state"] = "failed"
            job["error"] = f"could not start bootstrap: {exc}"
            return

        job["state"] = "running"
        job["pid"] = proc.pid
        _touch(job)

        deadline = asyncio.timeout(_BOOTSTRAP_TIMEOUT_S)
        try:
            async with deadline:
                while True:
                    raw = await proc.stdout.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if log_file is not None:
                        log_file.write(line + "\n")
                        log_file.flush()
                    if line.startswith(_STEP_PREFIX):
                        step = line[len(_STEP_PREFIX) :].strip()
                        if step:
                            job["steps"].append(step)
                            _touch(job)
                exit_code = await proc.wait()
        except TimeoutError:
            if not deadline.expired():
                raise
            await _terminate(proc)
            exit_code = proc.returncode
            job["state"] = "failed"
            job["error"] = f"bootstrap timed out after {int(_BOOTSTRAP_TIMEOUT_S)}s"
            if exit_code is not None:
                job["exit_code"] = exit_code
            return

        job["exit_code"] = exit_code
        if exit_code == 0:
            job["state"] = "applied"
        else:
            job["state"] = "failed"
            job["error"] = f"bootstrap exited {exit_code}"
    except asyncio.CancelledError:
        cancelled = True
        if proc is not None:
            await _terminate(proc)
        if job["state"] == "queued":
            job["state"] = "failed"
            job["error"] = "repair cancelled"
        raise
    except Exception as exc:
        log.warning("agent.repair.runner_failed", job_id=job_id, error=str(exc))
        if proc is not None:
            await _terminate(proc)
            exit_code = proc.returncode
            if exit_code is not None:
                job["exit_code"] = exit_code
        job["state"] = "failed"
        job["error"] = f"repair runner failed: {exc}"
    finally:
        _live_runners.discard(job_id)
        if log_file is not None:
            log_file.close()
        # A job cancelled while running stays ``running`` for
        # ``reconcile_stale_repair_job``; every other ending is written down.
        if job["state"] in ("applied", "failed"):
            _touch(job)
        outcome = "cancelled" if cancelled else job["state"]
        emit = audit_log.info if outcome == "applied" else audit_log.warning
        emit(
            "agent.repair.finished",
            agent_id=agent_id,
            job_id=job_id,
            actor=actor,
            exit_code=exit_code,
            state=outcome,
        )


__all__ = ["reconcile_stale_repair_job", "repair_agent", "router", "run_repair_job"]
