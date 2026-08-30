"""Runner Image catalogue endpoints (mounted under /api/runner-images).

Mirrors ``hal0.api.routes.models``'s pattern: request→service→envelope
shells over ``hal0.registry.runner_image_sync`` (discovery) and
``hal0.registry.runner_pull_jobs`` (download orchestration).

Catalogue ids contain a ``/`` (the GHCR repo path, e.g.
``hal0ai/hal0-toolbox-cpu``), so every id-taking route uses the ``:path``
converter rather than a plain string segment.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from hal0.api.middleware.error_codes import BadRequest, Conflict, Hal0Error, NotFound
from hal0.providers.podman_introspect import LocalImagesDigests, images_digests, is_valid_image_ref
from hal0.registry import runner_pull_jobs as _pull_jobs
from hal0.registry.runner_image import RunnerImage, RunnerImageTag
from hal0.registry.runner_image_sync import sync_runner_images
from hal0.registry.runner_pull import RunnerPullJob

log = logging.getLogger(__name__)

router = APIRouter()


class RunnerImageRmUnavailable(Hal0Error):
    """502 — ``podman_mutate.remove_image``'s write seam couldn't answer.

    ``details["reason"]`` carries the ``SeamUnanswered`` reason (e.g.
    ``"grant-denied"``, ``"podman-absent"``, ``"not-service-user"``). The
    catalogue row is deliberately left untouched on this outcome: an
    unanswered seam call is not the same as "removal failed", and faking a
    delete would desync the catalogue from the real podman store — honesty
    over a fake delete, same discipline as the pull side's fail-soft reads.
    """

    code = "runner_image.rm_unavailable"
    status = 502


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
    """Tag -> badge value (``"validated"``, ``"candidate"``, or stale-pin value)
    from frozen ``hal0.config.schema`` image-ref sets (VULKAN_CAPABLE, PROMPTFORGE
    candidate, STALE_ROCMFPX respectively).

    ``VULKAN_CAPABLE_IMAGE_REFS`` wins ties over the candidate set (a
    validated image should never read as merely "candidate"); the candidate
    set itself is fail-soft: ``DEFAULT_PROMPTFORGE_IMAGE`` landed with
    #2129/#1946, but the import stays guarded so a future rename or removal
    of the constant degrades to an empty candidate set rather than 500ing
    the catalogue routes.
    """
    from hal0.config.schema import STALE_ROCMFPX_IMAGE_REFS, VULKAN_CAPABLE_IMAGE_REFS

    candidates: set[str] = set()
    try:  # fail-soft — enrichment must not hard-depend on this constant
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
            # The stale/retired-pin badge value from STALE_ROCMFPX_IMAGE_REFS
            # (API contract, counted in scripts/scar_baseline.txt).
            out[t.tag] = "deprecated"
    return out


def enrich_row(
    image: RunnerImage,
    *,
    defaults: Mapping[str, tuple[str, str]],
    slot_usage: Mapping[str, str],
    local: LocalImagesDigests | None,
    specialties: Mapping[str, list[str]] = {},
) -> dict[str, Any]:
    """One catalogue row + the derived catalogue-v2/v3 contract fields. Pure.

    ``defaults`` maps runner-family key -> ``(effective image ref, source)``
    where source is ``"override"`` ([slots].default_images) or ``"release"``
    (the baked RUNNER_IMAGES constant); ``slot_usage`` maps slot name -> the
    image ref that slot's rendered unit / resolved config launches.

    ``is_default`` matches on the default ref's REPO against ``image.image``
    (contract: "any tag"), first matching family in ``defaults`` order wins
    (RUNNER_IMAGES insertion order — deterministic). Note ``defaults`` only
    ever carries ONE entry per shared image lineage: ``vulkanfpx`` is folded
    into its canonical ``rocmfpx`` key upstream (runner-image-catalogue v3,
    task 11 — see :func:`_effective_defaults`), so it never appears here to
    race rocmfpx for the match. ``in_use_by`` matches the exact ``image:tag``
    ref.

    ``local`` is one request's ``podman_introspect.images_digests()`` read
    (``None`` when neither store answered) — store-truth (v3): ``store_state``
    ("present"/"missing"/"unknown"), per-tag ``downloaded`` (``None`` when
    the store is unknown), ``store_context``, and ``badges``.

    ``specialties`` maps repo (``_repo_of`` shape) -> the sorted union of
    every :data:`hal0.runners.RUNNER_IMAGES` entry's
    ``supports.specialties`` whose image resolves to that repo (see
    :func:`_repo_specialties`, hoisted once per request beside ``defaults``)
    — the catalogue-v3 field the UI's ``groupRows`` (``runner-images.jsx``)
    reads to route a row into the "Specialized" group. ``[]`` for a repo no
    specialty runner claims — every plain toolbox image included.
    """
    row = _image_to_dict(image)
    row["specialties"] = list(specialties.get(image.image, []))
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


def _repo_specialties() -> dict[str, list[str]]:
    """Repo (``_repo_of`` shape) -> sorted union of every
    :data:`hal0.runners.RUNNER_IMAGES` entry's ``supports.specialties``
    whose ``runner.image`` resolves to that repo.

    Computed ONCE per request (hoisted beside ``defaults``/``slot_usage`` in
    :func:`_request_context`) — this is a pure fold over the in-process
    ``RUNNER_IMAGES`` registry, no I/O, but the union-per-repo shape is worth
    computing once rather than per row. A repo shared by more than one
    runner key (e.g. ``rocmfpx``/``vulkanfpx`` both resolving to
    ``DEFAULT_ROCMFPX_IMAGE``, neither of which carries a specialty today)
    unions cleanly to the same result either way.
    """
    from hal0.runners import RUNNER_IMAGES

    out: dict[str, set[str]] = {}
    for runner in RUNNER_IMAGES.values():
        specialties = runner.supports.specialties
        if not specialties:
            continue
        out.setdefault(_repo_of(runner.image), set()).update(specialties)
    return {repo: sorted(vals) for repo, vals in out.items()}


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

    Family-key canonicalization (runner-image-catalogue v3, task 11):
    ``vulkanfpx`` shares ``DEFAULT_ROCMFPX_IMAGE`` with ``rocmfpx`` — one
    lever per image lineage, so an override written under the alias key
    folds into the canonical family (``canonical_family`` wins over the
    alias when both are set — ``folded.setdefault``), and the alias family
    is skipped entirely from the per-family iteration below so it never
    emits its own defaults entry (or, downstream, a families row).
    """
    import os

    from hal0.runners import RUNNER_IMAGES, canonical_family

    overrides: Mapping[str, str] = {}
    try:
        from hal0.config.loader import load_hal0_config

        raw = load_hal0_config().slots.default_images
        if isinstance(raw, Mapping):
            overrides = raw
    except Exception:
        log.warning("runner_images.default_images_load_failed", exc_info=True)

    # Two passes so the canonical key always wins regardless of dict/toml
    # iteration order (fix round 1: a single ``setdefault`` pass let
    # whichever key happened to iterate first win, not the canonical one —
    # e.g. ``{"vulkanfpx": ..., "rocmfpx": ...}`` would keep the alias
    # value). Pass 1 seeds every canonical key present verbatim; pass 2
    # folds alias keys in with ``setdefault``, so an already-seeded
    # canonical value can never be overwritten by its alias.
    folded: dict[str, str] = {}
    for k, v in overrides.items():
        if canonical_family(k) == k:
            folded[k] = v
    for k, v in overrides.items():
        canon = canonical_family(k)
        if canon != k:
            log.warning("runner_images.default_images_alias_key key=%s canonical=%s", k, canon)
            folded.setdefault(canon, v)
    overrides = folded

    out: dict[str, tuple[str, str]] = {}
    for key, runner in RUNNER_IMAGES.items():
        if canonical_family(key) != key:
            continue  # alias family — folded into its canonical entry above
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
            if "@" in ref:
                # Digest-pinned ref (``repo@sha256:…`` — what
                # ``manifest_image_ref`` returns with real digests, and any
                # digest-form override/env value): the digest IS the ref,
                # not something to look up by tag — ``ref.rpartition(":")``
                # would otherwise split inside the digest and produce hex
                # garbage as a fake "tag".
                eff_digest = ref.rpartition("@")[2]
            else:
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


def _tag_in_use_by(image: RunnerImage, tag: str, slot_usage: Mapping[str, str]) -> list[str]:
    """Slot names whose launched ref guards the ``(image, tag)`` about to be
    deleted — the DELETE tag route's application-level in-use guard.

    Deliberately simpler than ``enrich_row``/``_families_payload``'s
    catalogue-wide digest reconciliation (that's for display; this is a
    safety gate that must stay cheap and easy to reason about). Covers two
    matches only:

      1. Exact ref match — the slot's launched ref is literally
         ``image:tag``.
      2. Same-row digest match — the tag being deleted has a known digest
         (from THIS row's own ``tags``, the ``runner_image_tag`` facts) and
         some slot is pinned to a DIFFERENT tag of the same image repo whose
         digest (again, from this row's own ``tags``) is identical — i.e.
         two tag names that are secretly the same bytes (a re-tag) both
         guard the shared image.

    NOT covered: a slot ref pinned by digest (``repo@sha256:...``) is never
    unpacked here — it can only ever match case 1, and never will (that ref
    shape has no ``:tag`` suffix to equal). Accepted gap: digest-pinned
    slots are rare (manifest-tier defaults only) and
    ``podman_mutate.remove_image``'s seam-level rc-67 "in-use" outcome is
    the real backstop that stops bytes a running container actually holds
    from being removed regardless of what this application-level guard
    missed.
    """
    ref = f"{image.image}:{tag}"
    tag_digest = next((t.digest for t in image.tags if t.tag == tag), None)
    hits: set[str] = set()
    prefix = f"{image.image}:"
    for name, usage_ref in slot_usage.items():
        if usage_ref == ref:
            hits.add(name)
        elif tag_digest and usage_ref.startswith(prefix):
            other_digest = next(
                (t.digest for t in image.tags if t.tag == usage_ref[len(prefix) :]), None
            )
            if other_digest and other_digest == tag_digest:
                hits.add(name)
    return sorted(hits)


async def _restart_slot(name: str, request: Request) -> None:
    """Restart one slot via the exact same service call the per-slot
    ``POST /api/slots/{name}/restart`` button makes today.

    Delegates to ``hal0.api.routes.slots._get_slot_manager(request).restart``
    — no new mechanism, no subprocess. Also mirrors that route's audit
    trail (``record_action``, with ``rec.after`` set from the same
    ``_state_value(snap)`` slots.py's own restart route records) and cache
    invalidation, so a batch restart from this page is indistinguishable,
    downstream, from clicking each slot's own button. The cache clear sits
    in a ``finally`` — same semantics as slots.py's ``@_invalidates_snapshot``
    decorator — so a raising ``sm.restart()`` still drops the stale
    ``/api/slots`` snapshot instead of leaving it to serve pre-restart state
    for the rest of its TTL. Kept as a thin module-level shim so tests can
    monkeypatch it directly.
    """
    from hal0.api._audit import record_action
    from hal0.api.routes.slots import _get_slot_manager, _state_value

    sm = _get_slot_manager(request)
    try:
        async with record_action(
            request, category="slot", action="slot.restart", target=name
        ) as rec:
            snap = await sm.restart(name)
            rec.after = {"state": _state_value(snap)}
    finally:
        request.app.state._slots_snapshot_cache = None


def _request_context() -> tuple[
    dict[str, tuple[str, str]], dict[str, str], LocalImagesDigests | None, dict[str, list[str]]
]:
    """One ``defaults``/``slot_usage``/``local``/``specialties`` quadruple,
    shared by every row AND the families payload for a single request —
    computed once each (``_local_store`` is explicitly "one store read per
    request"; ``_repo_specialties`` is a pure in-process fold, hoisted here
    for the same one-per-request discipline)."""
    defaults = _effective_defaults()
    slot_usage = _slot_image_usage()
    local = _local_store()
    specialties = _repo_specialties()
    return defaults, slot_usage, local, specialties


def _enrich_with(
    images: list[RunnerImage],
    defaults: Mapping[str, tuple[str, str]],
    slot_usage: Mapping[str, str],
    local: LocalImagesDigests | None,
    specialties: Mapping[str, list[str]] = {},
) -> list[dict[str, Any]]:
    """Enrich every row against one already-computed request context.

    The shared tail of ``_enriched``, ``list_runner_images``, and
    ``sync_runner_images_route`` — each of the latter two already has its
    own ``defaults``/``slot_usage``/``local``/``specialties`` on hand (to
    also feed ``_safe_families_payload``), so this factors out the
    row-enrichment list comprehension instead of repeating it three ways.
    """
    return [
        enrich_row(
            i, defaults=defaults, slot_usage=slot_usage, local=local, specialties=specialties
        )
        for i in images
    ]


def _enriched(images: list[RunnerImage]) -> list[dict[str, Any]]:
    defaults, slot_usage, local, specialties = _request_context()
    return _enrich_with(images, defaults, slot_usage, local, specialties)


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
    defaults, slot_usage, local, specialties = _request_context()
    rows = _enrich_with(images, defaults, slot_usage, local, specialties)
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
    defaults, slot_usage, local, specialties = _request_context()
    rows = _enrich_with(result.images, defaults, slot_usage, local, specialties)
    return {
        "images": rows,
        "families": _safe_families_payload(result.images, defaults, slot_usage, local),
        "images_json_ok": result.images_json_ok,
        "images_json_error": result.images_json_error,
        "probe_errors": result.probe_errors,
    }


@router.post("/restart-affected", status_code=202)
async def restart_affected_slots(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Restart every slot whose launched image ref equals ``body['ref']``.

    The page-side #2096 workaround: after rolling a family default, the
    operator restarts the drifted slots in one click instead of visiting
    each slot. Uses the exact same restart path as the per-slot button
    (:func:`_restart_slot`). Registered ahead of the ``/{image_id:path}``
    catch-alls below — same reasoning as ``/pulls/list``: a literal
    ``restart-affected`` segment would otherwise be swallowed as an
    ``image_id`` by the greedy ``:path`` converter if declared after it.

    Fail-soft per slot: a restart failure is logged and the slot is
    skipped from ``restarted`` rather than 500ing the whole batch — one
    stuck slot shouldn't block the rest from rolling.
    """
    ref = body.get("ref")
    if not isinstance(ref, str) or not is_valid_image_ref(ref):
        raise BadRequest("invalid image ref", code="runner_image.ref_invalid", details={"ref": ref})
    names = sorted(n for n, r in _slot_image_usage().items() if r == ref)
    restarted: list[str] = []
    for name in names:
        try:
            await _restart_slot(name, request)
            restarted.append(name)
        except Exception:
            log.warning("runner_images.restart_failed slot=%s", name, exc_info=True)
    return {"restarted": restarted}


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


@router.delete("/{image_id:path}/tags/{tag}")
async def delete_runner_image_tag(image_id: str, tag: str, request: Request) -> dict[str, Any]:
    """Delete one catalogued tag and reclaim its bytes from the local store.

    Grouped here with the other explicit-suffix routes (ahead of the
    ``/{image_id:path}`` GET catch-all below) for readability, matching the
    file's convention — but unlike the GET suffix routes, ordering here
    isn't load-bearing: Starlette matches on (path, METHOD) together, so a
    DELETE request never falls into a GET-only route no matter where this
    is declared. ``test_delete_tag_route_ordering`` in
    ``tests/api/test_runner_images_routes.py`` pins that both directions
    (GET-single-image, DELETE-tag) resolve to the right handler.

    Guards, in order:
      1. 404 ``runner_image.not_found`` — unknown id.
      2. 404 ``runner_image.tag_not_found`` — id known, but ``tag`` is
         neither the headline tag, a ``runner_image_tag`` fact, nor an
         ``available_tags`` entry.
      3. 409 ``runner_image.pull_in_progress`` — a pull job for this id is
         ``queued``/``running`` and targets this tag (or has no tag at
         all — an older untagged job record, which always pulls the
         headline). Fix round 1 (#2106): a pull racing this DELETE could
         otherwise complete AFTER ``store.remove_tag`` and re-stamp
         ``local_path``/``downloaded_at`` via ``set_local_state``, leaving
         the row ``downloaded=True`` with zero tag rows. Mirrors
         ``RunnerPullConflict``'s discipline (``runner_pull.py``): an
         in-flight mutation on the same id wins, the caller is told to
         cancel first rather than racing silently.
      4. 409 ``runner_image.tag_in_use`` — application-level guard naming
         the slot(s) responsible (see :func:`_tag_in_use_by`).
      5. ``podman_mutate.remove_image`` — the seam-level guard, a second
         independent barrier: outcome ``"in-use"`` (podman rc 67) also 409s
         the same code but names no slots (the app-level guard above didn't
         catch it, e.g. an unmanaged container holding the image);
         ``"unknown"`` (seam absent/denied/erroring) is 502
         ``runner_image.rm_unavailable`` with the seam's reason, catalogue
         left untouched (no fake delete); ``"removed"``/``"missing"``
         update the catalogue via ``store.remove_tag`` and respond 200.

    Audited the same way ``restart-affected`` is: ``record_action`` with
    category ``"runner_image"``, action ``"runner_image.rm"``, target the
    ``image:tag`` ref — a raised 409/502 inside the block is recorded
    ``outcome="error"`` and re-raised (``hal0.activity.audit_action``'s
    confirmation guarantee), so the audit trail is honest either way.
    """
    store = request.app.state.runner_image_registry
    image = store.get(image_id)
    if image is None:
        raise NotFound(
            f"runner image {image_id!r} not in catalogue",
            details={"image_id": image_id},
            code="runner_image.not_found",
        )
    tag_known = (
        tag == image.tag or any(t.tag == tag for t in image.tags) or tag in image.available_tags
    )
    if not tag_known:
        raise NotFound(
            f"tag {tag!r} not catalogued for {image_id!r}",
            details={"image_id": image_id, "tag": tag},
            code="runner_image.tag_not_found",
        )

    jobs: dict[str, RunnerPullJob] = request.app.state.runner_image_pull_jobs
    job = jobs.get(image_id)
    if job is not None and job.state in ("queued", "running") and job.tag in (tag, None):
        raise Conflict(
            f"a pull for {image_id!r} tag {tag!r} is in progress; cancel it first",
            details={"image_id": image_id, "tag": tag},
            code="runner_image.pull_in_progress",
        )

    ref = f"{image.image}:{tag}"
    in_use_slots = _tag_in_use_by(image, tag, _slot_image_usage())
    if in_use_slots:
        raise Conflict(
            f"runner image tag {ref!r} is in use",
            details={"image_id": image_id, "tag": tag, "slots": in_use_slots},
            code="runner_image.tag_in_use",
        )

    from hal0.api._audit import record_action
    from hal0.providers import podman_mutate

    async with record_action(
        request, category="runner_image", action="runner_image.rm", target=ref
    ) as rec:
        outcome, reason = await asyncio.to_thread(podman_mutate.remove_image, ref)
        rec.after = {"outcome": outcome}
        if outcome == "in-use":
            raise Conflict(
                f"runner image tag {ref!r} is in use by a container or has child images",
                details={"image_id": image_id, "tag": tag},
                code="runner_image.tag_in_use",
            )
        if outcome == "unknown":
            raise RunnerImageRmUnavailable(
                f"image removal seam unavailable ({reason})",
                details={"image_id": image_id, "tag": tag, "reason": reason},
            )
        # "removed" or "missing": the seam agrees the bytes are gone (or
        # already were) — update the catalogue to match.
        old_local_path = image.local_path
        catalogue_removed = store.remove_tag(image_id, tag)
        rec.after = {"outcome": outcome, "catalogue_removed": catalogue_removed}
        if not catalogue_removed:
            # The seam agrees the bytes are gone, but the catalogue had no
            # matching tag row to delete — a desync worth a log line (the
            # 200 response is still honest: the bytes really are gone/
            # already-absent, this just flags the catalogue disagreeing).
            log.warning(
                "runner_image.rm_catalogue_desync image_id=%s tag=%s outcome=%s",
                image_id,
                tag,
                outcome,
            )
        if old_local_path:
            updated = store.get(image_id)
            if updated is None or updated.local_path is None:
                # remove_tag cleared the marker (or the row is gone
                # entirely) — unlink the now-orphaned marker file.
                # Fail-soft: a filesystem hiccup here must not turn an
                # already-successful removal into a 500.
                with contextlib.suppress(OSError):
                    Path(old_local_path).unlink(missing_ok=True)

    return {"removed": outcome == "removed", "outcome": outcome}


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
