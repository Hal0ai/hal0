"""Model registry endpoints (mounted under /api/models).

This is the *internal* models surface for the dashboard — distinct from
OpenAI-compat `/v1/models`.  Aggregates entries from every configured
upstream so the dashboard's Models view shows what's actually reachable,
plus any locally-registered models from the ModelRegistry.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from hal0.api._audit import record_action
from hal0.api.middleware.error_codes import BadRequest, NotFound
from hal0.config.loader import load_hal0_config
from hal0.registry import pull_jobs as _pull_jobs
from hal0.registry.curated import CURATED, CuratedModel, HaloaiModel
from hal0.registry.pull import PullInvalidSource
from hal0.registry.update_check import evaluate_model_update, fetch_remote_lfs_shas
from hal0.runners import RUNNER_IMAGES as _runner_images_registry
from hal0.services import models_service as _svc

# See slots.py for the writer-gate rationale.

router = APIRouter()

log = logging.getLogger(__name__)


# ── durable pull-job store (#626 / #MR-1) ─────────────────────────────────────
#
# The snapshot writer lives in registry.pull (``persist_pull_job``/
# ``pull_job_file``) so ``run_pull`` persists terminal state for EVERY caller —
# the dashboard route AND the installer/bundle-tier pulls that call run_pull
# directly. The full pull-job orchestration (start/track/update/cancel flows,
# the disk-fallback READ path ``load_persisted``/``reconcile_persisted``) now
# lives in ``hal0.registry.pull_jobs``; the route handlers below are thin shells
# over it and the underscore names are re-bound below.


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

# Runner-key registry (UI-API-1 item 2): the set of valid ``preferred_runner``
# keys the model PUT validates against. Bound here so the handler reads as a
# thin membership check.
_RUNNER_IMAGES = _runner_images_registry

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


# ── typed command bodies (typed-bodies inc-3) ────────────────────────────────
#
# House pattern (precedent: config.py / settings.py / dashboard_layout.py):
# parse the body by hand exactly as before (so a malformed-JSON / non-object
# body keeps its existing 400 + hal0 envelope), then run the parsed dict
# through a pydantic model and translate ``ValidationError`` into the SAME
# ``BadRequest`` (status + code) the site already raises for the equivalent
# bad-value case today. Deliberately NOT FastAPI body-params — every site
# below already 400s on bad input with the typed envelope, and body-params
# would flip that to FastAPI's 422 + a different envelope shape.
#
# LEFT LOOSE (not typed here — already screened downstream, typing would
# duplicate + risk drifting out of sync with the real boundary):
#   - create_model  — ``Model(**body)`` IS the validation (registry.store.Model
#     is the pydantic schema of record for a model row).
#   - update_model  — ``registry.update()`` re-validates through the same
#     ``Model`` schema; ``_svc.screen_model_write`` additionally screens the
#     launch-affecting fields (preferred_runner / extra_args) before that.
#   - add-from-path / inspect / validate — thin route shells whose body
#     shape is screened inside ``hal0.services.models_service``
#     (``add_from_path`` / ``inspect_hf_repo`` / ``screen_model_write``).
#   - scan (plain ``POST /scan``) — already defensive by design: any body
#     read failure or a non-list ``rows`` falls back to the old
#     roots-walk auto-scan rather than 400ing; that fallback IS the
#     intended contract, not a validation gap.


class _ScanPreviewBody(BaseModel):
    """``POST /scan/preview`` body — only ``paths`` needs real typing.

    ``recursive`` is intentionally left OUT of this model (see
    ``scan_preview`` below) — its existing ``bool(body.get(...))`` truthy
    coercion must not be replaced by pydantic's stricter bool parsing.
    """

    paths: list[str] = []


class _SetModelDefaultBody(BaseModel):
    """``POST /{model_id}/default`` body.

    ``default`` is typed ``Any`` ON PURPOSE, not ``bool`` — see
    ``set_model_default`` below for why: pydantic's bool coercion parses
    the STRING ``"false"`` as ``False``, but the existing code path does
    ``bool(body["default"])``, and Python's ``bool("false")`` is ``True``
    (any non-empty string is truthy). Typing this field ``bool`` would
    silently flip that result — a real behavior change this lane must not
    introduce. Kept as a pydantic model anyway (rather than a bare dict)
    so the body-shape validation still follows the house pattern.
    """

    default: Any = None


def _validation_error_details(exc: ValidationError) -> dict[str, str]:
    """Render a pydantic ValidationError into ``{field_path: message}``.

    Same shape as the identically-named helper in ``config.py`` /
    ``dashboard_layout.py`` / ``settings.py`` / ``slots.py`` — kept local
    per-module rather than shared, matching the house pattern.
    """
    out: dict[str, str] = {}
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        out[loc or "<root>"] = err.get("msg", "invalid")
    return out


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

    try:
        parsed_body = _ScanPreviewBody.model_validate({"paths": body.get("paths") or []})
    except ValidationError as exc:
        # Covers both a non-list `paths` (old behavior: same message) and a
        # list containing non-string entries (NEW: the old code only checked
        # `isinstance(raw_paths, list)`, so e.g. `{"paths": [1, 2]}` used to
        # sail through and crash downstream Path()/detect() calls on the int
        # instead of failing here with a clean 400).
        raise BadRequest(
            "'paths' must be a non-empty list of absolute path strings",
            details=_validation_error_details(exc),
        ) from exc
    raw_paths = parsed_body.paths
    if not raw_paths:
        raise BadRequest("'paths' must be a non-empty list of absolute paths")
    # `recursive` stays a raw truthy-coerce exactly like before — see
    # `_ScanPreviewBody`'s docstring for why it's not a typed bool field.
    recursive = bool(body.get("recursive", True))

    cfg = load_hal0_config()
    extensions = {e.lower() for e in cfg.models.file_extensions}

    preview = _svc.preview_scan_rows(raw_paths, recursive, extensions)

    # Drift view: existing registry rows whose backing file is now absent.
    # Surfaced so the operator/UI can see what a `{"prune": true}` scan would
    # remove BEFORE committing. ``referenced`` flags rows a slot/stack still
    # points at — those are protected from prune (repair, don't delete).
    registry = request.app.state.model_registry
    missing = _svc.missing_registry_rows(registry)

    return {"preview": preview, "count": len(preview), "missing": missing}


@router.post("/scan")
async def scan_models(request: Request) -> dict[str, Any]:
    """Walk model roots and register new files — legacy auto-scan, or
    commit a user-edited preview when ``rows`` is supplied.

    Two body shapes:

    * **Legacy / empty body** — walk the configured ``[models].roots`` and
      auto-register every new candidate via the discover module. Each
      added model fires a ``model.registered`` event with
      ``source='scan'``. Pass ``{"prune": true}`` to also reconcile the
      registry: rows whose backing file is missing on disk are removed
      (each firing a ``model.pruned`` event) UNLESS the id is referenced by
      a slot or stack, in which case it is reported under
      ``missing_referenced`` for repair rather than deleted.

    * **``{"rows": [...]}``** — commit pre-vetted preview rows. Each row
      may carry user-edited ``backends`` / ``capabilities`` / ``defaults``
      / ``name`` / ``id`` overrides; otherwise we fall back to the
      detection output for that path. User overrides always win — that's
      the whole point of the preview round-trip.

    Returns ``{added, skipped, scanned_roots}`` in both modes (plus
    ``pruned`` / ``missing_referenced`` on the auto-scan path) so the UI's
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

    prune = bool(body.get("prune", False)) if isinstance(body, dict) else False
    cfg = load_hal0_config()
    return await _svc.auto_scan_and_register(registry, cfg.models, event_bus, prune=prune)


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
    # Screen launch-affecting fields at CREATE time too (UI-API-1 item 1): PUT
    # already screened, but create wrote straight to the registry, so a row
    # born with an extra_args that smuggles --port/--model/… would only fail at
    # launch (or silently rebind the slot). Same helper the PUT/validate paths use.
    _svc.screen_model_write(body, runner_images=_RUNNER_IMAGES)
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


@router.post("/validate")
async def validate_model_write(request: Request) -> dict[str, Any]:
    """Dry-run screen of a model create/edit body — no registry write (UI-API-1).

    The dashboard's Model drawer POSTs a candidate ``{defaults, preferred_runner}``
    here before saving, so a managed-arg smuggle (e.g. ``--port`` in
    ``defaults.extra_args``) or an unknown ``preferred_runner`` is surfaced inline
    instead of only at the eventual save (or, worse, at launch). Returns
    ``{"ok": true}`` on a clean body; a violation raises the SAME typed envelope
    (``slot.managed_arg_denied`` / ``model.extra_args_unparseable`` /
    ``model.extra_args_json_quoting`` / ``model.unknown_runner`` /
    ``model.defaults_invalid`` / ``model.context_size_out_of_range`` /
    ``model.vision_requires_mmproj`` / ``model.mmproj_not_found``) the create and
    PUT paths raise, so the drawer renders one error path.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    # A dry run of a sparse EDIT needs the stored row for the same reason the PUT
    # does (#1393) — the drawer sends only changed keys, so a body that adds
    # ``vision`` must be screened against the persisted ``mmproj``. ``id`` is
    # optional and unknown ids fall back to a create-shaped screen; the registry
    # is only READ here (this route never writes).
    existing: dict[str, Any] | None = None
    candidate_id = body.get("id")
    if isinstance(candidate_id, str) and candidate_id:
        with contextlib.suppress(Exception):
            existing = request.app.state.model_registry.get(candidate_id).model_dump(mode="python")
    _svc.screen_model_write(body, runner_images=_RUNNER_IMAGES, existing=existing)
    return {"ok": True}


@router.get("/pulls")
async def list_pulls(request: Request) -> list[dict[str, Any]]:
    """Return all pull jobs (active in-memory + persisted terminal from disk).

    Dedup: in-memory jobs win over persisted snapshots for the same model_id.
    Aggregation + enrichment + sort live in :func:`hal0.registry.pull_jobs.list_all`.
    """
    return _pull_jobs.list_all(
        request.app.state.model_pull_jobs,
        request.app.state.model_registry,
    )


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

    # Job creation + queued-snapshot persist + emit + detached scheduling live in
    # ``pull_jobs.enqueue_update``; ``_run_pull_with_events`` is passed by module
    # binding to preserve the test-suite monkeypatch seam.
    return await _pull_jobs.enqueue_update(
        request,
        model_id=model_id,
        hf_repo=hf_repo,
        hf_file=hf_file,
        dest=dest,
        run_wrapper=_run_pull_with_events,
    )


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

    ``defaults`` and ``capability_flags`` are the two tables where "any
    subset" reaches INSIDE the object (#1413): an absent sub-key keeps the
    stored value, an explicit ``null`` clears that one value, and
    ``{"defaults": null}`` drops the whole table. Every other field —
    including ``metadata`` and the list-valued ``capabilities``/``tags``/
    ``backends`` — is a flat replace. See
    :func:`hal0.registry.store.merge_update`.

    Errors:
      * ``400 model.defaults_invalid`` — a ``defaults`` value that isn't
        parseable as its ``ModelDefaults`` type (#1414).
      * ``400 model.context_size_out_of_range`` — ``defaults.context_size``
        outside the launchable range (#1414).
      * ``400 slot.hardware_flag_denied`` / ``400 slot.managed_arg_denied`` —
        ``defaults.extra_args`` reaching for slot- or authority-owned flags.
      * ``404 model.not_found`` — ``model_id`` not registered.
    """
    registry = request.app.state.model_registry
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")

    # Snapshot the pre-update model FIRST — it feeds two consumers: the
    # changed-fields diff below, and the write screen, which needs the stored row
    # to resolve a sparse body (#1393: a PUT that adds ``vision`` without
    # mentioning ``mmproj`` must be screened against the PERSISTED projector).
    try:
        before = registry.get(model_id).model_dump(mode="python")
    except Exception:
        before = {}

    # Validate the launch-affecting fields (defaults.extra_args + the
    # vision↔mmproj pairing) at SAVE time (UI-API-1 items 1/2, #1393). Shared
    # with create + /validate so the three paths never drift; fails the write
    # with the same envelope the launch path raises.
    _svc.screen_model_write(body, runner_images=_RUNNER_IMAGES, existing=before)

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


@router.post("/{model_id}/default")
async def set_model_default(model_id: str, request: Request) -> dict[str, Any]:
    """Promote or clear a model's per-type default marker.

    Body (optional)::

        {"default": true}    # promote — demotes the current holder of this type
        {"default": false}   # clear — the type is left with NO default

    ``default`` defaults to ``true`` when the body is empty/omitted, so a bare
    POST is "set as default". The single-holder invariant (at most one default
    model per dispatcher type) is enforced in ONE place —
    :func:`hal0.services.models_service.set_model_type_default` — which this
    route and the slot-create wiring both call. Promotion is atomic + idempotent
    (re-promoting the current holder is a no-op).

    Emits ``model.updated`` with ``changed_fields=["default"]`` for the target
    and each demoted peer so the footer ticker + Models poll refresh.

    Errors:
      * ``404 model.not_found`` — ``model_id`` not registered.
    """
    registry = request.app.state.model_registry
    # Body is optional — a bare POST means "set as default".
    default = True
    try:
        if int(request.headers.get("content-length") or 0) > 0:
            body = await request.json()
            if isinstance(body, dict) and "default" in body:
                # `_SetModelDefaultBody.default` is `Any` — this parse step
                # cannot itself raise ValidationError; it exists only to
                # keep the body-shape read on the house pattern. See the
                # model's docstring for why `default` isn't typed `bool`:
                # pydantic's bool coercion would parse the STRING "false"
                # as `False`, but the existing `bool(x)` below treats any
                # non-empty string as truthy — e.g. `{"default": "false"}`
                # stays `True`, same as today.
                parsed_body = _SetModelDefaultBody.model_validate(body)
                default = bool(parsed_body.default)
    except Exception as exc:
        raise BadRequest("body must be valid JSON", details={"error": str(exc)}) from exc

    try:
        result = _svc.set_model_type_default(registry, model_id, default=default)
    except Exception as exc:  # ModelNotFound → typed 404 envelope
        from hal0.registry.store import ModelNotFound

        if isinstance(exc, ModelNotFound):
            raise NotFound(f"model {model_id!r} not found", code="model.not_found") from None
        raise

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None and result["changed"]:
        touched = [result["model_id"], *result["demoted"]]
        for mid in touched:
            await event_bus.emit(
                "model.updated",
                "info",
                f"model:{mid}",
                f"{mid}: updated (default)",
                data={"id": mid, "changed_fields": ["default"]},
            )
    return result


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

    Returns 409 if the job is still active (queued/running). The active-guard +
    mem/disk removal live in :func:`hal0.registry.pull_jobs.delete`.
    """
    _pull_jobs.delete(model_id, request.app.state.model_pull_jobs)
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

    In-flight dedup, FLM dispatch, source/capability resolution, registry
    seeding, job creation + queued-snapshot persist, ``pull.queued`` emit and
    detached scheduling all live in :func:`hal0.registry.pull_jobs.enqueue`;
    ``_run_pull_with_events`` is passed by module binding to preserve the
    test-suite monkeypatch seam.
    """
    return await _pull_jobs.enqueue(request, model_id=model_id, run_wrapper=_run_pull_with_events)


@router.get("/{model_id}/pull/status")
async def pull_status(model_id: str, request: Request) -> dict[str, object]:
    """Return the current pull job for ``model_id``.

    Mirror of the updater route shape — `id`, `state`, `bytes_*`,
    `error*`, `path`, `sha256`. Polling at ~500ms is fine; for live
    progress prefer the SSE stream.

    Falls back to the on-disk store (#626) so a status poll still
    resolves after an ``hal0-api`` restart wiped the process-local dict.
    Snapshot + disk-fallback live in :func:`hal0.registry.pull_jobs.status`.
    """
    return _pull_jobs.status(
        model_id,
        request.app.state.model_pull_jobs,
        request.app.state.model_registry,
    )


@router.get("/{model_id}/pull/stream")
async def pull_stream(model_id: str, request: Request) -> StreamingResponse:
    """SSE stream of pull progress.

    Emits one ``data:`` frame at start, then one per ~256 KiB or every
    500ms (whichever is rarer), and a final frame on completion
    /failure/cancellation. Idempotent: subscribing after the job has
    finished yields one frame with the terminal state and closes.

    Falls back to the on-disk store (#626) when the in-memory job is
    absent (e.g. after an ``hal0-api`` restart): emits one terminal
    frame from the persisted snapshot and closes. The generator + disk
    fallback + shutdown short-circuit live in
    :func:`hal0.registry.pull_jobs.build_stream_response`.
    """
    return _pull_jobs.build_stream_response(model_id, request)


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
    The flag-set + not-found guard live in
    :func:`hal0.registry.pull_jobs.cancel`.
    """
    return _pull_jobs.cancel(model_id, request.app.state.model_pull_jobs)
