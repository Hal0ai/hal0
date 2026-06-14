"""Dashboard layout persistence endpoints (mounted under /api/user).

Single-operator LAN device — no auth.  One layout file per install.

Endpoints:
    GET  /api/user/dashboard-layout  — return the saved layout, or {} if none.
    PUT  /api/user/dashboard-layout  — validate, reconcile, persist; 204.

DashLayout schema (v2):
    v:        int  — must equal 2
    order:    list[str]          — CardId or "pin:<slotName>" keys
    enabled:  dict[str, bool]    — CardId -> on/off
    spans:    dict[str, int]     — LayoutKey -> column span (clamped [1,12])
    pinned:   list[str]          — pinned slot names

Unknown CardIds in ``enabled`` or ``order`` are rejected with 422.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ValidationError, field_validator, model_validator

from hal0.api.middleware.error_codes import Hal0Error
from hal0.dashboard import layout_store

router = APIRouter()

# ── Valid card ids ─────────────────────────────────────────────────────────────

_VALID_CARD_IDS: frozenset[str] = frozenset(
    [
        "slots",
        "memory",
        "throughput",
        "quickchat",
        "services",
        "utilization",
        "attention",
        "slottrack",
        "approvals",
        "power",
        "scheduler",
    ]
)


def _is_valid_layout_key(key: str) -> bool:
    """Return True if *key* is a valid CardId or a ``pin:<anything>`` key."""
    if key.startswith("pin:"):
        return True
    return key in _VALID_CARD_IDS


# ── Pydantic schema ────────────────────────────────────────────────────────────


class DashLayout(BaseModel):
    """Validated dashboard layout body (v2)."""

    v: int
    order: list[str]
    enabled: dict[str, bool]
    spans: dict[str, int]
    pinned: list[str]

    @model_validator(mode="after")
    def _check_version(self) -> DashLayout:
        if self.v != 2:
            raise ValueError(f"layout version must be 2, got {self.v!r}")
        return self

    @field_validator("order")
    @classmethod
    def _check_order_keys(cls, v: list[str]) -> list[str]:
        bad = [k for k in v if not _is_valid_layout_key(k)]
        if bad:
            raise ValueError(f"unknown layout keys in order: {bad!r}")
        return v

    @field_validator("enabled")
    @classmethod
    def _check_enabled_keys(cls, v: dict[str, bool]) -> dict[str, bool]:
        bad = [k for k in v if k not in _VALID_CARD_IDS]
        if bad:
            raise ValueError(f"unknown card ids in enabled: {bad!r}")
        return v


# ── Error type ─────────────────────────────────────────────────────────────────


class LayoutInvalidError(Hal0Error):
    """Schema validation failure for the dashboard layout body."""

    code = "layout.invalid"
    status = 422


def _validation_error_details(exc: ValidationError) -> dict[str, str]:
    out: dict[str, str] = {}
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        out[loc or "<root>"] = err.get("msg", "invalid")
    return out


# ── Slot name helper ───────────────────────────────────────────────────────────


async def _get_slot_names(request: Request) -> list[str]:
    """Return current slot names from app.state.slot_manager; empty on error."""
    try:
        sm = getattr(request.app.state, "slot_manager", None)
        if sm is None:
            return []
        slots = await sm.list()
        return [s.name for s in slots]
    except Exception:
        return []


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get("/dashboard-layout")
async def get_dashboard_layout(request: Request) -> dict[str, Any]:
    """Return the saved dashboard layout, or ``{}`` when none has been saved."""
    raw = layout_store.load()
    if not raw:
        return {}
    slot_names = await _get_slot_names(request)
    return layout_store.reconcile(raw, slot_names)


@router.put("/dashboard-layout", status_code=204)
async def put_dashboard_layout(request: Request) -> Response:
    """Validate, reconcile, and persist the dashboard layout.

    Returns 204 No Content on success.
    Returns 422 with ``code: "layout.invalid"`` on schema/validation errors.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise Hal0Error(
            "request body must be valid JSON",
            details={"error": str(exc)},
        ) from exc

    if not isinstance(body, dict):
        raise Hal0Error("request body must be a JSON object")

    try:
        layout = DashLayout.model_validate(body)
    except ValidationError as exc:
        raise LayoutInvalidError(
            "dashboard layout failed schema validation",
            details=_validation_error_details(exc),
        ) from exc

    slot_names = await _get_slot_names(request)
    reconciled = layout_store.reconcile(layout.model_dump(), slot_names)
    layout_store.save(reconciled)

    return Response(status_code=204)
