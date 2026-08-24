"""Runner Image catalogue endpoints (mounted under /api/runner-images).

Mirrors ``hal0.api.routes.models``'s pattern: request→service→envelope
shells over ``hal0.registry.runner_image_sync`` (discovery) and
``hal0.registry.runner_pull_jobs`` (download orchestration).

Catalogue ids contain a ``/`` (the GHCR repo path, e.g.
``hal0ai/hal0-toolbox-cpu``), so every id-taking route uses the ``:path``
converter rather than a plain string segment.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from hal0.api.middleware.error_codes import NotFound
from hal0.registry import runner_pull_jobs as _pull_jobs
from hal0.registry.runner_image import RunnerImage
from hal0.registry.runner_image_sync import sync_runner_images
from hal0.registry.runner_pull import RunnerPullJob

log = logging.getLogger(__name__)

router = APIRouter()


def _image_to_dict(image: RunnerImage) -> dict[str, Any]:
    return image.model_dump()


# ── row enrichment (runner-image-catalogue v2) ───────────────────────────────
#
# The frozen cross-task contract (spec
# docs/superpowers/specs/2026-08-24-runner-image-catalogue-v2-design.md):
# list/sync rows gain ``available_tags`` (stored on the model),
# ``is_default`` and ``in_use_by`` (derived here, response-side only).


def _repo_of(ref: str) -> str:
    """``ghcr.io/x/y:tag`` / ``...@sha256:...`` -> ``ghcr.io/x/y``."""
    ref = ref.split("@", 1)[0]
    head, sep, tail = ref.rpartition(":")
    if sep and "/" not in tail:
        return head
    return ref


def enrich_row(
    image: RunnerImage,
    *,
    defaults: Mapping[str, tuple[str, str]],
    slot_usage: Mapping[str, str],
) -> dict[str, Any]:
    """One catalogue row + the derived catalogue-v2 contract fields. Pure.

    ``defaults`` maps runner-family key -> ``(effective image ref, source)``
    where source is ``"override"`` ([slots].default_images) or ``"release"``
    (the baked RUNNER_IMAGES constant); ``slot_usage`` maps slot name -> the
    image ref that slot's rendered unit / resolved config launches.

    ``is_default`` matches on the default ref's REPO against ``image.image``
    (contract: "any tag"), first matching family in ``defaults`` order wins
    (RUNNER_IMAGES insertion order — deterministic for the rocmfpx/vulkanfpx
    shared-image pair). ``in_use_by`` matches the exact ``image:tag`` ref.
    """
    row = _image_to_dict(image)
    is_default: dict[str, str] | None = None
    for family, (ref, source) in defaults.items():
        if _repo_of(ref) == image.image:
            is_default = {"family": family, "source": source}
            break
    row["is_default"] = is_default
    row_ref = f"{image.image}:{image.tag}"
    row["in_use_by"] = sorted(name for name, ref in slot_usage.items() if ref == row_ref)
    return row


def _effective_defaults() -> dict[str, tuple[str, str]]:
    """Family key -> ``(effective default ref, source)`` for every runner family.

    The override map is read fresh from hal0.toml (same live-read idiom as
    ``hal0.providers.container._slot_default_images`` — a Settings save lands
    on the next request); the release tier is the baked
    :data:`hal0.runners.RUNNER_IMAGES` constant, deliberately NOT the
    env/manifest-resolved ref: the contract's ``"release"`` source badge
    means "shipped default", and the env/manifest escape hatches stay out of
    the honesty story. Fail-soft: an unreadable config degrades to
    release-only defaults, never a 500.
    """
    from hal0.runners import RUNNER_IMAGES

    overrides: Mapping[str, str] = {}
    try:
        from hal0.config.loader import load_hal0_config

        raw = load_hal0_config().slots.default_images
        if isinstance(raw, Mapping):
            overrides = raw
    except Exception:
        log.warning("runner_images.default_images_load_failed", exc_info=True)
    out: dict[str, tuple[str, str]] = {}
    for key, runner in RUNNER_IMAGES.items():
        ref = overrides.get(key)
        if isinstance(ref, str) and ref:
            out[key] = (ref, "override")
        else:
            out[key] = (runner.image, "release")
    return out


def _slot_rendered_or_resolved_image(slot_cfg: Mapping[str, Any]) -> str:
    """The image ref one slot launches: rendered unit first, else resolved.

    Contract tier 1 — the ``/etc/containers/systemd`` Quadlet snapshot: the
    ground truth on an installed box (survives config edits that haven't
    been applied yet). Attempted only when this process is literally the
    ``hal0`` service account — the same gate
    ``hal0.providers.podman_introspect`` puts on its rootful seam, and what
    keeps a dev shell / CI runner / unit test from reading the HOST box's
    real units into a hermetic test. Everything else (and any read failure)
    → tier 2, the resolved config via the provider's own
    ``_resolve_image_ref`` chain, which is what the NEXT (re)start launches.
    """
    try:
        from hal0.providers.container import _QUADLET_DIR
        from hal0.slots.naming import slot_instance_token, slot_quadlet_name
        from hal0.system.seam import is_hal0_service_user

        if is_hal0_service_user():
            token = slot_instance_token(slot_cfg)
            if token:
                text = (_QUADLET_DIR / slot_quadlet_name(token)).read_text(encoding="utf-8")
                for line in text.splitlines():
                    if line.startswith("Image="):
                        return line[len("Image=") :].strip()
    except OSError:
        pass
    from hal0.providers.container import _resolve_image_ref

    return _resolve_image_ref(slot_cfg, None)


def _slot_image_usage() -> dict[str, str]:
    """Slot name -> launched image ref, for every configured slot.

    Contract: ``in_use_by`` is ``[]`` when unknown — every failure here
    degrades to skipping the slot (or the whole map), never a 500 on the
    catalogue routes.
    """
    usage: dict[str, str] = {}
    try:
        from hal0.config.loader import list_slots, load_slot_config

        stems = list_slots()
    except Exception:
        log.warning("runner_images.slot_usage_enumeration_failed", exc_info=True)
        return usage
    for stem in stems:
        try:
            cfg = load_slot_config(stem)
            slot_map = cfg.model_dump(mode="python")
            name = str(slot_map.get("name") or stem)
            usage[name] = _slot_rendered_or_resolved_image(slot_map)
        except Exception:
            log.warning("runner_images.slot_usage_failed slot=%s", stem, exc_info=True)
            continue
    return usage


def _enriched(images: list[RunnerImage]) -> list[dict[str, Any]]:
    defaults = _effective_defaults()
    slot_usage = _slot_image_usage()
    return [enrich_row(i, defaults=defaults, slot_usage=slot_usage) for i in images]


@router.get("")
async def list_runner_images(request: Request) -> dict[str, Any]:
    """Return every catalogued runner image (catalogue-v2 enriched rows)."""
    store = request.app.state.runner_image_registry
    return {"images": _enriched(store.list())}


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
        "images": _enriched(result.images),
        "images_json_ok": result.images_json_ok,
        "images_json_error": result.images_json_error,
        "probe_errors": result.probe_errors,
    }


@router.post("/{image_id:path}/pull", status_code=202)
async def pull_runner_image(
    image_id: str, request: Request, tag: str | None = None
) -> dict[str, object]:
    """Start a background ``podman pull`` for a catalogued runner image.

    ``?tag=`` pulls that catalogued tag (headline or ``available_tags``
    member — anything else is 404 ``runner_image.tag_not_available``);
    omitted, the row's headline tag is pulled, exactly as before. One pull
    slot per image: a different tag already in flight answers 409
    ``runner_image.pull_conflict``.
    """
    return await _pull_jobs.enqueue(request, image_id=image_id, tag=tag)


@router.get("/{image_id:path}/pull/status")
async def pull_runner_image_status(
    image_id: str, request: Request, tag: str | None = None
) -> dict[str, object]:
    """Current pull job for ``image_id``, with on-disk fallback.

    ``?tag=`` answers only when the recorded job is for that tag (job
    records carry ``tag``); a mismatch is 404, so a per-tag poller can't
    be fooled by another tag's job in the single per-image slot.
    """
    jobs: dict[str, RunnerPullJob] = request.app.state.runner_image_pull_jobs
    return _pull_jobs.status(image_id, jobs, tag=tag)


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
    return _enriched([image])[0]


__all__ = ["enrich_row", "router"]
