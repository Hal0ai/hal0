"""Model registry endpoints (mounted under /api/models).

This is the *internal* models surface for the dashboard — distinct from
OpenAI-compat `/v1/models`.  Aggregates entries from every configured
upstream so the dashboard's Models view shows what's actually reachable,
plus any locally-registered models from the ModelRegistry.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import StreamingResponse

from hal0.api.middleware.auth import require_writer
from hal0.api.middleware.error_codes import Hal0Error
from hal0.registry.curated import get_curated
from hal0.registry.pull import (
    PullInvalidSource,
    PullJob,
    PullJobNotFound,
    make_job,
    run_pull,
)

# See slots.py for the writer-gate rationale.
_writer = [Depends(require_writer)]

router = APIRouter()


# Known-alias model ids that upstream gateways advertise as routing
# shortcuts (haloai's hermes-proxy exposes them as "primary", "tiny",
# etc., plus haloai:* namespaced variants).  Filtered from the dashboard
# Models view because they're not real models — they're routes.
_ALIAS_NAMES = frozenset(
    {
        "primary",
        "medium",
        "tiny",
        "embed",
        "rerank",
        "npu",
        "coding",
        "coder",
        "whisper",
        "moonshine",
        "vibevoice",
        "kokoro",
        "tts-1",
        "tts-1-hd",
        "bge-reranker",
        "nomic-embed",
    }
)


def _is_alias(model_id: str) -> bool:
    """Filter out routing aliases that aren't real models."""
    if model_id.startswith("haloai:"):
        return True
    return model_id in _ALIAS_NAMES


@router.get("")
async def list_models(request: Request) -> dict[str, Any]:
    """Aggregate models from every upstream.  Dashboard reads this."""
    upstreams = request.app.state.upstreams
    cache = getattr(request.app.state, "model_cache", {})
    now = int(time.time())
    data: list[dict[str, Any]] = []
    seen: set[str] = set()
    filtered = 0
    for u in upstreams.list():
        try:
            ids = cache.get(u.name) or await upstreams.fetch_models(u.name)
            cache[u.name] = ids
        except Exception:
            ids = []
        for mid in ids:
            if mid in seen:
                continue
            if _is_alias(mid):
                filtered += 1
                continue
            seen.add(mid)
            data.append(
                {
                    "id": mid,
                    "name": mid,
                    "object": "model",
                    "created": now,
                    "owned_by": u.name,
                    "upstream": u.name,
                }
            )
    return {"models": data, "count": len(data), "filtered_aliases": filtered}


@router.post("", status_code=201, dependencies=_writer)
async def create_model(request: Request) -> dict[str, Any]:
    """Register a new model in the local ModelRegistry.

    Body shape: serialized ``Model`` — see ``hal0.registry.store.Model``.
    The model must already exist on disk (e.g. dropped into
    ``/var/lib/hal0/models/``) — this endpoint records metadata, it does
    not download. Use POST /api/models/{id}/pull for downloads.
    """
    from hal0.registry.store import Model

    registry = request.app.state.model_registry
    try:
        body = await request.json()
    except Exception as exc:
        raise Hal0Error("body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise Hal0Error("body must be a JSON object")
    try:
        model = Model(**body)
    except (TypeError, ValueError) as exc:
        raise Hal0Error(f"invalid Model payload: {exc}") from exc
    registry.add(model)
    return _model_to_dict(model)


def _model_to_dict(model: Any) -> dict[str, Any]:
    """Serialise a registry Model to the dashboard's flat shape."""
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return {**getattr(model, "__dict__", {})}


@router.get("/{model_id}")
async def get_model(model_id: str, request: Request) -> dict[str, Any]:
    """Return a single model by id, preferring the local registry then
    falling back to whichever upstream advertises it."""
    registry = request.app.state.model_registry
    if registry.has(model_id):
        return _model_to_dict(registry.get(model_id))
    listing = await list_models(request)
    for m in listing["models"]:
        if m["id"] == model_id:
            return m
    raise Hal0Error(
        f"model {model_id!r} not found in registry or any upstream catalog",
        details={"model_id": model_id},
    )


@router.put("/{model_id}", dependencies=_writer)
async def update_model(model_id: str, request: Request) -> dict[str, Any]:
    """Apply partial updates to a registered model's metadata."""
    registry = request.app.state.model_registry
    try:
        body = await request.json()
    except Exception as exc:
        raise Hal0Error("body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise Hal0Error("body must be a JSON object")
    model = registry.update(model_id, body)
    return _model_to_dict(model)


@router.delete("/{model_id}", dependencies=_writer)
async def delete_model(model_id: str, request: Request) -> dict[str, object]:
    """Remove a model from the local registry (does not delete files)."""
    registry = request.app.state.model_registry
    removed = registry.remove(model_id)
    return {"id": model_id, "deleted": bool(removed)}


def _resolve_pull_source(request: Request, model_id: str) -> tuple[str, str]:
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


@router.post("/{model_id}/pull", status_code=202, dependencies=_writer)
async def pull_model(
    model_id: str,
    request: Request,
    background: BackgroundTasks,
) -> dict[str, object]:
    """Start a background HuggingFace pull and return a job handle.

    Idempotent-ish: if a pull for this model_id is already in
    ``queued``/``running`` state, the existing job's handle is returned
    rather than spawning a duplicate. A completed/failed/cancelled job
    is replaced.
    """
    jobs: dict[str, PullJob] = request.app.state.model_pull_jobs

    # Don't double-pull. A user spamming the wizard's Download button
    # shouldn't kick off two streams against the same HF URL.
    existing = jobs.get(model_id)
    if existing is not None and existing.state in ("queued", "running"):
        return {
            "id": existing.job_id,
            "model_id": model_id,
            "state": existing.state,
            "resumed": True,
        }

    hf_repo, hf_file = _resolve_pull_source(request, model_id)
    job = make_job(model_id)
    jobs[model_id] = job

    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    registry = request.app.state.model_registry
    background.add_task(
        run_pull,
        job,
        hf_repo=hf_repo,
        hf_file=hf_file,
        registry=registry,
        hf_token=hf_token,
    )
    return {
        "id": job.job_id,
        "model_id": model_id,
        "state": job.state,
        "hf_repo": hf_repo,
        "hf_file": hf_file,
    }


@router.get("/{model_id}/pull/status")
async def pull_status(model_id: str, request: Request) -> dict[str, object]:
    """Return the current pull job for ``model_id``.

    Mirror of the updater route shape — `id`, `state`, `bytes_*`,
    `error*`, `path`, `sha256`. Polling at ~500ms is fine; for live
    progress prefer the SSE stream.
    """
    jobs: dict[str, PullJob] = request.app.state.model_pull_jobs
    job = jobs.get(model_id)
    if job is None:
        raise PullJobNotFound(
            f"no pull job for model {model_id!r}",
            details={"model_id": model_id},
        )
    return job.as_dict()


@router.get("/{model_id}/pull/stream")
async def pull_stream(model_id: str, request: Request) -> StreamingResponse:
    """SSE stream of pull progress.

    Emits one ``data:`` frame at start, then one per ~256 KiB or every
    500ms (whichever is rarer), and a final frame on completion
    /failure/cancellation. Idempotent: subscribing after the job has
    finished yields one frame with the terminal state and closes.
    """
    jobs: dict[str, PullJob] = request.app.state.model_pull_jobs
    job = jobs.get(model_id)
    if job is None:
        raise PullJobNotFound(
            f"no pull job for model {model_id!r}",
            details={"model_id": model_id},
        )

    async def _gen() -> Any:
        # Emit an immediate snapshot so SSE clients don't sit at zero
        # while waiting for the first progress signal.
        yield f"data: {json.dumps(job.as_dict())}\n\n"
        while job.state in ("queued", "running"):
            event = job.progress_event
            try:
                await asyncio.wait_for(event.wait(), timeout=5.0)
            except TimeoutError:
                # Keep-alive — surfaces stuck downloads without closing
                # the stream.
                yield f"data: {json.dumps(job.as_dict())}\n\n"
                continue
            yield f"data: {json.dumps(job.as_dict())}\n\n"
        # One terminal frame so the UI sees the final state and can close.
        yield f"data: {json.dumps(job.as_dict())}\n\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{model_id}/pull/cancel", dependencies=_writer)
async def pull_cancel(model_id: str, request: Request) -> dict[str, object]:
    """Request cancellation of an in-flight pull.

    Sets a cancel flag the background task observes on the next chunk
    boundary; the partial download is unlinked, the job transitions to
    ``cancelled``. Idempotent — cancelling a completed job is a no-op.
    """
    jobs: dict[str, PullJob] = request.app.state.model_pull_jobs
    job = jobs.get(model_id)
    if job is None:
        raise PullJobNotFound(
            f"no pull job for model {model_id!r}",
            details={"model_id": model_id},
        )
    if job.state in ("queued", "running"):
        job.cancel_requested = True
    return job.as_dict()
