"""Settings (config) endpoints (mounted under /api/settings).

Typed read/write of ``/etc/hal0/hal0.toml`` (or the HAL0_HOME-rooted
override). All writes go through ``hal0.config.loader.hal0_config_txn``
which uses the same tempfile+fsync+os.replace pattern as
``write_env_atomic`` — never a partial-write.

Endpoints:
    GET  /api/settings              — current parsed Hal0Config as a dict.
    PUT  /api/settings              — partial update; deep-merged into the
                                      existing config, validated against
                                      the pydantic schema, then atomically
                                      written. Response includes
                                      ``_hal0.apply_plan`` and
                                      ``_hal0.changeset`` so the UI can
                                      render the right effect badge and
                                      toast from the real diff without a
                                      second round-trip.
    POST /api/settings/preview      — same body shape as the PUT, same
                                      ChangeSet computation, writes
                                      nothing — for a confirm-before-apply
                                      drawer (#1967, #2195, #2203, #1511).
    POST /api/settings/reload       — re-read /etc/hal0/hal0.toml from
                                      disk into the running process.
    GET  /api/settings/schema       — pydantic JSON schema of Hal0Config
                                      so the dashboard can render typed
                                      fields without hard-coding shapes.
    GET  /api/settings/apply-plan   — full key→apply-class registry the
                                      dashboard mounts once to render
                                      per-row effect badges (#552).
    GET  /api/settings/fields       — one row per operator-editable schema
                                      key (group/label/description/type/
                                      enum/range/default/current/reload
                                      class/secret) so the dashboard can
                                      render a labelled row for every
                                      config key automatically (#2108).

Validation failures return the structured error envelope with
``code: "config.invalid"`` and ``details`` containing a per-field
``{field_path: message}`` map.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import ValidationError

from hal0.api._redact import redact_config
from hal0.api._settings_apply import APPLY_CLASSES, get_registry
from hal0.api._settings_changeset import changeset_payload, compute_settings_changeset
from hal0.api._settings_fields import build_settings_fields
from hal0.api.middleware.error_codes import BadRequest, Hal0Error
from hal0.config.loader import hal0_config_txn, load_hal0_config
from hal0.config.schema import Hal0Config
from hal0.registry.model_store import (
    MigrationPlan,
    build_suggestions,
    describe_store_state,
    execute_migration,
    plan_migration,
)

log = logging.getLogger(__name__)

# See slots.py for the writer-gate rationale.

router = APIRouter()


class ConfigInvalidError(Hal0Error):
    """Schema validation failure — typed so the envelope carries field paths."""

    code = "config.invalid"
    status = 400


def _validation_error_details(exc: ValidationError) -> dict[str, str]:
    """Render a pydantic ValidationError into ``{field_path: message}``."""
    out: dict[str, str] = {}
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        out[loc or "<root>"] = err.get("msg", "invalid")
    return out


def _config_to_dict(cfg: Hal0Config) -> dict[str, Any]:
    """Project a Hal0Config into a JSON-safe dict, scrubbing sensitive keys.

    Every config-echoing endpoint routes through this helper so the
    redaction is applied exactly once (#553). The walk masks any
    sensitive-named value (api_key, token, password, …) at any depth,
    which catches stragglers living in the ``extra: dict[str, Any]``
    pydantic escape hatch.
    """
    return redact_config(cfg.model_dump(mode="json"))


@router.get("")
async def get_settings(request: Request) -> dict[str, Any]:
    """Return the current Hal0Config as JSON.

    The dashboard's Settings view reads this on mount. Missing
    ``/etc/hal0/hal0.toml`` is fine: the loader returns the all-defaults
    Hal0Config, which is the legitimate state of a fresh install.
    """
    cfg = getattr(request.app.state, "hal0_config", None)
    if cfg is None:
        cfg = load_hal0_config()
        request.app.state.hal0_config = cfg
    return _config_to_dict(cfg)


@router.put("")
async def update_settings(request: Request) -> dict[str, Any]:
    """Apply a partial update to hal0.toml.

    Body shape: any subset of ``Hal0Config`` keys. Nested objects are
    deep-merged into the current config so callers only need to send
    the keys they're changing (e.g. ``{"telemetry": {"enabled": true}}``
    flips one bit without restating the rest of ``telemetry``).

    Validation failures return ``code: "config.invalid"`` with a
    ``details`` map of per-field reasons.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise Hal0Error("request body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise Hal0Error("request body must be a JSON object")

    # One serialized RMW path for every hal0.toml writer (#1721). The txn
    # holds the shared in-process lock AND the cross-process advisory lock,
    # and hands back a config read from DISK inside both — never
    # ``app.state.hal0_config``, which any other writer may have invalidated
    # (#1717 review: a settings PUT waiting behind a ~60s graph PUT used to
    # resume on the stale cached snapshot, merge its own unrelated patch into
    # it, and overwrite the graph section the graph route had just written).
    #
    # ``compute_settings_changeset`` is the SAME function
    # ``POST /api/settings/preview`` calls (#1967/#2195/#2203/#1511-shaped
    # fixtures in tests/api/test_settings_changeset.py) — one diff, read
    # here under the write lock so it can never see a value a concurrent
    # writer is mid-changing.
    async with hal0_config_txn(request) as txn:
        try:
            cs = compute_settings_changeset(txn.config, body)
        except ValidationError as exc:
            raise ConfigInvalidError(
                "hal0 config failed schema validation",
                details=_validation_error_details(exc),
            ) from exc

        # Atomic write via the txn — which also refreshes
        # ``app.state.hal0_config`` so no writer can leave that cache stale.
        try:
            txn.save(cs.merged)
        except OSError as exc:
            raise Hal0Error(
                f"could not persist hal0 config: {exc}",
                details={"error": str(exc), "errno": getattr(exc, "errno", None)},
            ) from exc

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None:
        # Surface a footer chip when the operator saves the config. The
        # data field intentionally carries only the top-level keys touched
        # by the PATCH so secrets / api keys don't leak into the ring.
        await event_bus.emit(
            "system.config_save",
            "info",
            "system",
            "hal0 config saved",
            data={"keys": sorted(body.keys())},
        )

    # Issue #552 — the per-save apply plan. The UI needs to know which
    # keys are live vs. which need a service restart vs. which need a
    # manual operator action *before* it renders the success toast, so
    # the partition rides along in the response under ``_hal0.apply_plan``.
    # The top-level config dict stays the same shape — this is purely
    # additive (#545). ``cs.plan`` is byte-identical to the old inline
    # ``apply_plan(_dotted_leaf_keys(body))`` call — every touched leaf,
    # whether or not its value actually changed, classified or landed in
    # ``unknown`` (never silently dropped).
    #
    # ``_hal0.changeset`` is the newer, stricter view (#1967/#2195/#2203/
    # #1511): only leaves whose value actually changed, each with its own
    # before/after and reload class, so the UI can toast from truth
    # instead of re-deriving "what changed" from the raw PATCH body.
    config_view = _config_to_dict(cs.merged)
    config_view["_hal0"] = {"apply_plan": cs.plan, "changeset": changeset_payload(cs)}
    return config_view


@router.post("/preview")
async def preview_settings(request: Request) -> dict[str, Any]:
    """Compute the ChangeSet a PUT with this body would apply, without writing.

    Calls :func:`hal0.api._settings_changeset.compute_settings_changeset` —
    the exact same function ``update_settings`` calls — so preview and
    apply can never disagree about what a PATCH would do (the class of bug
    behind #1967/#2195/#2203/#1511: two call sites independently deriving
    "what changed"). Body shape is identical to ``PUT /api/settings``.

    Reads against the live in-process config (``app.state.hal0_config``),
    not a fresh disk re-read under the write lock — a preview never blocks
    on, or competes with, a concurrent writer. The actual apply always
    re-reads fresh under the lock in ``update_settings``, so a preview is a
    best-effort snapshot, same caveat as any dry-run.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise Hal0Error("request body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise Hal0Error("request body must be a JSON object")

    cfg = getattr(request.app.state, "hal0_config", None)
    if cfg is None:
        cfg = load_hal0_config()
        request.app.state.hal0_config = cfg

    try:
        cs = compute_settings_changeset(cfg, body)
    except ValidationError as exc:
        raise ConfigInvalidError(
            "hal0 config failed schema validation",
            details=_validation_error_details(exc),
        ) from exc

    return {"changeset": changeset_payload(cs), "apply_plan": cs.plan}


@router.post("/reload")
async def reload_settings(request: Request) -> dict[str, Any]:
    """Re-read hal0.toml from disk into ``app.state.hal0_config``.

    Returns the freshly loaded config. Used after an external editor
    changes the TOML (the dashboard hot-edits go through PUT and don't
    need this).
    """
    try:
        cfg = load_hal0_config()
    except Hal0Error:
        # Loader raises ConfigParseError (a Hal0Error subclass) on bad
        # TOML — let the envelope middleware surface it as-is.
        raise
    request.app.state.hal0_config = cfg
    return _config_to_dict(cfg)


@router.get("/schema")
async def settings_schema() -> dict[str, Any]:
    """Return the pydantic JSON schema of Hal0Config.

    Lets the dashboard render field metadata (description, types,
    constraints) without hard-coding the shape. Mirrors what
    ``/api/openapi.json`` advertises but without the FastAPI envelope.
    """
    return Hal0Config.model_json_schema()


@router.get("/apply-plan")
async def get_apply_plan() -> dict[str, Any]:
    """Return the full settings-apply-plan registry (issue #552).

    Response shape::

        {
          "apply_classes": ["immediate", "service-restart", "manual-restart"],
          "registry": {
            "log_level":          {"apply_class": "immediate",       "services": []},
            "models.store":       {"apply_class": "service-restart", "services": ["slots"]},
            "slots.max_slots":    {"apply_class": "service-restart", "services": ["hal0-api"]},
            "slots.port_range_start": {"apply_class": "manual-restart", "services": []},
            ...
          }
        }

    The dashboard fetches this once on mount so each settings row can
    render the right apply badge (live / ⟳ restart <service> / ⚠
    manual restart) without a per-save server round-trip. The per-save
    partition still rides along on the PUT response as
    ``_hal0.apply_plan`` so the success toast can show the precise
    effect split for just the keys that were touched.
    """
    return {
        "apply_classes": list(APPLY_CLASSES),
        "registry": get_registry(),
    }


async def _live_target_for(request: Request, value: Any) -> bool | None:
    """Whether a ``hal0/<slot>``-style value currently resolves to a loaded slot.

    Returns ``None`` when ``value`` isn't a virtual model reference (nothing
    to say), ``True``/``False`` otherwise. Used to flag #2108's fresh-install
    gap: ``[brain_chat].tool_model`` defaults to ``hal0/agent``, and that
    slot ships unbound by design, so the default has nowhere live to route
    tool turns until an operator pulls a model into it — this lets the
    renderer say so plainly instead of the operator discovering it mid-chat.
    """
    if not isinstance(value, str) or not value.startswith("hal0/"):
        return None
    try:
        from hal0.api.routes.v1 import _normalize_loaded_models, _normalize_slot_views
        from hal0.normalize.resolver import LiveSlotResolver

        views = await _normalize_slot_views(request)
        resolver = LiveSlotResolver(
            slot_views_provider=lambda: views,
            loaded_models_provider=lambda: _normalize_loaded_models(request),
        )
        res = await resolver.resolve(value)
    except Exception:
        return None
    if res is None:
        return None
    return not res.fallback


@router.get("/fields")
async def get_settings_fields(request: Request) -> dict[str, Any]:
    """Schema-driven settings field metadata (#2108, #1967, #2195, #2203, #1511).

    One row per operator-editable ``Hal0Config`` leaf — group, label,
    description, type/enum/range, default, current value, reload class
    (``apply_class``/``services``), and secret flag — so the dashboard
    renders a labelled row for every schema key automatically instead of
    requiring a hand-authored FormRow per field (the gap that left
    ``[brain_chat].tool_model`` with no dashboard path).

    :func:`hal0.api._settings_fields.build_settings_fields` supplies
    everything a live config alone can answer (schema metadata + current
    value + reload class, joined from the same
    :data:`hal0.api._settings_apply.REGISTRY` ``/api/settings/apply-plan``
    serves, so the two endpoints can never disagree on a key's reload
    effect — see ``tests/api/test_settings_fields.py`` for the ratchet that
    keeps every schema leaf classified in that registry). This route adds
    the one thing that needs a live ``Request``: ``live_target``, populated
    only for a ``hal0/<slot>``-shaped current value, ``true``/``false`` for
    whether that alias currently resolves to a loaded slot — ``null`` for
    every other field. That flags #2108's fresh-install gap directly:
    ``tool_model`` defaults to ``hal0/agent``, and that slot ships unbound
    by design, so a fresh box's row renders with ``live_target: false``
    instead of the operator discovering the gap mid-chat.
    """
    cfg = getattr(request.app.state, "hal0_config", None)
    if cfg is None:
        cfg = load_hal0_config()
        request.app.state.hal0_config = cfg
    fields = build_settings_fields(cfg)
    for row in fields:
        row["live_target"] = await _live_target_for(request, row["current"])
    return {"fields": fields}


# ── Model storage (Settings → Models · FirstRun "Storage" step) ────────────
#
# ONE setting that all model consumers point at. Replaces PR #313's roots
# + pull_root with a single ``[models].store`` field; the legacy field is
# retained for round-trip compat (see ModelsConfig.effective_store).
#
# Endpoints:
#   GET  /api/settings/models/store              — current state + suggestions.
#   POST /api/settings/models/store              — set + propagate; dry-run by
#                                                  default when a move is
#                                                  required. Pass migrate=true
#                                                  (or hit /migrate) to commit.
#   POST /api/settings/models/store/migrate      — explicit migrate-then-apply.


def _store_state_payload(cfg: Hal0Config) -> dict[str, Any]:
    """Bundle current store + per-path probe + suggestion chips.

    The UI calls this on mount; firstrun calls it to render its preset
    chips. Keeping it in one helper means the dry-run POST response
    embeds the same shape so callers don't fork their render paths.
    """
    effective = cfg.models.effective_store()
    state = describe_store_state(effective)
    return {
        "store": cfg.models.store or None,
        "effective": effective,
        "fallback_active": not bool(cfg.models.store),
        "pull_root_legacy": cfg.models.pull_root,
        "current_state": state.to_dict(),
        "suggestions": build_suggestions(current=effective),
    }


@router.get("/models/store")
async def get_model_store(request: Request) -> dict[str, Any]:
    """Return the model-store setting + suggestions for the UI to render.

    The response carries:
      * ``store`` — the raw value of ``[models].store`` (``None`` when
        unset and the legacy ``pull_root`` is being used).
      * ``effective`` — the path the pull engine actually uses.
      * ``fallback_active`` — True when ``store`` is unset and we're
        riding the PR-#313 ``pull_root`` for backward compatibility.
      * ``current_state`` — probe of the effective path (exists / files
        / size / free).
      * ``suggestions`` — preset chips for firstrun + settings.
    """
    cfg = getattr(request.app.state, "hal0_config", None)
    if cfg is None:
        cfg = load_hal0_config()
        request.app.state.hal0_config = cfg
    return _store_state_payload(cfg)


@router.post("/models/store")
async def set_model_store(request: Request) -> dict[str, Any]:
    """Set ``[models].store`` and propagate to every consumer.

    Body::

        {"path": "/mnt/ai-models", "migrate": false}

    Validation:
      * ``path`` must be a non-empty absolute string. Must exist, be a
        directory, be readable + writable. Empty string is rejected — to
        clear the override and fall back to the legacy ``pull_root``,
        pass the literal ``pull_root`` value.
      * If the effective store currently has files and they don't yet
        live at ``path``, a migration is required.

    Behaviour:
      * **Dry-run** (default, ``migrate=false``): when a move is needed,
        responds 200 with ``{status: "needs_migration", plan: {...}}``
        and does NOT touch hal0.toml or files. The UI renders a
        confirmation modal.
      * **Apply** (``migrate=true`` OR no move needed):
        1. Move files (if needed) atomically.
        2. Persist hal0.toml.

      The order is move-first / persist-last so a failed move leaves
      the prior config + bytes in place. Slot containers observe the
      new path on their next restart.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")

    raw_path = body.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise BadRequest(
            "'path' must be a non-empty absolute path string",
            code="config.invalid",
        )
    path = raw_path.strip()
    migrate = bool(body.get("migrate", False))

    candidate = Path(path)
    if not candidate.is_absolute():
        raise BadRequest(
            f"store path {path!r} must be absolute",
            code="config.invalid",
            details={"path": path},
        )
    if not candidate.exists():
        raise BadRequest(
            f"store path {path!r} does not exist",
            code="models.store_missing",
            details={"path": path},
        )
    if not candidate.is_dir():
        raise BadRequest(
            f"store path {path!r} is not a directory",
            code="models.store_not_directory",
            details={"path": path},
        )
    if not os.access(candidate, os.R_OK):
        raise BadRequest(
            f"store path {path!r} is not readable",
            code="models.store_unreadable",
            details={"path": path},
        )
    if not os.access(candidate, os.W_OK):
        raise BadRequest(
            f"store path {path!r} is not writable",
            code="models.store_unwritable",
            details={"path": path},
        )

    current = getattr(request.app.state, "hal0_config", None)
    if current is None:
        current = load_hal0_config()
    prev_effective = current.models.effective_store()

    plan = plan_migration(current=prev_effective, target=path)

    # Dry-run: confirm needed-migration before touching anything.
    if plan.needed and not migrate:
        return {
            "status": "needs_migration",
            "plan": {
                "source": plan.source,
                "target": plan.target,
                "files_count": plan.files_count,
                "size_bytes": plan.size_bytes,
                "same_filesystem": plan.same_filesystem,
            },
            "state": _store_state_payload(current),
        }

    return await _apply_store_change(
        request=request,
        path=path,
        current=current,
        plan=plan,
    )


@router.post("/models/store/migrate")
async def migrate_model_store(request: Request) -> dict[str, Any]:
    """Explicit migrate-then-apply endpoint.

    Body::

        {"path": "/new/path"}

    Equivalent to POST ``/models/store`` with ``migrate=true``. Exists
    as a standalone route so the UI's confirmation modal has a clean
    URL to fire at after the dry-run round-trip.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest("body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    body["migrate"] = True
    # Replay the body through the regular setter — the JSON parse +
    # validation surface is single-sourced there.
    request._json = body  # type: ignore[attr-defined]

    async def _replayed_json() -> Any:
        return body

    request.json = _replayed_json  # type: ignore[assignment]
    return await set_model_store(request)


async def _apply_store_change(
    *,
    request: Request,
    path: str,
    current: Hal0Config,
    plan: MigrationPlan,
) -> dict[str, Any]:
    """Move files (if any) → persist hal0.toml.

    Failure semantics:
      * Move fails → return 500-style envelope; hal0.toml untouched.
        Files already moved are NOT rolled back — operator can re-run
        with the new path as both source + target (no-op).

    Slot containers mount the store path; they observe the new value on
    their next restart (the apply-plan badge tells the operator).

    Lock scope (#1721). The file move is deliberately kept OUTSIDE the config
    lock and only the read-modify-write is serialized: a real multi-file store
    migration can run for minutes, and holding the one hal0.toml lock across it
    would stall every other config writer — including the API's own settings
    and memory/graph routes — for the whole move. Nothing in the move reads or
    writes hal0.toml, so it needs no protection; what needed fixing was the
    save, which used to persist ``current`` — a snapshot taken BEFORE the move,
    possibly minutes stale and possibly the startup ``app.state`` cache — and
    could therefore erase a concurrent writer's section. The txn below re-reads
    from disk under the lock and applies only ``[models].store`` to that fresh
    config, so a concurrent settings/graph/auth/channel write survives and the
    critical section stays as short as every other writer's.
    """
    migration_result_dict: dict[str, Any] | None = None
    if plan.needed:
        try:
            mig = execute_migration(plan)
        except OSError as exc:
            raise Hal0Error(
                f"migration failed: {exc}",
                code="models.store_migration_failed",
                details={
                    "source": plan.source,
                    "target": plan.target,
                    "error": str(exc),
                },
            ) from exc
        if mig.failed and not mig.moved:
            raise Hal0Error(
                f"migration moved no files; first failure: {mig.failed[0]}",
                code="models.store_migration_failed",
                details={
                    "source": plan.source,
                    "target": plan.target,
                    "failed": mig.failed,
                },
            )
        migration_result_dict = {
            "source": mig.source,
            "target": mig.target,
            "moved": list(mig.moved),
            "failed": list(mig.failed),
        }

    # Persist hal0.toml — short critical section, fresh read (see the
    # lock-scope note in this function's docstring).
    async with hal0_config_txn(request) as txn:
        fresh = txn.config
        new_models_raw = dict(fresh.models.model_dump(mode="python"))
        new_models_raw["store"] = path
        try:
            merged_models = fresh.models.__class__.model_validate(new_models_raw)
        except ValidationError as exc:
            raise ConfigInvalidError(
                "models config failed schema validation",
                details=_validation_error_details(exc),
            ) from exc
        merged = fresh.model_copy(update={"models": merged_models})
        try:
            txn.save(merged)
        except OSError as exc:
            raise Hal0Error(
                f"could not persist hal0 config: {exc}",
                details={"error": str(exc), "errno": getattr(exc, "errno", None)},
            ) from exc

    # A store change usually means model files already live at the new path
    # (that's why the operator pointed hal0 there). Register them NOW —
    # auto_scan_on_start was the only other automatic trigger, so without
    # this the settings change silently did nothing until the next api
    # restart and slots kept failing with "model file not found" against
    # stale registry paths.
    scan_result: dict[str, Any] | None = None
    registry = getattr(request.app.state, "model_registry", None)
    if registry is not None:
        try:
            from hal0.registry.discover import scan_and_register

            scan_result = scan_and_register(registry, merged.models)
        except Exception:
            log.warning("settings.store_rescan_failed", exc_info=True)

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None:
        await event_bus.emit(
            "system.config_save",
            "info",
            "system",
            f"models.store → {path}",
            data={
                "store": path,
                "migrated_files": len(migration_result_dict["moved"])
                if migration_result_dict
                else 0,
                "scan_added": len((scan_result or {}).get("added", [])),
            },
        )

    return {
        "status": "ok",
        "config": _config_to_dict(merged),
        "state": _store_state_payload(merged),
        "migration": migration_result_dict,
        "scan": scan_result,
    }
