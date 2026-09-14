"""Runner-image pull-job orchestration — the request/service seam.

Mirrors :mod:`hal0.registry.pull_jobs` (the model-pull orchestration layer):
this module holds everything a route handler needs to start/track/cancel a
runner-image download without touching job internals directly — in-flight
dedup, the detached-task scheduler (issue #1225: never a Starlette
``BackgroundTask``, so a live pull doesn't block graceful shutdown), the
disk-fallback status/stream read path, and cancel.

Interface contract:

    enqueue(request, *, image_id, tag=None) -> dict
        POST /{id}/pull — starts a background pull, in-flight dedup.
        ``tag`` restricts the pulled ref to a catalogued tag (headline or
        ``available_tags`` member); None keeps the headline behaviour.
    status(image_id, jobs, tag=None) -> dict
        GET /{id}/pull/status — disk-fallback included; ``tag`` filters to
        the job for that tag (in-memory slot, else that tag's persisted
        snapshot; 404 only when neither exists).
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
import os
from collections.abc import Coroutine
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from hal0.registry.runner_pull import (
    RunnerImageNotCatalogued,
    RunnerImageTagNotAvailable,
    RunnerPullConflict,
    RunnerPullJob,
    RunnerPullJobNotFound,
    list_persisted_jobs,
    make_job,
    persist_pull_job,
    persisted_job_files,
    pull_job_file,
    run_runner_pull,
    validate_pull_tag,
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


def _default_provider_factory() -> Any:
    from hal0.providers.container import container_provider

    return container_provider()


#: Monkeypatch seam for tests (mirrors ``routes.models._run_pull_with_events``):
#: point this at a fake ``pull_image_stream``-shaped object instead of the
#: real podman-backed singleton.
provider_factory: Any = _default_provider_factory

#: Retry attempts for a dashboard-triggered runner-image pull (backoff +
#: non-retryable classification: see ``runner_pull.is_retryable_pull_error``
#: / ``pull_backoff_delay``). ``run_runner_pull``'s own default (1, no
#: retry) is for direct/test callers; this is where the real multi-attempt
#: policy is opted into for the actual user-facing pull path. Override with
#: HAL0_RUNNER_PULL_MAX_ATTEMPTS for a slower/flakier network.
_PULL_MAX_ATTEMPTS = int(os.environ.get("HAL0_RUNNER_PULL_MAX_ATTEMPTS", "4"))


async def enqueue(request: Request, *, image_id: str, tag: str | None = None) -> dict[str, object]:
    """Start a background pull for ``image_id`` and return a job handle.

    ``tag`` selects which catalogued tag to pull; ``None`` means the row's
    headline ``tag`` (exactly the pre-per-tag behaviour). An explicit tag
    must be the headline or a member of the row's ``available_tags`` —
    the catalogue stays the honesty boundary, no free-text refs.

    Idempotent-ish: an in-flight (``queued``/``running``) job is returned
    rather than duplicated — but only when it's for the same tag (or no
    tag was requested). Job state is keyed one-per-image, so a request for
    a *different* tag while one is downloading is an honest 409, not a
    "resumed" handle to the wrong ref.
    """
    if tag is not None:
        validate_pull_tag(tag)  # typed 400 before any lookup or path build
    jobs: dict[str, RunnerPullJob] = request.app.state.runner_image_pull_jobs
    existing = jobs.get(image_id)
    if existing is not None and existing.state in ("queued", "running"):
        if tag is not None and existing.tag != tag:
            raise RunnerPullConflict(
                f"a pull for {existing.image_ref!r} is already in flight — "
                f"wait for it (or cancel) before pulling tag {tag!r}",
                details={"image_id": image_id, "tag": tag, "in_flight_tag": existing.tag},
            )
        return {
            "id": existing.job_id,
            "image_id": image_id,
            "tag": existing.tag,
            "state": existing.state,
            "resumed": True,
        }

    store = request.app.state.runner_image_registry
    entry = store.get(image_id)
    if entry is None:
        raise RunnerImageNotCatalogued(
            f"runner image {image_id!r} not in catalogue — sync first",
            details={"image_id": image_id},
        )
    if tag is not None and tag != entry.tag and tag not in entry.available_tags:
        raise RunnerImageTagNotAvailable(
            f"tag {tag!r} is not a catalogued tag of runner image {image_id!r} — sync first",
            details={"image_id": image_id, "tag": tag, "available_tags": entry.available_tags},
        )
    pull_tag = tag if tag is not None else entry.tag
    image_ref = f"{entry.image}:{pull_tag}"

    job = make_job(image_id, image_ref, tag=pull_tag)
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

    provider = provider_factory()

    async def _run() -> None:
        try:
            await run_runner_pull(
                job, store=store, provider=provider, max_attempts=_PULL_MAX_ATTEMPTS
            )
        finally:
            persist_pull_job(job)
            if event_bus is not None:
                await _emit_terminal(event_bus, job)

    schedule_pull_task(request.app.state, image_id, _run())
    return {
        "id": job.job_id,
        "image_id": image_id,
        "tag": pull_tag,
        "state": job.state,
        "image_ref": image_ref,
    }


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


def _read_snapshot(path: Any) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        loaded = json.loads(raw)
    except ValueError:
        return None
    return loaded if isinstance(loaded, dict) else None


def load_persisted(image_id: str, tag: str | None = None) -> dict[str, Any] | None:
    """Read a persisted job snapshot from disk (reconciled), or None.

    Snapshots are per-(image, tag) files. With ``tag``, only that tag's
    snapshot answers. Without, the most recent pull wins — highest
    ``started_at`` across every per-tag ``<id>@<tag>.json`` sibling.
    """
    if tag is not None:
        data = _read_snapshot(pull_job_file(image_id, tag=tag))
        if data is None or data.get("tag") != tag:
            return None
        return reconcile_persisted(data)
    best: dict[str, Any] | None = None
    for path in persisted_job_files(image_id):
        data = _read_snapshot(path)
        if data is None:
            continue
        if best is None or (data.get("started_at") or 0) > (best.get("started_at") or 0):
            best = data
    return reconcile_persisted(best) if best is not None else None


def reconcile_persisted(persisted: dict[str, Any]) -> dict[str, Any]:
    """Repair a non-terminal snapshot whose worker is gone (after a restart)."""
    if persisted.get("state") in ("queued", "running"):
        persisted = dict(persisted)
        persisted["state"] = "failed"
        persisted.setdefault("error", "pull interrupted by hal0-api restart; re-run the pull")
        persisted.setdefault("error_code", "runner_image.pull_interrupted")
    return persisted


def status(
    image_id: str, jobs: dict[str, RunnerPullJob], *, tag: str | None = None
) -> dict[str, object]:
    """Current job record for ``image_id`` (disk fallback included).

    With ``tag``, only a job for that exact tag answers — the in-memory
    slot may hold another tag's pull, in which case the read falls through
    to the requested tag's persisted snapshot (a newer pull must not
    orphan the previous tag's terminal result).
    """
    if tag is not None:
        validate_pull_tag(tag)  # typed 400 before any lookup or path build
    job = jobs.get(image_id)
    if job is not None and (tag is None or job.tag == tag):
        return job.as_dict()
    persisted = load_persisted(image_id, tag)
    if persisted is not None:
        return persisted
    if tag is not None:
        raise RunnerPullJobNotFound(
            f"no pull job for runner image {image_id!r} tag {tag!r}",
            details={"image_id": image_id, "tag": tag},
        )
    raise RunnerPullJobNotFound(
        f"no pull job for runner image {image_id!r}", details={"image_id": image_id}
    )


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
    """All pull jobs — active in-memory + persisted terminal, deduped per image.

    In-memory wins; among one image's persisted snapshots (per-tag files),
    the most recently started wins — one row per image, matching the
    single in-memory pull slot.
    """
    persisted = list_persisted_jobs()
    by_id: dict[str, dict[str, Any]] = {}
    for p in persisted:
        if p.get("state") not in ("completed", "failed", "cancelled"):
            continue
        iid = p.get("image_id")
        if not (isinstance(iid, str) and iid):
            continue
        prev = by_id.get(iid)
        if prev is None or (p.get("started_at") or 0) >= (prev.get("started_at") or 0):
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
