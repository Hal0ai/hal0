"""Model update-check endpoints (mounted under /api/models/updates).

The Models dashboard needs two things for HF-sourced rows:

* ``GET  /api/models/updates``        — which installed models have a newer
  build of their file sitting in the HF repo (sha probe, TTL-cached)?
* ``POST /api/models/updates/apply``  — re-pull the outdated ones (all of
  them, or an explicit subset) through the existing pull machinery so
  progress/SSE/footer events work unchanged.

Lives in its own module (mounted BEFORE the main models router so the
literal ``/updates`` path wins over ``/{model_id}``) to keep
routes/models.py from growing further. The comparison logic itself is
:mod:`hal0.registry.updates`.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request

from hal0.api.middleware.error_codes import BadRequest
from hal0.registry.pull import PullJob, make_job, persist_pull_job
from hal0.registry.updates import check_for_updates, clear_check_cache

router = APIRouter()

log = logging.getLogger(__name__)


def _hf_token() -> str | None:
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")


@router.get("")
async def list_updates(request: Request, refresh: bool = False) -> dict[str, Any]:
    """Return per-model update statuses for every HF-sourced registry row.

    ``?refresh=true`` drops the probe cache and re-HEADs huggingface.co;
    otherwise probes within the TTL are reused. Rows without HF
    coordinates (scanned files, FLM tags, upstream-only ids) are not in
    the response at all — they have no update story.

    Response::

        {
          "updates": [ {model_id, status, update_available, ...}, ... ],
          "available": ["model-id", ...],
          "count": <checked rows>,
          "available_count": <len(available)>
        }
    """
    registry = request.app.state.model_registry
    if refresh:
        clear_check_cache()
    statuses = await check_for_updates(registry.list(), hf_token=_hf_token())
    available = [s["model_id"] for s in statuses if s["update_available"]]

    event_bus = getattr(request.app.state, "events", None)
    if refresh and available and event_bus is not None:
        await event_bus.emit(
            "model.updates_available",
            "info",
            "models:updates",
            f"{len(available)} model update(s) available on Hugging Face",
            data={"model_ids": available, "count": len(available)},
        )

    return {
        "updates": statuses,
        "available": available,
        "count": len(statuses),
        "available_count": len(available),
    }


@router.post("/apply", status_code=202)
async def apply_updates(
    request: Request,
    background: BackgroundTasks,
) -> dict[str, Any]:
    """Re-pull outdated models — the dashboard's "Update all" button.

    Body (optional)::

        {"model_ids": ["qwen3-4b-q4_k_m", ...]}

    Without a body (or without ``model_ids``) every model whose cached
    check says ``update_available`` is re-pulled. Each update is a
    normal HF pull: same job store, same SSE stream, same footer
    events, same atomic on-disk swap — so the existing downloads pane
    shows progress with zero new plumbing.

    Response ``{"started": [...], "skipped": [...]}`` where a skip
    carries a ``reason`` (``pull_active`` / ``no_hf_source``).
    """
    # Local import — these helpers live beside the pull route and pulling
    # them at call time keeps this module import-light (mirrors the
    # models.py convention for cross-module helpers).
    from hal0.api.routes.models import (
        _resolve_pull_capability,
        _resolve_pull_source_with_body,
        _run_pull_with_events,
    )

    registry = request.app.state.model_registry
    jobs: dict[str, PullJob] = request.app.state.model_pull_jobs
    event_bus = getattr(request.app.state, "events", None)
    hf_token = _hf_token()

    body: dict[str, Any] = {}
    try:
        if int(request.headers.get("content-length") or 0) > 0:
            parsed = await request.json()
            if isinstance(parsed, dict):
                body = parsed
    except Exception as exc:
        raise BadRequest("body must be valid JSON", details={"error": str(exc)}) from exc

    raw_ids = body.get("model_ids")
    if raw_ids is not None and not isinstance(raw_ids, list):
        raise BadRequest("'model_ids' must be a list of model id strings")

    if isinstance(raw_ids, list):
        targets = [str(m).strip() for m in raw_ids if isinstance(m, str) and str(m).strip()]
        if not targets:
            raise BadRequest("'model_ids' must contain at least one model id")
    else:
        # No explicit subset → everything the (cached) check flags.
        statuses = await check_for_updates(registry.list(), hf_token=hf_token)
        targets = [s["model_id"] for s in statuses if s["update_available"]]

    started: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for model_id in targets:
        existing = jobs.get(model_id)
        if existing is not None and existing.state in ("queued", "running"):
            skipped.append({"model_id": model_id, "reason": "pull_active"})
            continue
        try:
            hf_repo, hf_file, mmproj_file, _ = _resolve_pull_source_with_body(
                request, model_id, None
            )
        except Exception:
            skipped.append({"model_id": model_id, "reason": "no_hf_source"})
            continue

        job = make_job(model_id)
        jobs[model_id] = job
        persist_pull_job(job)

        if event_bus is not None:
            await event_bus.emit(
                "pull.queued",
                "info",
                f"pull:{model_id}",
                f"{model_id}: queued (update from {hf_repo}/{hf_file})",
                data={
                    "model_id": model_id,
                    "hf_repo": hf_repo,
                    "hf_file": hf_file,
                    "update": True,
                },
            )

        capability, comfyui_subdir = _resolve_pull_capability(request, model_id, None)
        background.add_task(
            _run_pull_with_events,
            job,
            hf_repo=hf_repo,
            hf_file=hf_file,
            registry=registry,
            hf_token=hf_token,
            event_bus=event_bus,
            capability=capability,
            comfyui_subdir=comfyui_subdir,
            mmproj_file=mmproj_file,
        )
        started.append({"model_id": model_id, "job_id": job.job_id, "state": job.state})

    return {
        "started": started,
        "skipped": skipped,
        "count": len(started),
    }
