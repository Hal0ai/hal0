"""Settings (config) endpoints (mounted under /api/settings).

Typed read/write of ``/etc/hal0/hal0.toml`` (or the HAL0_HOME-rooted
override). All writes go through ``hal0.config.loader.save_hal0_config``
which uses the same tempfile+fsync+os.replace pattern as
``write_env_atomic`` — never a partial-write.

Endpoints:
    GET  /api/settings            — current parsed Hal0Config as a dict.
    PUT  /api/settings            — partial update; deep-merged into the
                                    existing config, validated against
                                    the pydantic schema, then atomically
                                    written.
    POST /api/settings/reload     — re-read /etc/hal0/hal0.toml from disk
                                    into the running process.
    GET  /api/settings/schema     — pydantic JSON schema of Hal0Config
                                    so the dashboard can render typed
                                    fields without hard-coding shapes.

Validation failures return the structured error envelope with
``code: "config.invalid"`` and ``details`` containing a per-field
``{field_path: message}`` map.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import ValidationError

from hal0.api.middleware.error_codes import Hal0Error
from hal0.config.loader import load_hal0_config, save_hal0_config
from hal0.config.schema import Hal0Config

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
    return cfg.model_dump(mode="json")


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
    return _config_to_dict(merged)


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
