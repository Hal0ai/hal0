"""Proxmox-integration settings endpoints (mounted under /api/settings).

Lets operators configure the optional Proxmox API token used by the
dashboard's "Proxmox host" memory segment (see hal0.hardware.pve).
Token is sensitive — it's never echoed back in GET, only ``token_value_set``.

Endpoints:
    GET    /api/settings/proxmox        — config (redacted) + live status
    PUT    /api/settings/proxmox        — write new config (atomic)
    DELETE /api/settings/proxmox        — remove config file
    POST   /api/settings/proxmox/test   — validate a candidate without saving
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, ValidationError

from hal0.api.middleware.auth import require_writer
from hal0.api.middleware.error_codes import Hal0Error
from hal0.hardware import pve

_writer = [Depends(require_writer)]

router = APIRouter()


class ProxmoxConfigBody(BaseModel):
    """Request shape for PUT /api/settings/proxmox.

    ``token_value`` is optional on edit — if omitted, the existing
    on-disk value is preserved so the operator can flip ``verify_ssl``
    or change the host without re-entering the secret.
    """

    model_config = {"extra": "forbid"}

    host: str = Field(min_length=1)
    port: int = Field(default=8006, ge=1, le=65535)
    user: str = Field(min_length=1)
    token_name: str = Field(min_length=1)
    token_value: str | None = None
    verify_ssl: bool = False


class ProxmoxTestBody(BaseModel):
    """Request shape for POST /api/settings/proxmox/test.

    Same as ProxmoxConfigBody but ``token_value`` is required — testing
    a saved config without editing it goes through the live status that
    GET already returns.
    """

    model_config = {"extra": "forbid"}

    host: str = Field(min_length=1)
    port: int = Field(default=8006, ge=1, le=65535)
    user: str = Field(min_length=1)
    token_name: str = Field(min_length=1)
    token_value: str = Field(min_length=1)
    verify_ssl: bool = False


def _validation_details(exc: ValidationError) -> dict[str, str]:
    out: dict[str, str] = {}
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        out[loc or "<root>"] = err.get("msg", "invalid")
    return out


def _load_for_get() -> dict[str, Any]:
    """Read the raw config (token included) to learn what's persisted.

    Used by GET to surface non-secret fields + a ``token_value_set``
    flag. The token itself is never returned in the API response.
    """
    from hal0.hardware.pve import _load_pve_config

    return _load_pve_config() or {}


@router.get("")
async def get_proxmox_config() -> dict[str, Any]:
    """Return the current Proxmox integration state.

    Shape:
        {
          configured: bool,
          host, port, user, token_name, verify_ssl,   # blank when unconfigured
          token_value_set: bool,                       # true iff a token is on disk
          status: <pve_status() output>,               # live cluster snapshot
        }
    """
    raw = _load_for_get()
    status = await pve.pve_status()
    body: dict[str, Any] = {
        "configured": bool(raw),
        "host": raw.get("host", ""),
        "port": raw.get("port", 8006),
        "user": raw.get("user", ""),
        "token_name": raw.get("token_name", ""),
        "verify_ssl": bool(raw.get("verify_ssl", False)),
        "token_value_set": bool(raw.get("token_value")),
        "status": status,
    }
    return body


@router.put("", dependencies=_writer)
async def put_proxmox_config(request: Request) -> dict[str, Any]:
    """Write /etc/hal0/proxmox.json from the supplied body.

    If ``token_value`` is omitted and a file already exists, the
    persisted token is preserved — lets the UI edit other fields
    without re-prompting for the secret.
    """
    try:
        raw = await request.json()
    except Exception as exc:
        raise Hal0Error(
            "request body must be valid JSON",
            details={"error": str(exc)},
            code="request.invalid_json",
        ) from exc
    if not isinstance(raw, dict):
        raise Hal0Error("request body must be a JSON object")

    try:
        body = ProxmoxConfigBody.model_validate(raw)
    except ValidationError as exc:
        raise Hal0Error(
            "proxmox config failed validation",
            code="proxmox.config_invalid",
            details=_validation_details(exc),
            status=400,
        ) from exc

    payload = body.model_dump()
    if not payload.get("token_value"):
        existing = _load_for_get()
        if existing.get("token_value"):
            payload["token_value"] = existing["token_value"]
        else:
            raise Hal0Error(
                "token_value is required when no token is on disk yet",
                code="proxmox.token_required",
                details={"field": "token_value"},
                status=400,
            )

    try:
        pve.save_pve_config(payload)
    except OSError as exc:
        raise Hal0Error(
            f"could not persist proxmox config: {exc}",
            code="proxmox.write_failed",
            details={"error": str(exc), "errno": getattr(exc, "errno", None)},
        ) from exc

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None:
        await event_bus.emit(
            "system.proxmox_save",
            "info",
            "system",
            "proxmox integration saved",
            data={"host": payload["host"], "user": payload["user"]},
        )
    return await get_proxmox_config()


@router.delete("", dependencies=_writer)
async def delete_proxmox_config(request: Request) -> dict[str, Any]:
    """Remove the Proxmox config file (returns to 'not configured')."""
    try:
        existed = pve.delete_pve_config()
    except OSError as exc:
        raise Hal0Error(
            f"could not delete proxmox config: {exc}",
            code="proxmox.delete_failed",
            details={"error": str(exc), "errno": getattr(exc, "errno", None)},
        ) from exc

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None and existed:
        await event_bus.emit(
            "system.proxmox_remove",
            "info",
            "system",
            "proxmox integration removed",
            data={},
        )
    return {"configured": False, "existed": existed}


@router.post("/test", dependencies=_writer)
async def test_proxmox_config(request: Request) -> dict[str, Any]:
    """Validate a candidate config WITHOUT writing it.

    Used by the Settings UI's 'Test connection' button so operators
    can verify creds before clicking Save.
    """
    try:
        raw = await request.json()
    except Exception as exc:
        raise Hal0Error(
            "request body must be valid JSON",
            details={"error": str(exc)},
            code="request.invalid_json",
        ) from exc
    if not isinstance(raw, dict):
        raise Hal0Error("request body must be a JSON object")

    try:
        body = ProxmoxTestBody.model_validate(raw)
    except ValidationError as exc:
        raise Hal0Error(
            "proxmox config failed validation",
            code="proxmox.config_invalid",
            details=_validation_details(exc),
            status=400,
        ) from exc

    return await pve.pve_test(body.model_dump())


__all__ = ["router"]
