"""Model registry endpoints (mounted under /api/models).

This is the *internal* models surface for the dashboard — distinct from
OpenAI-compat `/v1/models`.  Aggregates entries from every configured
upstream so the dashboard's Models view shows what's actually reachable,
plus any locally-registered models from the ModelRegistry.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from hal0.api._audit import record_action
from hal0.api.middleware.error_codes import BadRequest, NotFound
from hal0.config.loader import load_hal0_config
from hal0.registry.curated import CURATED, CuratedModel, HaloaiModel, get_curated
from hal0.registry.pull import (
    PullInvalidSource,
    PullJob,
    PullJobNotFound,
    list_persisted_jobs,
    make_job,
    run_flm_pull,
    run_pull,
)
from hal0.registry.pull import persist_pull_job as _persist_pull_job
from hal0.registry.pull import pull_job_file as _pull_job_file
from hal0.registry.update_check import evaluate_model_update, fetch_remote_lfs_shas
from hal0.services import models_service as _svc
from hal0.upstreams.filters import apply_filters
from hal0.upstreams.huggingface import fetch_repo as _fetch_hf_repo
from hal0.upstreams.huggingface import normalise_repo_slug as _normalise_hf_repo

# See slots.py for the writer-gate rationale.

router = APIRouter()

log = logging.getLogger(__name__)


# ── durable pull-job store (#626 / #MR-1) ─────────────────────────────────────
#
# The snapshot writer now lives in registry.pull (imported above as
# ``_persist_pull_job``/``_pull_job_file``) so ``run_pull`` persists terminal
# state for EVERY caller — the dashboard route AND the installer/bundle-tier
# pulls that call run_pull directly. The disk-fallback read path below still
# consumes those snapshots.


def _load_persisted_pull_job(model_id: str, registry: Any | None = None) -> dict[str, Any] | None:
    """Read a persisted pull-job snapshot from disk, or None if absent/unreadable.

    ``registry`` (when supplied) is forwarded to the reconcile step so a stale
    non-terminal snapshot can be cross-checked against ground truth — an
    install that actually landed is reported ``completed``, not ``failed``.
    """
    path = _pull_job_file(model_id)
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
    return _reconcile_persisted_pull_job(loaded, registry)


def _reconcile_persisted_pull_job(
    persisted: dict[str, Any], registry: Any | None = None
) -> dict[str, Any]:
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


# ── service-layer bindings (P3-routers §J) ───────────────────────────────────
#
# The registry classification, row-serialisation, scan-commit and delete-cascade
# logic moved to ``hal0.services.models_service`` so the route handlers are
# request→service→envelope shells. These module-level names keep the handler
# bodies below reading as thin call sites (and preserve the public
# ``_comfyui_category`` import the test-suite depends on). New callers should
# import from ``hal0.services.models_service`` directly.
_ALIAS_NAMES = _svc.ALIAS_NAMES
_FLM_DISPATCH_TYPE = _svc.FLM_DISPATCH_TYPE
_MODALITY_TO_SLOT_TYPE = _svc.MODALITY_TO_SLOT_TYPE
_is_alias = _svc.is_alias
_dispatch_type = _svc.dispatch_type
_comfyui_category = _svc.comfyui_category
_model_to_dict = _svc.model_to_dict
_lazy_quant = _svc.lazy_quant
_pull_entry = _svc.pull_entry
_speed_for_entry = _svc.speed_for_entry
_eta_for_entry = _svc.eta_for_entry
_hf_repo_for_model = _svc.hf_repo_for_model
_suggest_id_from_path = _svc.suggest_id_from_path


@router.get("")
async def list_models(request: Request) -> dict[str, Any]:
    """Aggregate models from the local registry + every upstream.

    Local registry entries (a real file on disk) win on id collision —
    the upstream might still advertise the id, but the user has the
    bytes locally and that's the truth. Each row carries ``installed``
    so the UI can render an installed/advertised badge.
    """
    registry = request.app.state.model_registry
    upstreams = request.app.state.upstreams
    cache = getattr(request.app.state, "model_cache", {})
    now = int(time.time())
    data: list[dict[str, Any]] = []
    seen: set[str] = set()
    filtered = 0
    # Last HF update-check snapshot (populated by /api/models/updates/check;
    # never fetched on this hot path). The flag is recomputed against the
    # row's CURRENT metadata.sha256 rather than replayed from the snapshot,
    # so applying an update clears the badge on the next poll without
    # waiting for the check TTL to expire.
    update_checks: dict[str, Any] = {}
    _upd_state = getattr(request.app.state, "model_update_state", None)
    if isinstance(_upd_state, dict):
        update_checks = _upd_state.get("models") or {}
    for entry in registry.list():
        dumped = _model_to_dict(entry)
        dumped["installed"] = True
        chk = update_checks.get(entry.id)
        if isinstance(chk, dict):
            remote_sha = chk.get("remote_sha256")
            local_sha = (entry.metadata or {}).get("sha256")
            dumped["update_available"] = bool(
                isinstance(remote_sha, str)
                and remote_sha
                and isinstance(local_sha, str)
                and local_sha
                and remote_sha != local_sha.lower()
            )
        dumped.setdefault("object", "model")
        dumped.setdefault("created", now)
        dumped.setdefault("owned_by", "local")
        dumped["type"] = _dispatch_type(
            dumped.get("id", ""), capabilities=dumped.get("capabilities")
        )
        # ComfyUI discriminator + category for the dashboard's dedicated
        # image-gen surface. Path-derived so it self-heals rows an older pull
        # mis-tagged (capabilities=["chat"], backends=[]) with no migration:
        # any model whose bytes live under the ComfyUI models tree — or that
        # already carries the comfyui backend — is owned_by "comfyui" and
        # advertises its subdir as ``comfyui_category``. The UI groups the
        # image-gen surface on this, not on the (possibly stale) capability.
        _cat = _comfyui_category(dumped.get("path"))
        _bes = list(dumped.get("backends") or [])
        if _cat is not None or "comfyui" in _bes:
            dumped["owned_by"] = "comfyui"
            if "comfyui" not in _bes:
                _bes.append("comfyui")
                dumped["backends"] = _bes
            if _cat is not None:
                dumped["comfyui_category"] = _cat
        data.append(dumped)
        seen.add(entry.id)
    # "Don't surface invisible models": the composite ``hal0``/npu upstream
    # advertises FLM slot-default tags via /v1/models even when the weights are
    # not on disk, so they used to leak into the catalog as available-but-
    # uninstalled rows. The dedicated FLM probe below is the authoritative
    # source (it re-adds the INSTALLED ones with the right npu shape), so drop
    # every FLM-servable tag from the generic upstream advertisement. The probe
    # is module-cached, so the second call in the injector below is O(1).
    flm_skip: set[str] = set()
    try:
        from hal0.providers.flm import flm_served_models as _flm_probe

        for _fm in _flm_probe():
            _tag = _fm.get("tag")
            if isinstance(_tag, str) and _tag:
                flm_skip.add(_tag)
                flm_skip.add(_tag.replace(":", "-") + "-FLM")
    except Exception:
        # Probe unavailable (no flm binary / dev host) — nothing to skip.
        pass
    for u in upstreams.list():
        # Slot-backed entries serve LOCAL models: the composite ``hal0``
        # aggregate (kind="slot") and container slots (kind="remote" with
        # slot_name) advertise ids that live on this host's disk — labeling
        # them origin="upstream" put local slot models in the Models page
        # Upstream tab whenever the advertised id differed from the registry
        # id (raw GGUF casing vs normalized alias). Only genuine remotes
        # contribute upstream rows here.
        if u.kind != "remote" or u.slot_name:
            continue
        if not getattr(u, "enabled", True) or not getattr(u, "advertise_models", True):
            continue
        try:
            ids = cache.get(u.name) or await upstreams.fetch_models(u.name)
            cache[u.name] = ids
        except Exception:
            ids = []
        # Same operator curation as /v1/models — the Models page is a
        # discovery surface, so per-upstream filters apply here too
        # (dispatch stays unfiltered; hidden models remain addressable).
        ids = apply_filters(ids, getattr(u, "model_filters", None))
        for mid in ids:
            if mid in seen:
                continue
            if _is_alias(mid):
                filtered += 1
                continue
            # FLM-servable tag advertised before its weights are pulled — the
            # dedicated probe below re-adds the installed ones. Skip here so an
            # un-pulled FLM model never shows as an available upstream row.
            if mid in flm_skip:
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
                    "installed": False,
                    # Explicit origin (WS-13): advertised by a remote
                    # provider's /v1/models, never on this host's disk.
                    # Clients should prefer this over inferring from
                    # installed+upstream.
                    "origin": "upstream",
                    # Upstream-only rows have no local path → "pulled"
                    # by the path-shape rule (issue #220). The
                    # blessed bucket is reserved for files actually
                    # laid out under the blessed recipe tree.
                    "ns": "pulled",
                    # Upstream rows carry no capabilities; classify from
                    # the id so W7 still counts embed/rerank/voice/img.
                    "type": _dispatch_type(mid),
                }
            )
    # Installed FLM/NPU models — surfaced straight from the host-flm probe so
    # the NPU slot pickers can select any model on disk, not just the one a slot
    # already defaults to (the composite ``hal0`` upstream advertises only slot
    # defaults, so without this only the configured npu model appeared). The id
    # uses the ``<tag>-FLM`` convention so the dashboard maps it to the
    # npu device; ``capabilities`` + an explicit ``device`` let the slot-swap
    # popover derive type/device without requiring a registry entry.
    try:
        from hal0.providers.flm import flm_served_models

        for fm in flm_served_models():
            if not fm.get("installed"):
                continue
            mid = fm["tag"].replace(":", "-") + "-FLM"
            if mid in seen:
                continue
            seen.add(mid)
            caps = list(fm.get("capabilities") or [])
            # FLM chat tags are chat-first even when multimodal (gemma4 also
            # advertises ``stt``); pick chat as the primary role so they land
            # in the NPU chat picker, not under stt.
            primary = "chat" if "chat" in caps else (caps[0] if caps else "chat")
            # The NPU slot pickers (ui/dash/slots.jsx) gate on the FLM-seed
            # shape: ``isFlmModel`` needs backend=="flm" / upstream=="npu", and
            # ``modelSlotType`` needs the DISPATCHER vocabulary (chat→llm,
            # embed→embedding, stt→transcription) — not the W7 type vocab. Match
            # that shape exactly so probe-sourced models are selectable.
            data.append(
                {
                    "id": mid,
                    "name": mid,
                    "object": "model",
                    "created": now,
                    "owned_by": "flm",
                    "upstream": "npu",
                    "backend": "flm",
                    "installed": True,
                    # FLM rows carry upstream="npu" for the slot pickers but
                    # are installed host-side — explicitly local (WS-13).
                    "origin": "local",
                    "ns": "pulled",
                    "type": _FLM_DISPATCH_TYPE.get(primary, "llm"),
                    "capability": primary,
                    "capabilities": caps,
                    "device": "npu",
                }
            )
    except Exception:
        # Probe unavailable (no flm binary / dev host) — skip silently; the
        # rest of the catalog still renders.
        pass
    return {"models": data, "count": len(data), "filtered_aliases": filtered}


@router.get("/catalogue")
async def list_catalogue() -> dict[str, Any]:
    """Curated catalogue split into pullable (HF) and upstream-routed entries."""
    pullable: list[dict[str, Any]] = []
    upstream: list[dict[str, Any]] = []
    for entry in CURATED:
        if isinstance(entry, CuratedModel):
            # Only HF-coordinate entries are pullable. FLM/NPU-served curated
            # entries (empty hf_repo, recommended_slot="flm") are neither
            # HF-pullable nor haloai-upstream — the FLM slot serves them
            # locally — so they don't appear in this pull/upstream split.
            if entry.hf_repo:
                pullable.append(entry.model_dump(mode="json"))
        elif isinstance(entry, HaloaiModel):
            upstream.append(entry.model_dump(mode="json"))
    return {
        "pullable": pullable,
        "upstream": upstream,
        "counts": {
            "pullable": len(pullable),
            "upstream": len(upstream),
            "total": len(pullable) + len(upstream),
        },
    }


@router.post("/scan/preview")
async def scan_preview(request: Request) -> dict[str, Any]:
    """Walk the requested paths and return :class:`DetectionResult` rows.

    Inspection-only: no registry mutation, no event emission. The UI uses
    this to populate the "Scan directory" preview table where the user
    edits backends + capabilities + id before committing via POST /scan.

    Body::

        {
          "paths":     ["/abs/dir/or/file", ...],   # required
          "recursive": bool                          # default True
        }

    Files matching the configured ``[models].file_extensions`` are
    selected when walking directories. A path that is a file is detected
    directly regardless of extension.

    ``recursive`` defaults to True because the operator-facing flow
    (dashboard "Scan directory", CLI) almost always points at a model
    store root (e.g. ``/mnt/ai-models``) whose .gguf files live under
    per-repo subdirs; a flat ``iterdir()`` returns zero rows there and
    looks broken. Callers that want a shallow walk pass ``recursive:
    false`` explicitly.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")

    raw_paths = body.get("paths") or []
    if not isinstance(raw_paths, list) or not raw_paths:
        raise BadRequest("'paths' must be a non-empty list of absolute paths")
    recursive = bool(body.get("recursive", True))

    cfg = load_hal0_config()
    extensions = {e.lower() for e in cfg.models.file_extensions}

    preview = _svc.preview_scan_rows(raw_paths, recursive, extensions)
    return {"preview": preview, "count": len(preview)}


@router.post("/scan")
async def scan_models(request: Request) -> dict[str, Any]:
    """Walk model roots and register new files — legacy auto-scan, or
    commit a user-edited preview when ``rows`` is supplied.

    Two body shapes:

    * **Legacy / empty body** — walk the configured ``[models].roots`` and
      auto-register every new candidate via the discover module. Each
      added model fires a ``model.registered`` event with
      ``source='scan'``.

    * **``{"rows": [...]}``** — commit pre-vetted preview rows. Each row
      may carry user-edited ``backends`` / ``capabilities`` / ``defaults``
      / ``name`` / ``id`` overrides; otherwise we fall back to the
      detection output for that path. User overrides always win — that's
      the whole point of the preview round-trip.

    Returns ``{added, skipped, scanned_roots}`` in both modes so the UI's
    toast-render path is unchanged.
    """
    registry = request.app.state.model_registry
    event_bus = getattr(request.app.state, "events", None)

    body: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        body = await request.json()
    rows = body.get("rows") if isinstance(body, dict) else None

    if isinstance(rows, list) and rows:
        return await _svc.commit_scan_rows(rows, registry, event_bus)

    cfg = load_hal0_config()
    return await _svc.auto_scan_and_register(registry, cfg.models, event_bus)


@router.post("/add-from-path", status_code=201)
async def add_model_from_path(request: Request) -> dict[str, Any]:
    """Register a single already-downloaded model file by absolute path.

    Convenience wrapper around ``detect()`` + ``ModelRegistry.add()``
    aimed at the dashboard's "Add by path" flow — the operator points at
    one file, we read its header (or fall back to filename heuristic),
    derive id + capabilities + backends, then write the entry. No
    network, no copy — the file stays where it lives.

    Body::

        {
          "path":      "/abs/path/to/model.gguf",  # required
          "id":        "optional explicit registry id",
          "name":      "optional display name",
          "labels":    ["llm", "chat", ...],       # optional capabilities override
          "overwrite": false                       # default false
        }

    Errors:
      * ``400 validation.invalid`` — body shape wrong.
      * ``400 model.path_missing`` — file does not exist or is not readable.
      * ``400 model.unsupported_format`` — extension not in the registry's
        ``[models].file_extensions`` allow-list.
      * ``409 model.already_exists`` — id already registered and
        ``overwrite=false``.

    The file must be readable by the hal0-api process; we do **not**
    `chown` or copy.  When the file lives under a scan root pinned in
    ``[models].roots`` we trust the operator owns the path; when it's
    elsewhere we still allow it (the operator can point anywhere they
    have read access to).
    """
    from hal0.registry.detect import detect
    from hal0.registry.discover import _normalise_id
    from hal0.registry.model import Model
    from hal0.registry.store import ModelAlreadyExists

    registry = request.app.state.model_registry
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")

    raw_path = body.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise BadRequest("'path' must be a non-empty absolute path string")

    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise BadRequest(
            f"'path' must be absolute (got {raw_path!r})",
            code="model.path_relative",
        )
    if not path.exists() or not path.is_file():
        raise BadRequest(
            f"path {str(path)!r} is not a readable file",
            code="model.path_missing",
            details={"path": str(path)},
        )
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path

    # Enforce the same extension allow-list the scan walker uses so
    # accidentally pointing at a tokenizer.json or a README.md fails
    # loudly rather than landing in the registry.
    cfg = load_hal0_config()
    allowed_exts = {e.lower() for e in cfg.models.file_extensions}
    if resolved.suffix.lower() not in allowed_exts:
        raise BadRequest(
            f"file extension {resolved.suffix!r} not in [models].file_extensions",
            code="model.unsupported_format",
            details={"path": str(resolved), "allowed": sorted(allowed_exts)},
        )

    detection = detect(resolved)
    raw_labels = body.get("labels")
    if isinstance(raw_labels, list) and raw_labels:
        capabilities = [str(c) for c in raw_labels if isinstance(c, str) and c.strip()]
    else:
        capabilities = list(detection.suggested_capabilities) or ["chat"]

    raw_id = body.get("id")
    if isinstance(raw_id, str) and raw_id.strip():
        model_id = raw_id.strip()
    else:
        # Prefer the detector's suggested_name (post-GGUF arch+param sniff)
        # falling back to the slug of the stem so two paths to the same
        # file land on the same id as the auto-scan would.
        model_id = _normalise_id(detection.suggested_name or resolved.stem)

    raw_name = body.get("name")
    if isinstance(raw_name, str) and raw_name.strip():
        display_name = raw_name.strip()
    else:
        display_name = detection.suggested_name or resolved.stem

    overwrite = bool(body.get("overwrite", False))

    try:
        size_bytes = resolved.stat().st_size
    except OSError:
        size_bytes = 0

    metadata: dict[str, Any] = {"discovered": True, "source": "add-from-path"}
    if detection.context_length is not None:
        metadata["context_length"] = detection.context_length

    try:
        model = Model(
            id=model_id,
            name=display_name,
            path=str(resolved),
            size_bytes=size_bytes,
            quant=detection.quant,
            capabilities=capabilities,
            backends=list(detection.suggested_backends),
            metadata=metadata,
        )
    except (TypeError, ValueError) as exc:
        raise BadRequest(f"invalid Model payload: {exc}") from exc

    if overwrite and registry.has(model_id):
        registry.remove(model_id)

    try:
        registry.add(model)
    except ModelAlreadyExists as exc:
        # Convert to the structured envelope shape (409) so the UI can
        # branch on the code rather than the message text.
        raise exc

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None:
        await event_bus.emit(
            "model.registered",
            "info",
            f"model:{model.id}",
            f"{model.id}: registered (add-from-path)",
            data={
                "id": model.id,
                "backends": list(model.backends),
                "capabilities": list(model.capabilities),
                "source": "add-from-path",
            },
        )
    return _model_to_dict(model)


@router.post("", status_code=201)
async def create_model(request: Request) -> dict[str, Any]:
    """Register a new model in the local ModelRegistry.

    Body shape: serialized ``Model`` — see ``hal0.registry.store.Model``.
    The model must already exist on disk (e.g. dropped into
    ``/var/lib/hal0/models/``) — this endpoint records metadata, it does
    not download. Use POST /api/models/{id}/pull for downloads.

    Optional ``source`` (top-level, not part of ``Model``) tags the
    emitted ``model.registered`` event so the footer can colour-code
    catalogue picks vs hand-registered files. Defaults to ``"manual"``.
    """
    from hal0.registry.store import Model

    registry = request.app.state.model_registry
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    # Pop ``source`` before validation — it's an event-only tag, not a
    # Model field. Default to "manual" for hand-registered single files.
    source = body.pop("source", "manual")
    try:
        model = Model(**body)
    except (TypeError, ValueError) as exc:
        raise BadRequest(f"invalid Model payload: {exc}") from exc
    registry.add(model)

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None:
        await event_bus.emit(
            "model.registered",
            "info",
            f"model:{model.id}",
            f"{model.id}: registered ({source})",
            data={
                "id": model.id,
                "backends": list(model.backends),
                "capabilities": list(model.capabilities),
                "source": str(source),
            },
        )
    return _model_to_dict(model)


@router.get("/pulls")
async def list_pulls(request: Request) -> list[dict[str, Any]]:
    """Return all pull jobs (active in-memory + persisted terminal from disk).

    Dedup: in-memory jobs win over persisted snapshots for the same model_id.
    """
    jobs: dict[str, PullJob] = request.app.state.model_pull_jobs
    registry = request.app.state.model_registry

    # Start with persisted TERMINAL jobs from disk.
    # Non-terminal snapshots (e.g. "running" from a crash) are skipped
    # — there is no matching in-memory job to reconcile them.
    persisted = list_persisted_jobs()
    by_model: dict[str, dict[str, Any]] = {}
    for p in persisted:
        state = p.get("state")
        if state not in ("completed", "failed", "cancelled"):
            continue
        mid = p.get("model_id")
        if isinstance(mid, str) and mid:
            by_model[mid] = p

    # Overlay in-memory jobs (they win — reflect live state)
    for mid, job in jobs.items():
        by_model[mid] = job.as_dict()

    # Build response entries enriched with registry data
    result: list[dict[str, Any]] = []
    for mid, data in by_model.items():
        entry = _pull_entry(data, mid, registry)
        result.append(entry)

    # Sort: active first, then by started_at descending
    result.sort(
        key=lambda e: (
            0 if e.get("state") in ("queued", "running") else 1,
            -(e.get("started_at") or 0),
        )
    )
    return result


# ── HF update check (models with newer bytes on the Hub) ─────────────────────
#
# NOTE: registered BEFORE the ``/{model_id}`` catch-all below — FastAPI
# matches in definition order, same reason ``/pulls`` sits above it.

_UPDATE_CHECK_TTL_S = 3600.0


@router.get("/updates/check")
async def check_model_updates(request: Request, refresh: bool = False) -> dict[str, Any]:
    """Probe HuggingFace for newer versions of installed HF-pulled models.

    Compares each registry row's recorded ``metadata.sha256`` against the
    repo's current LFS sha256 for the same ``hf_repo``/``hf_filename``
    (one tree fetch per unique repo). The result snapshot is cached on
    app state for an hour — ``?refresh=1`` forces a re-probe — and
    :func:`list_models` merges a per-row ``update_available`` flag from
    it so the catalog poll never touches huggingface.co.

    Fail-soft: an unreachable repo marks its models ``reason=
    "repo_unreachable"`` rather than failing the whole check; the route
    never 500s on upstream trouble.
    """
    state = request.app.state
    lock = getattr(state, "model_update_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        state.model_update_lock = lock
    async with lock:
        cached = getattr(state, "model_update_state", None)
        now = time.time()
        if (
            not refresh
            and isinstance(cached, dict)
            and now - cached.get("checked_at", 0) < _UPDATE_CHECK_TTL_S
        ):
            return cached

        registry = state.model_registry
        entries = [
            m
            for m in registry.list()
            if (m.hf_repo or "").strip() and (m.hf_filename or "").strip()
        ]
        hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        repo_files = await fetch_remote_lfs_shas(
            {m.hf_repo.strip() for m in entries}, hf_token=hf_token
        )
        checks: dict[str, dict[str, Any]] = {}
        for m in entries:
            verdict = evaluate_model_update(m, repo_files)
            if verdict is not None:
                checks[m.id] = verdict
        payload: dict[str, Any] = {
            "checked_at": now,
            "checked": len(checks),
            "updates_available": sum(1 for v in checks.values() if v["update_available"]),
            "models": checks,
        }
        state.model_update_state = payload
        return payload


@router.post("/{model_id}/update", status_code=202)
async def update_model_from_hf(
    model_id: str,
    request: Request,
) -> dict[str, object]:
    """Re-pull a model's HF file over its installed bytes (in place).

    The pull streams to staging, verifies HF's advertised sha256, then
    atomically replaces the file at the registry row's EXISTING ``path``
    (``dest_override``) — deliberately not re-deriving the destination,
    which would relocate pre-capability-layout models and orphan their
    old bytes. Job tracking, SSE progress, and the downloads pane all
    reuse the standard pull machinery under the same ``model_id`` key.

    Idempotent-ish like ``pull_model``: an in-flight job for the id is
    returned rather than duplicated.
    """
    registry = request.app.state.model_registry
    try:
        existing = registry.get(model_id)
    except Exception:
        raise NotFound(f"model {model_id!r} not found", code="model.not_found") from None
    hf_repo = (existing.hf_repo or "").strip()
    hf_file = (existing.hf_filename or "").strip()
    if not hf_repo or not hf_file:
        raise PullInvalidSource(
            f"model {model_id!r} has no hugging face source — set hf_repo + hf_filename",
            details={"model_id": model_id},
        )
    dest = (existing.path or "").strip()
    if not dest or Path(dest).is_dir():
        raise PullInvalidSource(
            f"model {model_id!r} has no single-file install path to update in place",
            details={"model_id": model_id, "path": dest},
        )

    jobs: dict[str, PullJob] = request.app.state.model_pull_jobs
    in_flight = jobs.get(model_id)
    if in_flight is not None and in_flight.state in ("queued", "running"):
        return {
            "id": in_flight.job_id,
            "model_id": model_id,
            "state": in_flight.state,
            "resumed": True,
        }

    job = make_job(model_id)
    jobs[model_id] = job
    _persist_pull_job(job)

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None:
        await event_bus.emit(
            "pull.queued",
            "info",
            f"pull:{model_id}",
            f"{model_id}: update queued ({hf_repo}/{hf_file})",
            data={"model_id": model_id, "hf_repo": hf_repo, "hf_file": hf_file, "update": True},
        )

    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    _schedule_pull_task(
        request.app.state,
        model_id,
        _run_pull_with_events(
            job,
            hf_repo=hf_repo,
            hf_file=hf_file,
            registry=registry,
            hf_token=hf_token,
            event_bus=event_bus,
            dest_override=dest,
        ),
    )
    return {
        "id": job.job_id,
        "model_id": model_id,
        "state": job.state,
        "hf_repo": hf_repo,
        "hf_file": hf_file,
        "dest_path": dest,
        "update": True,
    }


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
    raise NotFound(
        f"model {model_id!r} not found in registry or any upstream catalog",
        details={"model_id": model_id},
        code="model.not_found",
    )


@router.put("/{model_id}")
async def update_model(model_id: str, request: Request) -> dict[str, Any]:
    """Apply partial updates to a registered model's metadata.

    Body accepts any subset of: ``name``, ``capabilities``, ``backends``,
    ``defaults`` (nested ``ModelDefaults``), plus the legacy fields
    (``license``, ``tags``, ``metadata`` …). Emits ``model.updated`` with
    ``changed_fields`` so the footer ticker can render a "you edited X"
    chip.
    """
    registry = request.app.state.model_registry
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")

    # Snapshot the pre-update model so we can diff the field set the
    # client actually changed (vs the wire-format keys, which may include
    # unchanged values). Without this the footer's "changed X, Y" toast
    # would lie whenever the UI sends the full row.
    try:
        before = registry.get(model_id).model_dump(mode="python")
    except Exception:
        before = {}

    model = registry.update(model_id, body)

    after = model.model_dump(mode="python")
    changed: list[str] = []
    for key in body:
        if key == "id":
            continue
        if before.get(key) != after.get(key):
            changed.append(key)

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None:
        await event_bus.emit(
            "model.updated",
            "info",
            f"model:{model.id}",
            f"{model.id}: updated ({', '.join(changed) or 'no-op'})",
            data={"id": model.id, "changed_fields": changed},
        )
    return _model_to_dict(model)


# ── DELETE + cascade ───────────────────────────────────────────────────────
#
# The slot-cascade mechanics (referencing-slot scan, TOML default-clear,
# unload, registry remove + snapshot GC) live in
# ``hal0.services.models_service``; this route keeps only the audit envelope,
# the 404/409 policy checks, and the terminal ``model.deleted`` emit.


@router.delete("/{model_id}")
async def delete_model(
    model_id: str,
    request: Request,
    force_cascade: bool = True,
) -> dict[str, object]:
    """Remove a model from the registry, cascading through referencing slots.

    Query param ``force_cascade`` (default ``true``) controls behaviour
    when at least one slot has this model as its ``[model].default``:

    * ``force_cascade=true`` (default): unload every running referrer,
      clear ``[model].default = ""`` in each slot's TOML, delete the
      registry row, then emit ``model.deleted`` *last* — so subscribers
      see the slot transitions before the model disappears.
    * ``force_cascade=false``: return 409 with the ``affected_slots`` list
      so the UI can render a confirm-cascade dialog.

    The actual model file on disk is never touched — that's the
    operator's call. Registry rows hold metadata, not bytes.
    """
    registry = request.app.state.model_registry
    # Wrap the whole resolve+cascade+remove so the durable audit row captures
    # BOTH the denied/unknown-id paths (404 / 409) and the successful delete
    # with a before/after summary. The EventBus ``model.deleted`` emit below is
    # a separate footer-ticker signal, not the audit record.
    async with record_action(
        request, category="model", action="model.delete", target=model_id
    ) as rec:
        if not registry.has(model_id):
            # Mirror the registry's typed envelope. We don't raise ModelNotFound
            # directly to avoid importing it at module scope; the registry's
            # remove() returns False silently, so we need an explicit guard.
            from hal0.registry.store import ModelNotFound

            raise ModelNotFound(
                f"model {model_id!r} not in registry",
                details={"model_id": model_id},
            )

        affected = _svc.slots_referencing_model(model_id)
        affected_names = [entry["name"] for entry in affected]

        if affected and not force_cascade:
            from hal0.errors import Conflict

            raise Conflict(
                f"model {model_id!r} is referenced by {len(affected)} slot(s); "
                f"retry with force_cascade=true to cascade",
                code="model.in_use",
                details={"model_id": model_id, "affected_slots": affected_names},
            )

        # Cascade order is load-bearing for the footer's ticker UX (unload
        # referrers → clear [model].default → registry delete → snapshot GC);
        # the terminal model.deleted emit fires AFTER the audit context closes.
        slot_manager = getattr(request.app.state, "slot_manager", None)
        removed = await _svc.cascade_delete_model(registry, slot_manager, model_id, affected)
        rec.after = {
            "id": model_id,
            "deleted": bool(removed),
            "cascaded_slots": affected_names,
        }

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None:
        await event_bus.emit(
            "model.deleted",
            "info",
            f"model:{model_id}",
            f"{model_id}: deleted"
            + (f" (cascaded {len(affected_names)} slot(s))" if affected_names else ""),
            data={"id": model_id, "affected_slots": affected_names},
        )
    return {
        "id": model_id,
        "deleted": bool(removed),
        "affected_slots": affected_names,
    }


@router.delete("/pulls/{model_id}", status_code=204)
async def delete_pull(model_id: str, request: Request):
    """Clear a terminal pull job from memory + disk.

    Returns 409 if the job is still active (queued/running).
    """
    jobs: dict[str, PullJob] = request.app.state.model_pull_jobs

    # Check in-memory first — active jobs must not be cleared
    job = jobs.get(model_id)
    if job is not None and job.state in ("queued", "running"):
        from hal0.errors import Conflict

        raise Conflict(
            f"pull job for {model_id!r} is still active (state={job.state})",
            code="pull.active",
            details={"model_id": model_id, "state": job.state},
        )

    # Remove from in-memory dict
    deleted_mem = jobs.pop(model_id, None) is not None

    # Remove persisted file
    deleted_disk = False
    with contextlib.suppress(OSError):
        p = _pull_job_file(model_id)
        if p.exists():
            p.unlink()
            deleted_disk = True

    if not deleted_mem and not deleted_disk:
        raise PullJobNotFound(
            f"no pull job for model {model_id!r}",
            details={"model_id": model_id},
        )

    return Response(status_code=204)


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


def _resolve_pull_capability(
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


def _seed_registry_from_body(
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
    registry here gives ``_resolve_pull_source`` something to look up on
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


def _resolve_pull_source_with_body(
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
    repo, file = _resolve_pull_source(request, model_id)
    if mmproj is None:
        # Curated vision picks carry their mmproj alongside the main GGUF.
        curated = get_curated(model_id)
        if curated is not None:
            mm = (getattr(curated, "mmproj_file", "") or "").strip()
            mmproj = mm or None
    return repo, file, mmproj, False


def _schedule_pull_task(
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


async def _run_pull_with_events(
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
            _persist_pull_job(job)
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
                    speed = _speed_bps(job)
                    eta = _eta_s(job, speed)
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
        _persist_pull_job(job)
        if task_cancelled:
            with contextlib.suppress(Exception):
                await _emit_terminal_pull_event(event_bus, job)

    await _emit_terminal_pull_event(event_bus, job)


async def _emit_terminal_pull_event(event_bus: Any, job: PullJob) -> None:
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


def _speed_bps(job: PullJob) -> float:
    """Approximate average bytes/s since the job started."""
    elapsed = max(time.time() - (job.started_at or time.time()), 0.001)
    return job.bytes_downloaded / elapsed


def _eta_s(job: PullJob, speed_bps: float) -> float | None:
    """Estimate seconds-to-completion from current rolling speed."""
    if speed_bps <= 0 or job.bytes_total <= 0:
        return None
    remaining = max(job.bytes_total - job.bytes_downloaded, 0)
    return remaining / speed_bps


@router.post("/{model_id}/pull", status_code=202)
async def pull_model(
    model_id: str,
    request: Request,
) -> dict[str, object]:
    """Start a background HuggingFace pull and return a job handle.

    Body (all fields optional)::

        {
          "hf_repo": "org/repo",              # add-by-HF-coords override
          "hf_filename": "model.gguf",        # required iff hf_repo is set
          "mmproj_filename": "mmproj.gguf",   # optional vision sidecar (WS-11)
          "labels": ["chat", "vision"],       # seeded onto the registry row
        }

    Resolution order:
      1. Body-supplied ``hf_repo`` + ``hf_filename`` (the Add-by-HF-coords
         modal path — seeds a registry row for ``model_id`` so the
         dashboard can show progress against a real entry, then pulls).
      2. Existing registry row's ``hf_repo`` + ``hf_filename`` (the
         ``pick-default`` path that ran already).
      3. The curated catalogue entry for ``model_id``.

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

    # FLM/NPU tags route through the toolbox container instead of HF.
    # The ``model:tag`` shape is the dispatch signal (HF ids never use
    # colons), validated against the FLM probe so a stray ``foo:bar``
    # falls through to the HF resolver and gets a clean 422.
    from hal0.providers.flm import is_flm_tag

    if is_flm_tag(model_id):
        return await _start_flm_pull(model_id, request, jobs)

    # Body is optional — an empty / non-JSON request is the legacy
    # "pull by id" path used by the wizard's Pull-against-curated row.
    body: dict[str, Any] | None = None
    try:
        if int(request.headers.get("content-length") or 0) > 0:
            body = await request.json()
            if not isinstance(body, dict):
                body = None
    except Exception:
        body = None

    hf_repo, hf_file, mmproj_file, from_body = _resolve_pull_source_with_body(
        request, model_id, body
    )
    if from_body:
        labels = body.get("labels") if isinstance(body, dict) else None
        if not isinstance(labels, list):
            labels = None
        chat_template = body.get("chat_template") if isinstance(body, dict) else None
        if not isinstance(chat_template, str):
            chat_template = None
        _seed_registry_from_body(request, model_id, hf_repo, hf_file, labels, chat_template)
    job = make_job(model_id)
    jobs[model_id] = job
    # Persist the queued snapshot before returning so a status poll resolves
    # even if the daemon restarts before the background task runs (#626).
    _persist_pull_job(job)

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None:
        await event_bus.emit(
            "pull.queued",
            "info",
            f"pull:{model_id}",
            f"{model_id}: queued ({hf_repo}/{hf_file})",
            data={"model_id": model_id, "hf_repo": hf_repo, "hf_file": hf_file},
        )

    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    registry = request.app.state.model_registry
    # P3: route the download into the capability-grouped store layout when the
    # capability is resolvable (body → registry → curated); None → flat fallback.
    capability, comfyui_subdir = _resolve_pull_capability(request, model_id, body)
    _schedule_pull_task(
        request.app.state,
        model_id,
        _run_pull_with_events(
            job,
            hf_repo=hf_repo,
            hf_file=hf_file,
            registry=registry,
            hf_token=hf_token,
            event_bus=event_bus,
            capability=capability,
            comfyui_subdir=comfyui_subdir,
            mmproj_file=mmproj_file,
        ),
    )
    out: dict[str, object] = {
        "id": job.job_id,
        "model_id": model_id,
        "state": job.state,
        "hf_repo": hf_repo,
        "hf_file": hf_file,
    }
    if mmproj_file:
        out["mmproj_file"] = mmproj_file
    return out


async def _start_flm_pull(
    model_id: str,
    request: Request,
    jobs: dict[str, PullJob],
) -> dict[str, object]:
    """Spawn a background ``flm pull`` job and return the job handle.

    Shares the PullJob/SSE plumbing with the HF pull path so the
    dashboard's pull progress UI works unchanged. The HF-specific
    progress decile + speed/ETA wrapper (:func:`_run_pull_with_events`)
    isn't reused here because its progress event payload assumes byte
    deltas from a single HTTP stream — FLM's container emits multiple
    files with its own progress lines and we don't want to misreport
    rate. Footer events are emitted directly around the run.
    """
    job = make_job(model_id)
    jobs[model_id] = job
    # Persist the queued snapshot before returning so a status poll resolves
    # even if the daemon restarts before the background task runs (#626).
    _persist_pull_job(job)
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
            _persist_pull_job(job)
            if event_bus is not None:
                await _emit_terminal_pull_event(event_bus, job)

    _schedule_pull_task(request.app.state, model_id, _run_flm_with_events())
    return {
        "id": job.job_id,
        "model_id": model_id,
        "state": job.state,
        "source": "flm",
    }


@router.get("/{model_id}/pull/status")
async def pull_status(model_id: str, request: Request) -> dict[str, object]:
    """Return the current pull job for ``model_id``.

    Mirror of the updater route shape — `id`, `state`, `bytes_*`,
    `error*`, `path`, `sha256`. Polling at ~500ms is fine; for live
    progress prefer the SSE stream.

    Falls back to the on-disk store (#626) so a status poll still
    resolves after an ``hal0-api`` restart wiped the process-local dict.
    """
    jobs: dict[str, PullJob] = request.app.state.model_pull_jobs
    job = jobs.get(model_id)
    if job is None:
        persisted = _load_persisted_pull_job(model_id, request.app.state.model_registry)
        if persisted is not None:
            return persisted
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

    Falls back to the on-disk store (#626) when the in-memory job is
    absent (e.g. after an ``hal0-api`` restart): emits one terminal
    frame from the persisted snapshot and closes.
    """
    jobs: dict[str, PullJob] = request.app.state.model_pull_jobs
    job = jobs.get(model_id)
    if job is None:
        persisted = _load_persisted_pull_job(model_id, request.app.state.model_registry)
        if persisted is not None:
            # Serve one terminal frame from the persisted snapshot so the
            # client's SSE consumer sees the final state and can close.
            async def _gen_persisted() -> Any:
                yield f"data: {json.dumps(persisted)}\n\n"

            return StreamingResponse(
                _gen_persisted(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        raise PullJobNotFound(
            f"no pull job for model {model_id!r}",
            details={"model_id": model_id},
        )

    shutting_down: asyncio.Event | None = getattr(request.app.state, "shutting_down", None)

    async def _gen() -> Any:
        # Emit an immediate snapshot so SSE clients don't sit at zero
        # while waiting for the first progress signal.
        yield f"data: {json.dumps(job.as_dict())}\n\n"
        while job.state in ("queued", "running"):
            # hal0-api shutdown (issue #1225): don't leave this generator
            # dangling open — it's a live HTTP connection that would
            # otherwise block uvicorn's graceful-shutdown connection drain
            # for as long as the job stays non-terminal. In the common case
            # the job itself flips to "cancelled" promptly once the
            # lifespan shutdown path cancels its pull task (see
            # hal0.api._shutdown_pull_jobs), which already unblocks the
            # loop condition above; this flag additionally bounds the wait
            # for any stream whose job doesn't (e.g. one already terminal
            # on disk but still being polled by a stale in-memory handle).
            if shutting_down is not None and shutting_down.is_set():
                break
            event = job.progress_event
            try:
                await asyncio.wait_for(event.wait(), timeout=5.0)
            except TimeoutError:
                if shutting_down is not None and shutting_down.is_set():
                    break
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


# ── HuggingFace inspect (POST /api/models/inspect) ────────────────────────────


# In-process TTL cache keyed by normalised HF repo id. Storing the whole
# response shape (variants + tags + metadata) keeps repeat Inspect clicks
# on the same modal session free; the 5 minute TTL is short enough that
# a freshly-uploaded quant lands within one render.
_INSPECT_TTL_SECONDS = 300
_INSPECT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


@router.post("/inspect")
async def inspect_model(request: Request) -> dict[str, Any]:
    """Inspect a HuggingFace repo and return pullable variants + metadata.

    Body shape (either key accepted, ``hf_url`` is the dashboard's older
    alias)::

        {"hf_repo": "unsloth/Qwen3-8B-GGUF"}
        {"hf_url":  "https://huggingface.co/unsloth/Qwen3-8B-GGUF"}

    Response::

        {
          "repo": "...",
          "variants": [{"id": "qwen3-8b-q4_k_m.gguf", "size_bytes": ..., "info": "..."}],
          "tags": ["text-generation", ...],
          "metadata": {"license": "...", "readme_excerpt": "..."}
        }

    Cached for ~5 minutes per repo. HF unreachable / 5xx → ``502``
    with ``hf.unreachable`` / ``hf.upstream_error``. Repo missing →
    ``404`` with ``hf.repo_not_found``.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")

    repo_input = body.get("hf_repo")
    if not isinstance(repo_input, str) or not repo_input.strip():
        repo_input = body.get("hf_url")
    if not isinstance(repo_input, str) or not repo_input.strip():
        raise BadRequest(
            "either 'hf_repo' (org/name) or 'hf_url' is required",
            code="hf.bad_request",
        )

    repo = _normalise_hf_repo(repo_input)
    if "/" not in repo:
        raise BadRequest(
            f"'{repo_input}' is not a valid org/name HF repo coordinate",
            code="hf.bad_request",
            details={"input": repo_input},
        )

    now = time.time()
    cached = _INSPECT_CACHE.get(repo)
    if cached is not None and now - cached[0] < _INSPECT_TTL_SECONDS:
        payload = dict(cached[1])
        payload["repo"] = repo
        payload["cached"] = True
        return payload

    result = await _fetch_hf_repo(repo)
    _INSPECT_CACHE[repo] = (now, result)
    return {"repo": repo, "cached": False, **result}


@router.post("/{model_id}/pull/cancel")
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
