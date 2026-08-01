"""Runner Image catalogue endpoints (mounted under /api/runner-images).

Mirrors ``hal0.api.routes.models``'s pattern: request→service→envelope
shells over ``hal0.registry.runner_image_sync`` (discovery) and
``hal0.registry.runner_pull_jobs`` (download orchestration).

Catalogue ids contain a ``/`` (the GHCR repo path, e.g.
``hal0ai/hal0-toolbox-cpu``), so every id-taking route uses the ``:path``
converter rather than a plain string segment.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from hal0.api.middleware.error_codes import NotFound
from hal0.registry import runner_pull_jobs as _pull_jobs
from hal0.registry.runner_image import RunnerImage
from hal0.registry.runner_image_sync import sync_runner_images
from hal0.registry.runner_pull import RunnerPullJob

router = APIRouter()


def _image_to_dict(image: RunnerImage) -> dict[str, Any]:
    return image.model_dump()


@router.get("")
async def list_runner_images(request: Request) -> dict[str, Any]:
    """Return every catalogued runner image."""
    store = request.app.state.runner_image_registry
    return {"images": [_image_to_dict(i) for i in store.list()]}


@router.get("/downloaded")
async def list_downloaded_runner_images(request: Request) -> dict[str, Any]:
    """Locally-downloaded runner images only.

    Shaped for the sibling ``fix/slot-edit-drawer-cleanup`` branch's
    Runner Image dropdown — a flat list of ``{id, image, tag, local_path}``
    rows for images that have actually landed on this host.
    """
    store = request.app.state.runner_image_registry
    return {"images": [_image_to_dict(i) for i in store.list_downloaded()]}


@router.get("/pulls/list")
async def list_runner_image_pulls(request: Request) -> list[dict[str, Any]]:
    """All runner-image pull jobs (active in-memory + persisted terminal).

    Registered ahead of the ``/{image_id:path}`` catch-all below — a plain
    ``str`` segment can't be used for ``image_id`` (catalogue ids contain
    ``/``), so every literal-suffix route (``/downloaded``, ``/pulls/list``)
    must be declared before the greedy ``:path`` converter or FastAPI would
    route them into ``get_runner_image`` instead.
    """
    jobs: dict[str, RunnerPullJob] = request.app.state.runner_image_pull_jobs
    return _pull_jobs.list_all(jobs)


@router.post("/sync", status_code=202)
async def sync_runner_images_route(request: Request) -> dict[str, Any]:
    """Run one discovery pass (GHCR anon probe + images.json merge) now.

    Synchronous from the caller's perspective (unlike the pull job, a
    sync run is bounded by a handful of small HTTP round-trips, not a
    multi-GB transfer) — the manual "sync now" button and the scheduled
    poll both call this.
    """
    store = request.app.state.runner_image_registry
    result = await sync_runner_images(store)
    return {
        "images": [_image_to_dict(i) for i in result.images],
        "images_json_ok": result.images_json_ok,
        "images_json_error": result.images_json_error,
        "probe_errors": result.probe_errors,
    }


@router.post("/{image_id:path}/pull", status_code=202)
async def pull_runner_image(image_id: str, request: Request) -> dict[str, object]:
    """Start a background ``podman pull`` for a catalogued runner image."""
    return await _pull_jobs.enqueue(request, image_id=image_id)


@router.get("/{image_id:path}/pull/status")
async def pull_runner_image_status(image_id: str, request: Request) -> dict[str, object]:
    """Current pull job for ``image_id``, with on-disk fallback."""
    jobs: dict[str, RunnerPullJob] = request.app.state.runner_image_pull_jobs
    return _pull_jobs.status(image_id, jobs)


@router.get("/{image_id:path}/pull/stream")
async def pull_runner_image_stream(image_id: str, request: Request) -> StreamingResponse:
    """SSE stream of pull progress (layers_done/layers_total/state)."""
    return _pull_jobs.build_stream_response(image_id, request)


@router.post("/{image_id:path}/pull/cancel")
async def pull_runner_image_cancel(image_id: str, request: Request) -> dict[str, object]:
    """Request cancellation of an in-flight runner-image pull."""
    jobs: dict[str, RunnerPullJob] = request.app.state.runner_image_pull_jobs
    return _pull_jobs.cancel(image_id, jobs)


@router.get("/{image_id:path}")
async def get_runner_image(image_id: str, request: Request) -> dict[str, Any]:
    """Return one catalogued runner image (the card detail view).

    Registered LAST among the GET routes: the ``:path`` converter is
    greedy and would otherwise swallow ``/pull/status``/``/pull/stream``
    suffixes into ``image_id`` if declared before them.
    """
    store = request.app.state.runner_image_registry
    image = store.get(image_id)
    if image is None:
        raise NotFound(
            f"runner image {image_id!r} not in catalogue",
            details={"image_id": image_id},
            code="runner_image.not_found",
        )
    return _image_to_dict(image)


__all__ = ["router"]
