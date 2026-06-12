"""Settings (config) endpoints (mounted under /api/settings).

Typed read/write of ``/etc/hal0/hal0.toml`` (or the HAL0_HOME-rooted
override). All writes go through ``hal0.config.loader.save_hal0_config``
which uses the same tempfile+fsync+os.replace pattern as
``write_env_atomic`` — never a partial-write.

Endpoints:
    GET  /api/settings              — current parsed Hal0Config as a dict.
    PUT  /api/settings              — partial update; deep-merged into the
                                      existing config, validated against
                                      the pydantic schema, then atomically
                                      written. Response includes
                                      ``_hal0.apply_plan`` so the UI can
                                      render the right effect badge
                                      without a second round-trip.
    POST /api/settings/reload       — re-read /etc/hal0/hal0.toml from
                                      disk into the running process.
    GET  /api/settings/schema       — pydantic JSON schema of Hal0Config
                                      so the dashboard can render typed
                                      fields without hard-coding shapes.
    GET  /api/settings/apply-plan   — full key→apply-class registry the
                                      dashboard mounts once to render
                                      per-row effect badges (#552).

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
from hal0.api._settings_apply import APPLY_CLASSES, REGISTRY, apply_plan, get_registry
from hal0.api.middleware.error_codes import BadRequest, Hal0Error
from hal0.config.loader import load_hal0_config, save_hal0_config
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


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge: patch wins, but nested dicts are merged not replaced.

    Lists and scalars are replaced wholesale (no append/extend semantics)
    because the schema doesn't define list identities — the caller's intent
    when sending ``{"slots": {"port_range_end": 8090}}`` is to set that
    one knob, not to clobber the rest of ``slots``.
    """
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


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

    current = getattr(request.app.state, "hal0_config", None)
    if current is None:
        current = load_hal0_config()

    merged_raw = _deep_merge(current.model_dump(mode="python"), body)

    try:
        merged = Hal0Config.model_validate(merged_raw)
    except ValidationError as exc:
        raise ConfigInvalidError(
            "hal0 config failed schema validation",
            details=_validation_error_details(exc),
        ) from exc

    # Atomic write via the loader's write_toml_atomic-backed helper.
    try:
        save_hal0_config(merged)
    except OSError as exc:
        raise Hal0Error(
            f"could not persist hal0 config: {exc}",
            details={"error": str(exc), "errno": getattr(exc, "errno", None)},
        ) from exc

    request.app.state.hal0_config = merged
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
    # additive (#545).
    #
    # The touched-keys list is built from the *top-level* keys the PATCH
    # carried, not from the merged body's leaf paths: the apply-plan
    # registry keys on dotted paths (``slots.max_slots``) which never
    # appear at the top level of a PUT, so the partition mostly
    # surfaces empty buckets when the operator edits via the generic
    # endpoint. The shape stays consistent so the UI can render
    # uniform badges regardless of which endpoint produced the
    # response.
    # Build the apply plan from request body keys that the registry
    # directly knows about. Top-level section keys (e.g. "telemetry",
    # "dispatcher") are NOT in the registry — only dotted leaf paths
    # like "telemetry.enabled" are. Filtering them out before calling
    # apply_plan() prevents them from landing in "unknown" (they're
    # not unknown, just coarse-grained section names). The result is
    # all-empty buckets for a generic section-level PUT, which the UI
    # renders without any effect badge — correct, since the operator
    # didn't target a specific leaf key.
    touched_registry_keys = [k for k in body if k in REGISTRY]
    config_view = _config_to_dict(merged)
    config_view["_hal0"] = {"apply_plan": apply_plan(touched_registry_keys)}
    return config_view


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

    # Persist hal0.toml.
    new_models_raw = dict(current.models.model_dump(mode="python"))
    new_models_raw["store"] = path
    try:
        merged_models = current.models.__class__.model_validate(new_models_raw)
    except ValidationError as exc:
        raise ConfigInvalidError(
            "models config failed schema validation",
            details=_validation_error_details(exc),
        ) from exc
    merged = current.model_copy(update={"models": merged_models})
    try:
        save_hal0_config(merged)
    except OSError as exc:
        raise Hal0Error(
            f"could not persist hal0 config: {exc}",
            details={"error": str(exc), "errno": getattr(exc, "errno", None)},
        ) from exc
    request.app.state.hal0_config = merged

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
            },
        )

    return {
        "status": "ok",
        "config": _config_to_dict(merged),
        "state": _store_state_payload(merged),
        "migration": migration_result_dict,
    }
