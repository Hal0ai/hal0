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
from hal0.providers.podman_introspect import LocalImagesDigests, images_digests
from hal0.registry import runner_pull_jobs as _pull_jobs
from hal0.registry.runner_image import RunnerImage, RunnerImageTag
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


def _local_store() -> LocalImagesDigests | None:
    """One store read per request — seam first, rootless fallback, honest None."""
    try:
        return images_digests()
    except Exception:
        log.warning("runner_images.local_store_read_failed", exc_info=True)
        return None


def _tag_badges(image: RunnerImage) -> dict[str, str]:
    """Tag -> ``"validated" | "candidate" | "deprecated"``, from the frozen
    ``hal0.config.schema`` image-ref sets.

    ``VULKAN_CAPABLE_IMAGE_REFS`` wins ties over the candidate set (a
    validated image should never read as merely "candidate"); the candidate
    set itself is fail-soft: ``DEFAULT_PROMPTFORGE_IMAGE`` doesn't exist on
    this branch yet (lands with #2129), so an unmatched import degrades to
    an empty candidate set rather than 500ing the catalogue routes.
    """
    from hal0.config.schema import STALE_ROCMFPX_IMAGE_REFS, VULKAN_CAPABLE_IMAGE_REFS

    candidates: set[str] = set()
    try:  # present only once #2129 merges; enrichment must not depend on it
        from hal0.config.schema import DEFAULT_PROMPTFORGE_IMAGE

        candidates.add(DEFAULT_PROMPTFORGE_IMAGE)
    except ImportError:
        pass
    out: dict[str, str] = {}
    for t in image.tags or [RunnerImageTag(tag=image.tag)]:
        ref = f"{image.image}:{t.tag}"
        if ref in VULKAN_CAPABLE_IMAGE_REFS:
            out[t.tag] = "validated"
        elif ref in candidates:
            out[t.tag] = "candidate"
        elif ref in STALE_ROCMFPX_IMAGE_REFS:
            out[t.tag] = "deprecated"
    return out


def enrich_row(
    image: RunnerImage,
    *,
    defaults: Mapping[str, tuple[str, str]],
    slot_usage: Mapping[str, str],
    local: LocalImagesDigests | None,
) -> dict[str, Any]:
    """One catalogue row + the derived catalogue-v2/v3 contract fields. Pure.

    ``defaults`` maps runner-family key -> ``(effective image ref, source)``
    where source is ``"override"`` ([slots].default_images) or ``"release"``
    (the baked RUNNER_IMAGES constant); ``slot_usage`` maps slot name -> the
    image ref that slot's rendered unit / resolved config launches.

    ``is_default`` matches on the default ref's REPO against ``image.image``
    (contract: "any tag"), first matching family in ``defaults`` order wins
    (RUNNER_IMAGES insertion order — deterministic for the rocmfpx/vulkanfpx
    shared-image pair). ``in_use_by`` matches the exact ``image:tag`` ref.

    ``local`` is one request's ``podman_introspect.images_digests()`` read
    (``None`` when neither store answered) — store-truth (v3): ``store_state``
    ("present"/"missing"/"unknown"), per-tag ``downloaded`` (``None`` when
    the store is unknown), ``store_context``, and ``badges``.
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

    local_digests = set(filter(None, local.refs.values())) if local else set()
    local_refs = set(local.refs) if local else set()

    def _tag_state(t: RunnerImageTag) -> bool | None:
        if local is None:
            return None
        if t.digest and t.digest in local_digests:
            return True
        return f"{image.image}:{t.tag}" in local_refs

    tag_list = image.tags or []
    row["tags"] = [{**t.model_dump(), "downloaded": _tag_state(t)} for t in tag_list]
    headline = next((t for t in tag_list if t.tag == image.tag), None)
    if local is None:
        row["store_state"] = "unknown"
        row["downloaded"] = bool(image.local_path)  # marker only as last resort
    else:
        present = (
            _tag_state(headline)
            if headline is not None
            else f"{image.image}:{image.tag}" in local_refs
            or (image.digest in local_digests if image.digest else False)
        )
        row["store_state"] = "present" if present else "missing"
        row["downloaded"] = bool(present)
    row["store_context"] = local.context if local else None
    row["badges"] = _tag_badges(image)
    return row


def _effective_defaults() -> dict[str, tuple[str, str]]:
    """Family key -> ``(effective default ref, source)`` for every runner family.

    Full source vocabulary, mirroring :func:`hal0.runners.resolve_runner_image`'s
    tiers WITHOUT calling it (that function only knows env → manifest →
    release; the ``[slots].default_images`` override sits ABOVE all three —
    it's what a slot actually launches with when set, same live-read idiom as
    ``hal0.providers.container._slot_default_images``, a Settings save lands
    on the next request):

      1. ``override`` — ``[slots].default_images[key]``.
      2. ``env`` — ``HAL0_TOOLBOX_IMAGE_<KEY>``.
      3. ``manifest`` — ``manifest_image_ref(runner.manifest_key)``.
      4. ``release`` — the baked :data:`hal0.runners.RUNNER_IMAGES` default.

    Fail-soft throughout: an unreadable config or manifest degrades to the
    next tier, never a 500.
    """
    import os

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
            continue
        env_val = os.environ.get(f"HAL0_TOOLBOX_IMAGE_{key.upper()}", "").strip()
        if env_val:
            out[key] = (env_val, "env")
            continue
        if runner.manifest_key:
            try:
                from hal0.config.loader import manifest_image_ref

                pinned = manifest_image_ref(runner.manifest_key)
            except Exception:
                pinned = None
            if pinned:
                out[key] = (pinned, "manifest")
                continue
        out[key] = (runner.image, "release")
    return out


def _families_payload(
    images: list[RunnerImage],
    defaults: Mapping[str, tuple[str, str]],
    slot_usage: Mapping[str, str],
    local: LocalImagesDigests | None,
) -> list[dict[str, Any]]:
    """Launch-truth per-family summary (runner-image-catalogue v3, task 9).

    For every :data:`hal0.runners.RUNNER_IMAGES` family key: the effective
    ref + its source tier (see :func:`_effective_defaults`), the store state
    of that ref, which slots launch it (``slots``) vs. a different tag of
    the same repo (``pinned_slots``), the newest release-shaped tag
    catalogued for that repo, and whether that newest tag's digest differs
    from the effective ref's own digest (``update_available``).

    Pure function like ``enrich_row`` (no request state) — trivially safe
    to call from both ``list_runner_images`` and ``sync_runner_images_route``
    off the same ``defaults``/``slot_usage``/``local`` triple.
    """
    import re as _re

    release_re = _re.compile(r"^v?\d+(\.\d+)*$")
    by_repo: dict[str, RunnerImage] = {i.image: i for i in images}
    out: list[dict[str, Any]] = []
    for family, (ref, source) in defaults.items():
        repo = _repo_of(ref)
        row = by_repo.get(repo)
        newest = None
        if row is not None:
            cand = next((t for t in row.tags if release_re.match(t.tag) and t.digest), None)
            if cand is not None:
                newest = {"tag": cand.tag, "digest": cand.digest}
        eff_digest = None
        if row is not None:
            _, _, eff_tag = ref.rpartition(":")
            eff = next((t for t in row.tags if t.tag == eff_tag), None)
            eff_digest = eff.digest if eff else None
        if local is None:
            store_state = "unknown"
        else:
            present = ref in local.refs or (
                eff_digest is not None and eff_digest in set(local.refs.values())
            )
            store_state = "present" if present else "missing"
        slots = sorted(n for n, r in slot_usage.items() if r == ref)
        pinned = sorted(n for n, r in slot_usage.items() if r != ref and _repo_of(r) == repo)
        out.append(
            {
                "family": family,
                "effective_ref": ref,
                "source": source,
                "store_state": store_state,
                "slots": slots,
                "pinned_slots": pinned,
                "newest_release": newest,
                "update_available": bool(newest and eff_digest and newest["digest"] != eff_digest),
            }
        )
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


def _request_context() -> tuple[
    dict[str, tuple[str, str]], dict[str, str], LocalImagesDigests | None
]:
    """One ``defaults``/``slot_usage``/``local`` triple, shared by every row
    AND the families payload for a single request — computed once each
    (``_local_store`` is explicitly "one store read per request")."""
    defaults = _effective_defaults()
    slot_usage = _slot_image_usage()
    local = _local_store()
    return defaults, slot_usage, local


def _enriched(images: list[RunnerImage]) -> list[dict[str, Any]]:
    defaults, slot_usage, local = _request_context()
    return [enrich_row(i, defaults=defaults, slot_usage=slot_usage, local=local) for i in images]


def _safe_families_payload(
    images: list[RunnerImage],
    defaults: Mapping[str, tuple[str, str]],
    slot_usage: Mapping[str, str],
    local: LocalImagesDigests | None,
) -> list[dict[str, Any]]:
    """Fail-soft wrapper: the families payload must never 500 a catalogue
    route — a bug in this newer, more speculative computation degrades to
    an empty list rather than taking ``images``/``rows`` down with it."""
    try:
        return _families_payload(images, defaults, slot_usage, local)
    except Exception:
        log.warning("runner_images.families_payload_failed", exc_info=True)
        return []


@router.get("")
async def list_runner_images(request: Request) -> dict[str, Any]:
    """Return every catalogued runner image (catalogue-v2 enriched rows)
    plus the launch-truth ``families`` summary (catalogue v3, task 9)."""
    store = request.app.state.runner_image_registry
    images = store.list()
    defaults, slot_usage, local = _request_context()
    rows = [enrich_row(i, defaults=defaults, slot_usage=slot_usage, local=local) for i in images]
    return {
        "images": rows,
        "families": _safe_families_payload(images, defaults, slot_usage, local),
    }


@router.get("/downloaded")
async def list_downloaded_runner_images(request: Request) -> dict[str, Any]:
    """Locally-downloaded runner images only (store-truth, catalogue v3).

    Shaped for the sibling ``fix/slot-edit-drawer-cleanup`` branch's Runner
    Image dropdown. Routes through the same ``_enriched`` rows as every
    other list route and filters on the enriched ``downloaded`` flag
    (``store_state == "present"``, or the ``local_path`` marker when the
    store couldn't be read) — one truth instead of two: the store's own
    ``local_path``-only ``list_downloaded()`` stays available for other
    callers, but this route no longer trusts it alone.
    """
    store = request.app.state.runner_image_registry
    rows = _enriched(store.list())
    return {"images": [r for r in rows if r["downloaded"]]}


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
    defaults, slot_usage, local = _request_context()
    rows = [
        enrich_row(i, defaults=defaults, slot_usage=slot_usage, local=local) for i in result.images
    ]
    return {
        "images": rows,
        "families": _safe_families_payload(result.images, defaults, slot_usage, local),
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

    ``?tag=`` answers only with a job for that tag: the in-memory slot
    when it matches, else that tag's persisted snapshot (snapshots are
    per-tag files, so a newer pull can't orphan an older tag's result),
    else 404 — a per-tag poller can't be fooled by another tag's job.
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
