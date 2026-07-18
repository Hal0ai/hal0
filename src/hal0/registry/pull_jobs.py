"""HuggingFace/FLM pull-job orchestration (extracted from routes/models.py).

This is the request→service seam for the model-pull surface. The raw download
mechanics (``run_pull``/``run_flm_pull``/``make_job``/``persist_pull_job``/
``list_persisted_jobs``) live one layer down in :mod:`hal0.registry.pull`; this
module holds the *orchestration* that used to be inlined in the route handlers:
HF source/capability resolution, registry seeding from an add-by-HF body, the
detached-task scheduler, the progress-event fan-out wrapper, terminal footer
emission, and the persisted-snapshot disk-fallback + reconcile.

Interface contract (the only names route handlers / callers should use):

    load_persisted(model_id, registry=None) -> dict | None
        Read a persisted pull-job snapshot from disk (reconciled), or None.
    reconcile_persisted(persisted, registry=None) -> dict
        Repair a non-terminal snapshot whose worker is gone.
    resolve_pull_source(request, model_id) -> (hf_repo, hf_file)
        Registry row → curated catalogue; raises PullInvalidSource (422).
    resolve_pull_capability(request, model_id, body) -> (capability, subdir)
        body → registry → curated; (None, None) for ad-hoc → flat layout.
    resolve_pull_source_with_body(request, model_id, body)
        -> (hf_repo, hf_file, mmproj_file, from_body)
    seed_registry_from_body(request, model_id, hf_repo, hf_file, labels, chat_template=None)
        Idempotent upsert of a registry row from body-supplied HF coords.
    schedule_pull_task(app_state, model_id, coro) -> asyncio.Task
        Detached, shutdown-tracked task (NOT a Starlette BackgroundTask).
    run_pull_with_events(job, *, hf_repo, hf_file, registry, hf_token, event_bus, ...)
        run_pull wrapped with decile progress + terminal footer events.
    emit_terminal_pull_event(event_bus, job)
        Shared success/failure/cancellation footer emitter (HF + FLM).
    start_flm_pull(model_id, request, jobs) -> dict
        FLM/NPU background pull sharing the PullJob/SSE plumbing.
    speed_bps(job) / eta_s(job, speed)
        Rolling-rate helpers used by the progress payload.

Every function is pure of module state except for the ``request.app.state``
side effects (``model_pull_jobs``, ``model_pull_tasks``, ``events``) they were
already performing in the route layer — see the §3.1 Protocol seams in
``spec-p3-routers.final.md`` for the intended fake-able surface.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from fastapi import Request

from hal0.registry.curated import get_curated
from hal0.registry.pull import (
    PullInvalidSource,
    PullJob,
    make_job,
    persist_pull_job,
    pull_job_file,
    run_flm_pull,
    run_pull,
)


def load_persisted(model_id: str, registry: Any | None = None) -> dict[str, Any] | None:
    """Read a persisted pull-job snapshot from disk, or None if absent/unreadable.

    ``registry`` (when supplied) is forwarded to the reconcile step so a stale
    non-terminal snapshot can be cross-checked against ground truth — an
    install that actually landed is reported ``completed``, not ``failed``.
    """
    path = pull_job_file(model_id)
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
    return reconcile_persisted(loaded, registry)


def reconcile_persisted(persisted: dict[str, Any], registry: Any | None = None) -> dict[str, Any]:
    """Repair a persisted snapshot that was left in a non-terminal state.

    A snapshot only reaches disk-fallback when its job is absent from the
    in-memory dict — which after a restart means the worker that owned it is
    gone. A ``queued``/``running`` snapshot can therefore never make further
    progress.

    Before declaring it failed, cross-check ground truth (#MR-2): the terminal
    on-disk write is fail-soft, so a pull that actually completed can be left
    with a stale ``running`` snapshot. When ``registry`` knows the id and its
    model file exists on disk, the install landed — surface ``completed``
    (backfilling ``path``/``size_bytes`` from the registry) rather than a
    spurious ``failed``. Only a snapshot with no matching installed file falls
    through to the failed-rewrite. Terminal snapshots (completed/failed/
    cancelled) are returned unchanged.
    """
    if persisted.get("state") in ("queued", "running"):
        model_id = persisted.get("model_id")
        if registry is not None and model_id:
            try:
                if registry.has(model_id):
                    model = registry.get(model_id)
                    p = getattr(model, "path", None)
                    if p and Path(p).exists():
                        persisted = dict(persisted)
                        persisted["state"] = "completed"
                        persisted.setdefault("path", str(p))
                        size = getattr(model, "size_bytes", None)
                        if size:
                            persisted.setdefault("size_bytes", size)
                        sha = getattr(model, "sha256", None)
                        if sha:
                            persisted.setdefault("sha256", sha)
                        return persisted
            except Exception:
                # A registry hiccup must never raise inside a status poll —
                # degrade to the failed-rewrite below.
                pass
        persisted = dict(persisted)
        persisted["state"] = "failed"
        persisted.setdefault("error", "pull interrupted by hal0-api restart; re-run the pull")
        persisted.setdefault("error_code", "pull.interrupted")
    return persisted


def resolve_pull_source(request: Request, model_id: str) -> tuple[str, str]:
    """Resolve the (hf_repo, hf_file) tuple for a pull.

    Priority:
      1. The registry entry's ``hf_repo`` + ``hf_filename`` (set by
         ``pick-default`` when the curated catalogue is the source).
      2. The curated catalogue entry for ``model_id``.

    Raises ``PullInvalidSource`` (422) when neither path yields a repo
    + filename — typically because the caller hand-registered a model
    and never set its HF coordinates.
    """
    registry = request.app.state.model_registry
    try:
        existing = registry.get(model_id)
        repo = (existing.hf_repo or "").strip()
        filename = (existing.hf_filename or "").strip()
        if repo and filename:
            return repo, filename
    except Exception:
        pass
    curated = get_curated(model_id)
    if curated is not None:
        return curated.hf_repo, curated.hf_file
    raise PullInvalidSource(
        f"no hugging face source for model {model_id!r} — set hf_repo + hf_filename"
        " on the registry entry or pick a curated model id",
        details={"model_id": model_id},
    )


def resolve_pull_capability(
    request: Request,
    model_id: str,
    body: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    """Resolve ``(capability, comfyui_subdir)`` for a pull (P3 grouped layout).

    Priority for capability: explicit ``body.capability`` → the registry row's
    first capability → the curated entry's capability. ``comfyui_subdir`` comes
    from the curated entry only. Returns ``(None, None)`` for an unknown ad-hoc
    model so :func:`run_pull` falls back to the legacy flat layout.
    """
    if isinstance(body, dict):
        cap_raw = body.get("capability")
        if isinstance(cap_raw, str) and cap_raw.strip():
            return cap_raw.strip(), None

    try:
        existing = request.app.state.model_registry.get(model_id)
        caps = getattr(existing, "capabilities", None) or []
        if caps:
            return str(caps[0]), None
    except Exception:
        pass

    curated = get_curated(model_id)
    if curated is not None:
        subdir = (getattr(curated, "comfyui_subdir", "") or "").strip() or None
        return (curated.capability or None), subdir
    return None, None


def seed_registry_from_body(
    request: Request,
    model_id: str,
    hf_repo: str,
    hf_file: str,
    labels: list[str] | None,
    chat_template: str | None = None,
) -> None:
    """Upsert a registry row for ``model_id`` from body-supplied HF coords.

    The AddByHfModal flow inspects a HF repo, picks a variant, and POSTs
    the pull with the chosen ``hf_repo`` + ``hf_filename`` in the body
    against a brand-new id (e.g. ``user.Qwen3.6-27B-MTP``). Seeding the
    registry here gives ``resolve_pull_source`` something to look up on
    retries / reattach, and lets the dashboard's Models view surface the
    in-flight pull against a real row instead of a phantom id.

    Idempotent: if the row already exists, refresh its HF coordinates
    so a retry with a different variant against the same id stays
    intentional (rather than silently re-pulling the old variant).
    """
    from hal0.config import paths
    from hal0.registry.model import Model, ModelDefaults
    from hal0.registry.store import ModelAlreadyExists

    registry = request.app.state.model_registry
    provisional_path = str(paths.models_dir() / model_id / hf_file)
    caps = [str(c).strip() for c in (labels or []) if str(c).strip()] or ["chat"]
    # A pinned chat template is a launcher default that only matters at load
    # time (long after the pull finishes), so seed it now while the row is
    # created — the modal closes on pull start and can't sequence a later PUT.
    # The "auto" sentinel means "use the GGUF-embedded template" → no override.
    ct = (chat_template or "").strip()
    defaults = ModelDefaults(chat_template=ct) if ct and ct != "auto" else None
    try:
        existing = registry.get(model_id)
    except Exception:
        existing = None
    if existing is not None:
        # Refresh HF coords so a retry with a different variant lands, and
        # carry a freshly-picked chat template onto the existing row.
        patch: dict[str, Any] = {"hf_repo": hf_repo, "hf_filename": hf_file}
        if defaults is not None:
            patch["defaults"] = defaults.model_dump(exclude_none=True)
        with contextlib.suppress(Exception):
            registry.update(model_id, patch)
        return
    entry = Model(
        id=model_id,
        name=model_id,
        path=provisional_path,
        size_bytes=0,
        capabilities=caps,
        hf_repo=hf_repo,
        hf_filename=hf_file,
        tags=["user-added"],
        metadata={"source": "add-by-hf"},
        defaults=defaults,
    )
    # Race with another caller — the existing row already has coords.
    with contextlib.suppress(ModelAlreadyExists):
        registry.add(entry)


def resolve_pull_source_with_body(
    request: Request,
    model_id: str,
    body: dict[str, Any] | None,
) -> tuple[str, str, str | None, bool]:
    """Resolve (hf_repo, hf_file, mmproj_file) with an optional body override.

    Returns ``(repo, file, mmproj_file, from_body)`` where ``from_body=True``
    means the caller supplied both coordinate fields in the request payload —
    the pull route uses that flag to decide whether to seed a registry row.

    ``mmproj_file`` (WS-11) resolves body-first (the Add-by-HF modal sends
    ``mmproj_filename`` for vision models), then the curated entry's
    ``mmproj_file``; ``None`` means single-file pull.

    A partial body (only one of the two coordinate fields) is ignored to
    avoid silently mixing a body coord with a stale registry coord; the
    fallback path raises 422 with the existing message so the caller
    gets the same hint they would on an empty body.
    """
    mmproj: str | None = None
    if isinstance(body, dict):
        mm_raw = body.get("mmproj_filename") or body.get("mmproj_file")
        if isinstance(mm_raw, str) and mm_raw.strip():
            mmproj = mm_raw.strip()
        repo_raw = body.get("hf_repo")
        file_raw = body.get("hf_filename") or body.get("hf_file")
        if isinstance(repo_raw, str) and isinstance(file_raw, str):
            repo = repo_raw.strip()
            file = file_raw.strip()
            if repo and file:
                return repo, file, mmproj, True
    repo, file = resolve_pull_source(request, model_id)
    if mmproj is None:
        # Curated vision picks carry their mmproj alongside the main GGUF.
        curated = get_curated(model_id)
        if curated is not None:
            mm = (getattr(curated, "mmproj_file", "") or "").strip()
            mmproj = mm or None
    return repo, file, mmproj, False


def schedule_pull_task(
    app_state: Any,
    model_id: str,
    coro: Coroutine[Any, Any, None],
) -> asyncio.Task[None]:
    """Launch a pull body as a detached ``asyncio.Task``, tracked for shutdown.

    Deliberately NOT a Starlette ``BackgroundTasks`` entry (issue #1225):
    those run to completion inside the same ASGI call that sent the
    response, which keeps the HTTP connection "in flight" for the whole
    download — uvicorn won't dispatch the ASGI ``lifespan.shutdown`` event
    until every connection (including that one) closes, so a live multi-GB
    pull blocks `systemctl restart hal0-api` until systemd's own stop
    timeout SIGKILLs the process mid-write. Scheduling a detached task lets
    the request return immediately (closing its connection) while the
    download keeps running independently; ``app_state.model_pull_tasks``
    gives the lifespan shutdown path (hal0.api._shutdown_pull_jobs) a
    handle to find and cancel it with a bounded wait.
    """
    task = asyncio.create_task(coro)
    tasks: dict[str, asyncio.Task[None]] = app_state.model_pull_tasks
    tasks[model_id] = task

    def _untrack(t: asyncio.Task[None], _model_id: str = model_id) -> None:
        if tasks.get(_model_id) is t:
            tasks.pop(_model_id, None)

    task.add_done_callback(_untrack)
    return task


async def run_pull_with_events(
    job: PullJob,
    *,
    hf_repo: str,
    hf_file: str,
    registry: Any,
    hf_token: str | None,
    event_bus: Any | None,
    capability: str | None = None,
    comfyui_subdir: str | None = None,
    mmproj_file: str | None = None,
    dest_override: str | None = None,
) -> None:
    """Wrap ``run_pull`` so footer-visible progress events fan out.

    Emits ``pull.progress`` at each 10% decile (computed lazily — we
    snapshot the deciles already reached on the job's ``_last_decile``
    attr) plus terminal events on success / failure / cancellation. The
    HF download itself is untouched; we listen to the same progress
    signal SSE listens to so the byte counts stay authoritative.

    ``capability``/``comfyui_subdir`` (P3) route the download into the
    capability-grouped store layout; both default to None → legacy flat layout.
    """
    if event_bus is None:
        try:
            await run_pull(
                job,
                hf_repo=hf_repo,
                hf_file=hf_file,
                registry=registry,
                hf_token=hf_token,
                capability=capability,
                comfyui_subdir=comfyui_subdir,
                mmproj_file=mmproj_file,
                dest_override=dest_override,
            )
        finally:
            # Persist terminal state so a restart-surviving status poll resolves
            # (#626) — in a finally so a re-raised cancellation is still recorded.
            persist_pull_job(job)
        return

    async def _emit_progress() -> None:
        last_decile: int = getattr(job, "_last_pull_decile", -1)
        while job.state in ("queued", "running"):
            event = job.progress_event
            try:
                await asyncio.wait_for(event.wait(), timeout=2.0)
            except TimeoutError:
                continue
            if job.bytes_total > 0:
                pct = int((job.bytes_downloaded / job.bytes_total) * 100)
                decile = pct // 10
                if decile > last_decile and decile >= 1:
                    last_decile = decile
                    job._last_pull_decile = last_decile
                    speed = speed_bps(job)
                    eta = eta_s(job, speed)
                    await event_bus.emit(
                        "pull.progress",
                        "info",
                        f"pull:{job.model_id}",
                        f"{job.model_id}: {decile * 10}%",
                        data={
                            "model_id": job.model_id,
                            "downloaded": job.bytes_downloaded,
                            "total": job.bytes_total,
                            "pct": decile * 10,
                            "speed_bps": speed,
                            "eta_s": eta,
                        },
                    )

    progress_task = asyncio.create_task(_emit_progress())
    task_cancelled = False
    try:
        await run_pull(
            job,
            hf_repo=hf_repo,
            hf_file=hf_file,
            registry=registry,
            hf_token=hf_token,
            capability=capability,
            comfyui_subdir=comfyui_subdir,
            mmproj_file=mmproj_file,
            dest_override=dest_override,
        )
    except asyncio.CancelledError:
        # The TASK was cancelled (issue #1225: hal0-api's lifespan shutdown
        # does this to every in-flight pull). run_pull already recorded
        # job.state = "cancelled" before re-raising, but the terminal
        # footer event below sits AFTER this try/finally, which a
        # propagating CancelledError would otherwise skip — the operator
        # would never see a "pull cancelled" toast. Emit it from here
        # instead, then let the cancellation continue propagating.
        task_cancelled = True
        raise
    finally:
        progress_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await progress_task
        # Persist terminal state so a restart-surviving status poll resolves (#626).
        persist_pull_job(job)
        if task_cancelled:
            with contextlib.suppress(Exception):
                await emit_terminal_pull_event(event_bus, job)

    await emit_terminal_pull_event(event_bus, job)


async def emit_terminal_pull_event(event_bus: Any, job: PullJob) -> None:
    """Emit the success/failure/cancellation footer event for a pull.

    Shared between the HF pull wrapper and the FLM pull background task
    so both surfaces produce the same dashboard footer events.
    """
    if event_bus is None:
        return
    if job.state == "completed":
        await event_bus.emit(
            "pull.completed",
            "info",
            f"pull:{job.model_id}",
            f"{job.model_id}: download complete",
            data={
                "model_id": job.model_id,
                "downloaded": job.bytes_downloaded,
                "total": job.bytes_total,
                "sha256": job.sha256,
                "path": job.path,
            },
        )
    elif job.state == "failed":
        await event_bus.emit(
            "pull.failed",
            "error",
            f"pull:{job.model_id}",
            f"{job.model_id}: {job.error or 'pull failed'}",
            data={
                "model_id": job.model_id,
                "downloaded": job.bytes_downloaded,
                "total": job.bytes_total,
                "error": job.error,
                "error_code": job.error_code,
            },
        )
    elif job.state == "cancelled":
        await event_bus.emit(
            "pull.cancelled",
            "warn",
            f"pull:{job.model_id}",
            f"{job.model_id}: pull cancelled",
            data={
                "model_id": job.model_id,
                "downloaded": job.bytes_downloaded,
                "total": job.bytes_total,
            },
        )


def speed_bps(job: PullJob) -> float:
    """Approximate average bytes/s since the job started."""
    elapsed = max(time.time() - (job.started_at or time.time()), 0.001)
    return job.bytes_downloaded / elapsed


def eta_s(job: PullJob, speed: float) -> float | None:
    """Estimate seconds-to-completion from current rolling speed."""
    if speed <= 0 or job.bytes_total <= 0:
        return None
    remaining = max(job.bytes_total - job.bytes_downloaded, 0)
    return remaining / speed


async def start_flm_pull(
    model_id: str,
    request: Request,
    jobs: dict[str, PullJob],
) -> dict[str, object]:
    """Spawn a background ``flm pull`` job and return the job handle.

    Shares the PullJob/SSE plumbing with the HF pull path so the
    dashboard's pull progress UI works unchanged. The HF-specific
    progress decile + speed/ETA wrapper (:func:`run_pull_with_events`)
    isn't reused here because its progress event payload assumes byte
    deltas from a single HTTP stream — FLM's container emits multiple
    files with its own progress lines and we don't want to misreport
    rate. Footer events are emitted directly around the run.
    """
    job = make_job(model_id)
    jobs[model_id] = job
    # Persist the queued snapshot before returning so a status poll resolves
    # even if the daemon restarts before the background task runs (#626).
    persist_pull_job(job)
    registry = request.app.state.model_registry
    event_bus = getattr(request.app.state, "events", None)

    if event_bus is not None:
        await event_bus.emit(
            "pull.queued",
            "info",
            f"pull:{model_id}",
            f"{model_id}: queued (FLM/NPU)",
            data={"model_id": model_id, "source": "flm"},
        )

    async def _run_flm_with_events() -> None:
        try:
            await run_flm_pull(job, tag=model_id, registry=registry)
        finally:
            # Persist terminal state so a restart-surviving status poll resolves (#626).
            persist_pull_job(job)
            if event_bus is not None:
                await emit_terminal_pull_event(event_bus, job)

    schedule_pull_task(request.app.state, model_id, _run_flm_with_events())
    return {
        "id": job.job_id,
        "model_id": model_id,
        "state": job.state,
        "source": "flm",
    }
