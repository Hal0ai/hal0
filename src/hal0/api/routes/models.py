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
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from hal0.api._audit import record_action
from hal0.api.middleware.error_codes import BadRequest, NotFound
from hal0.config.loader import load_hal0_config
from hal0.registry import pull_jobs as _pull_jobs
from hal0.registry.curated import CURATED, CuratedModel, HaloaiModel
from hal0.registry.pull import (
    PullInvalidSource,
    PullJob,
    PullJobNotFound,
    list_persisted_jobs,
    make_job,
)
from hal0.registry.pull import persist_pull_job as _persist_pull_job
from hal0.registry.pull import pull_job_file as _pull_job_file
from hal0.registry.update_check import evaluate_model_update, fetch_remote_lfs_shas
from hal0.services import models_service as _svc

# See slots.py for the writer-gate rationale.

router = APIRouter()

log = logging.getLogger(__name__)


# ── durable pull-job store (#626 / #MR-1) ─────────────────────────────────────
#
# The snapshot writer lives in registry.pull (imported above as
# ``_persist_pull_job``/``_pull_job_file``) so ``run_pull`` persists terminal
# state for EVERY caller — the dashboard route AND the installer/bundle-tier
# pulls that call run_pull directly. The disk-fallback READ path
# (``load_persisted``/``reconcile_persisted``) now lives in
# ``hal0.registry.pull_jobs`` and is re-bound below.


# ── service-layer bindings (P3-routers §J) ───────────────────────────────────
#
# The registry classification, row-serialisation, scan-commit and delete-cascade
# logic moved to ``hal0.services.models_service``; the HuggingFace/FLM pull-job
# orchestration (source/capability resolution, registry seeding, task scheduler,
# progress-event wrapper, terminal emit, persisted-snapshot fallback, FLM pull)
# moved to ``hal0.registry.pull_jobs`` — so the route handlers below are
# request→service→envelope shells. These module-level underscore names keep the
# handler bodies reading as thin call sites, preserve the monkeypatch seams the
# test-suite depends on (``routes.models._run_pull_with_events``), and preserve
# the ``hal0.api.__init__`` import of ``_run_pull_with_events``/
# ``_schedule_pull_task``. New callers should import from the service modules
# (``hal0.services.models_service`` / ``hal0.registry.pull_jobs``) directly.
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

# Pull-job orchestration bindings (P3-routers §J → hal0.registry.pull_jobs).
_load_persisted_pull_job = _pull_jobs.load_persisted
_reconcile_persisted_pull_job = _pull_jobs.reconcile_persisted
_resolve_pull_source = _pull_jobs.resolve_pull_source
_resolve_pull_capability = _pull_jobs.resolve_pull_capability
_seed_registry_from_body = _pull_jobs.seed_registry_from_body
_resolve_pull_source_with_body = _pull_jobs.resolve_pull_source_with_body
_schedule_pull_task = _pull_jobs.schedule_pull_task
_run_pull_with_events = _pull_jobs.run_pull_with_events
_emit_terminal_pull_event = _pull_jobs.emit_terminal_pull_event
_speed_bps = _pull_jobs.speed_bps
_eta_s = _pull_jobs.eta_s
_start_flm_pull = _pull_jobs.start_flm_pull


@router.get("")
async def list_models(request: Request) -> dict[str, Any]:
    """Aggregate models from the local registry + every upstream.

    Local registry entries (a real file on disk) win on id collision —
    the upstream might still advertise the id, but the user has the
    bytes locally and that's the truth. Each row carries ``installed``
    so the UI can render an installed/advertised badge. The three-source
    aggregation lives in :func:`hal0.services.models_service.list_all`.
    """
    return await _svc.list_all(
        registry=request.app.state.model_registry,
        upstreams=request.app.state.upstreams,
        cache=getattr(request.app.state, "model_cache", {}),
        update_state=getattr(request.app.state, "model_update_state", None),
    )


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

    Detection + derivation + registry write + event emit live in
    :func:`hal0.services.models_service.add_from_path`.
    """
    registry = request.app.state.model_registry
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    event_bus = getattr(request.app.state, "events", None)
    return await _svc.add_from_path(body, registry=registry, event_bus=event_bus)


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

    # Screen defaults.extra_args against the managed-arg denylist at SAVE
    # time. Launch already rejects loudly (slot.managed_arg_denied), so this
    # is defense-in-depth/UX for non-dashboard clients: fail the write with
    # the same envelope instead of persisting a tune that can never load.
    defaults = body.get("defaults")
    if isinstance(defaults, dict) and isinstance(defaults.get("extra_args"), str):
        import shlex

        from hal0.slots.argv import _deny_managed_flags

        try:
            tokens = shlex.split(defaults["extra_args"])
        except ValueError as exc:
            raise BadRequest(
                f"defaults.extra_args is not parseable as a flag string: {exc}",
                code="model.extra_args_unparseable",
            ) from exc
        _deny_managed_flags(tokens, segment="model defaults.extra_args")

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


# The repo-coordinate resolution + TTL cache + Hub fetch moved to
# ``hal0.services.models_service`` (P3-routers §J). The cache binding is
# re-exported so ``tests/api/test_models_routes.py`` can still reset it via
# ``routes.models._INSPECT_CACHE.clear()``.
_INSPECT_TTL_SECONDS = _svc.INSPECT_TTL_SECONDS
_INSPECT_CACHE = _svc.INSPECT_CACHE


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
    ``404`` with ``hf.repo_not_found``. Resolution + cache + fetch live in
    :func:`hal0.services.models_service.inspect_hf_repo`.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    return await _svc.inspect_hf_repo(body)


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
