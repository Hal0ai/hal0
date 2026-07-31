"""Runner-image pull-job orchestration — the request/service seam.

Mirrors :mod:`hal0.registry.pull_jobs` (the model-pull orchestration layer):
this module holds everything a route handler needs to start/track/cancel a
runner-image download without touching job internals directly — in-flight
dedup, the detached-task scheduler (issue #1225: never a Starlette
``BackgroundTask``, so a live pull doesn't block graceful shutdown), the
disk-fallback status/stream read path, and cancel.

Interface contract:

    enqueue(request, *, image_id) -> dict
        POST /{id}/pull — starts a background pull, in-flight dedup.
    status(image_id, jobs) -> dict
        GET /{id}/pull/status — disk-fallback included.
    cancel(image_id, jobs) -> dict
        POST /{id}/pull/cancel — idempotent.
    list_all(jobs) -> list[dict]
        GET /pulls — in-memory + persisted-terminal, deduped.
    build_stream_response(image_id, request) -> StreamingResponse
        GET /{id}/pull/stream — SSE, disk-fallback + shutdown short-circuit.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Coroutine
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from hal0.registry.runner_pull import (
    RunnerImageNotCatalogued,
    RunnerPullJob,
    RunnerPullJobNotFound,
    list_persisted_jobs,
    make_job,
    persist_pull_job,
    pull_job_file,
    run_runner_pull,
)


def schedule_pull_task(
    app_state: Any,
    image_id: str,
    coro: Coroutine[Any, Any, None],
) -> asyncio.Task[None]:
    """Launch a pull body as a detached ``asyncio.Task``, tracked for shutdown.

    Same rationale as ``hal0.registry.pull_jobs.schedule_pull_task``
    (issue #1225): a Starlette ``BackgroundTasks`` entry keeps the HTTP
    connection open for the whole download, blocking graceful shutdown.
    """
    task = asyncio.create_task(coro)
    tasks: dict[str, asyncio.Task[None]] = app_state.runner_image_pull_tasks
    tasks[image_id] = task

    def _untrack(t: asyncio.Task[None], _image_id: str = image_id) -> None:
        if tasks.get(_image_id) is t:
            tasks.pop(_image_id, None)

    task.add_done_callback(_untrack)
    return task


async def enqueue(request: Request, *, image_id: str) -> dict[str, object]:
    """Start a background pull for ``image_id`` and return a job handle.

    Idempotent-ish: an in-flight (``queued``/``running``) job is returned
    rather than duplicated.
    """
    jobs: dict[str, RunnerPullJob] = request.app.state.runner_image_pull_jobs
    existing = jobs.get(image_id)
    if existing is not None and existing.state in ("queued", "running"):
        return {"id": existing.job_id, "image_id": image_id, "state": existing.state, "resumed": True}

    store = request.app.state.runner_image_registry
    entry = store.get(image_id)
    if entry is None:
        raise RunnerImageNotCatalogued(
            f"runner image {image_id!r} not in catalogue — sync first",
            details={"image_id": image_id},
        )
    image_ref = f"{entry.image}:{entry.tag}"

    job = make_job(image_id, image_ref)
    jobs[image_id] = job
    persist_pull_job(job)

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None:
        await event_bus.emit(
            "runner_image.pull.queued",
            "info",
            f"runner-image-pull:{image_id}",
            f"{image_id}: queued ({image_ref})",
            data={"image_id": image_id, "image_ref": image_ref},
        )

    provider = request.app.state.container_provider

    async def _run() -> None:
        try:
            await run_runner_pull(job, store=store, provider=provider)
        finally:
            persist_pull_job(job)
            if event_bus is not None:
                await _emit_terminal(event_bus, job)

    schedule_pull_task(request.app.state, image_id, _run())
    return {"id": job.job_id, "image_id": image_id, "state": job.state, "image_ref": image_ref}


async def _emit_terminal(event_bus: Any, job: RunnerPullJob) -> None:
    if job.state == "completed":
        await event_bus.emit(
            "runner_image.pull.completed",
            "info",
            f"runner-image-pull:{job.image_id}",
            f"{job.image_id}: download complete",
            data={"image_id": job.image_id, "local_path": job.local_path},
        )
    elif job.state == "failed":
        await event_bus.emit(
            "runner_image.pull.failed",
            "error",
            f"runner-image-pull:{job.image_id}",
            f"{job.image_id}: {job.error or 'pull failed'}",
            data={"image_id": job.image_id, "error": job.error, "error_code": job.error_code},
        )
    elif job.state == "cancelled":
        await event_bus.emit(
            "runner_image.pull.cancelled",
            "warn",
            f"runner-image-pull:{job.image_id}",
            f"{job.image_id}: pull cancelled",
            data={"image_id": job.image_id},
        )


def load_persisted(image_id: str) -> dict[str, Any] | None:
    """Read a persisted job snapshot from disk (reconciled), or None."""
    path = pull_job_file(image_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        loaded = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(loaded, dict):
        return None
    return reconcile_persisted(loaded)


def reconcile_persisted(persisted: dict[str, Any]) -> dict[str, Any]:
    """Repair a non-terminal snapshot whose worker is gone (after a restart)."""
    if persisted.get("state") in ("queued", "running"):
        persisted = dict(persisted)
        persisted["state"] = "failed"
        persisted.setdefault("error", "pull interrupted by hal0-api restart; re-run the pull")
        persisted.setdefault("error_code", "runner_image.pull_interrupted")
    return persisted


def status(image_id: str, jobs: dict[str, RunnerPullJob]) -> dict[str, object]:
    job = jobs.get(image_id)
    if job is None:
        persisted = load_persisted(image_id)
        if persisted is not None:
            return persisted
        raise RunnerPullJobNotFound(
            f"no pull job for runner image {image_id!r}", details={"image_id": image_id}
        )
    return job.as_dict()


def cancel(image_id: str, jobs: dict[str, RunnerPullJob]) -> dict[str, object]:
    job = jobs.get(image_id)
    if job is None:
        raise RunnerPullJobNotFound(
            f"no pull job for runner image {image_id!r}", details={"image_id": image_id}
        )
    if job.state in ("queued", "running"):
        job.cancel_requested = True
    return job.as_dict()


def list_all(jobs: dict[str, RunnerPullJob]) -> list[dict[str, Any]]:
    """All pull jobs — active in-memory + persisted terminal, deduped (in-memory wins)."""
    persisted = list_persisted_jobs()
    by_id: dict[str, dict[str, Any]] = {}
    for p in persisted:
        if p.get("state") not in ("completed", "failed", "cancelled"):
            continue
        iid = p.get("image_id")
        if isinstance(iid, str) and iid:
            by_id[iid] = p
    for iid, job in jobs.items():
        by_id[iid] = job.as_dict()
    result = list(by_id.values())
    result.sort(
        key=lambda e: (
            0 if e.get("state") in ("queued", "running") else 1,
            -(e.get("started_at") or 0),
        )
    )
    return result


def build_stream_response(image_id: str, request: Request) -> StreamingResponse:
    """SSE stream of pull progress, with on-disk fallback + shutdown short-circuit."""
    jobs: dict[str, RunnerPullJob] = request.app.state.runner_image_pull_jobs
    job = jobs.get(image_id)
    if job is None:
        persisted = load_persisted(image_id)
        if persisted is not None:

            async def _gen_persisted() -> Any:
                yield f"data: {json.dumps(persisted)}\n\n"

            return StreamingResponse(
                _gen_persisted(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        raise RunnerPullJobNotFound(
            f"no pull job for runner image {image_id!r}", details={"image_id": image_id}
        )

    shutting_down: asyncio.Event | None = getattr(request.app.state, "shutting_down", None)

    async def _gen() -> Any:
        yield f"data: {json.dumps(job.as_dict())}\n\n"
        while job.state in ("queued", "running"):
            if shutting_down is not None and shutting_down.is_set():
                break
            event = job.progress_event
            try:
                await asyncio.wait_for(event.wait(), timeout=5.0)
            except TimeoutError:
                if shutting_down is not None and shutting_down.is_set():
                    break
                yield f"data: {json.dumps(job.as_dict())}\n\n"
                continue
            yield f"data: {json.dumps(job.as_dict())}\n\n"
        yield f"data: {json.dumps(job.as_dict())}\n\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def shutdown_pull_jobs(app_state: Any, timeout_s: float = 10.0) -> None:
    """Cancel every in-flight runner-image pull so shutdown doesn't wait on them."""
    tasks: dict[str, asyncio.Task[None]] = getattr(app_state, "runner_image_pull_tasks", {}) or {}
    live = [t for t in tasks.values() if not t.done()]
    if not live:
        return
    for t in live:
        t.cancel()
    with contextlib.suppress(Exception):
        await asyncio.wait(live, timeout=timeout_s)


__all__ = [
    "build_stream_response",
    "cancel",
    "enqueue",
    "list_all",
    "load_persisted",
    "reconcile_persisted",
    "schedule_pull_task",
    "shutdown_pull_jobs",
    "status",
]
