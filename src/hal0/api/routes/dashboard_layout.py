"""Dashboard layout persistence endpoints (mounted under /api/user).

Single-operator LAN device — no auth.  One layout file per install.

Endpoints:
    GET  /api/user/dashboard-layout  — return the saved layout, or {} if none.
    PUT  /api/user/dashboard-layout  — validate, reconcile, persist; 204.

DashLayout schema (v3 — canonical, #1060/#1061 fixed bands):
    v:            int   — must equal 3
    cells:        dict[str, str]  — cellId -> widgetId
    quickActions: bool            — quick-actions strip on/off

Server-side reconcile runs the SAME whitelist rules the client does
(:mod:`hal0.dashboard.layout_v3`, mirroring ``useDashLayout.reconcile``):
every cell must exist and hold a widget from its own ``accepts`` list that is
actually built, otherwise it falls back to that cell's default. Shape-only
validation would let a stale client persist a cell the dashboard can't render.

DashLayout schema (v2 — LEGACY, tolerated not rejected):
    v:        int  — 2
    order:    list[str]          — CardId or "pin:<slotName>" keys
    enabled:  dict[str, bool]    — CardId -> on/off
    spans:    dict[str, int]     — LayoutKey -> column span (clamped [1,12])
    pinned:   list[str]          — pinned slot names

v2 is kept accepted so a cached pre-#1061 UI bundle doesn't start 422-ing, and
so a v2 file already on disk keeps round-tripping under the v2 rules. The
client's own ``reconcile()`` fail-softs an unrecognised payload to its default
layout, so an operator holding a pre-#1061 file sees defaults, not an error.

HAL0-SUNSET: v1.2 — drop the v2 branch once no cached pre-#1061 UI bundle can
still be in a browser. v3 is then the only accepted body.

Unknown CardIds in ``enabled`` or ``order`` are rejected with 422; so is any
version that is neither 3 nor 2.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from hal0.api.middleware.error_codes import Hal0Error
from hal0.dashboard import layout_store, layout_v3

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


class DashLayoutV3(BaseModel):
    """Validated dashboard layout body (v3 — the canonical schema).

    Shape only. The whitelist rules (does this cell exist, does it accept this
    widget, is that widget built) live in :mod:`hal0.dashboard.layout_v3` and
    run as a reconcile, not a rejection — the same fail-soft the client applies
    on load, so a widget that ships later can't 422 an operator's saved layout.

    ``quickActions`` keeps the client's camelCase on the wire; the alias lets
    the attribute stay snake_case in Python.
    """

    model_config = ConfigDict(populate_by_name=True)

    v: int
    cells: dict[str, str] = {}
    quick_actions: bool = Field(default=True, alias="quickActions")

    @model_validator(mode="after")
    def _check_version(self) -> DashLayoutV3:
        if self.v != layout_v3.LAYOUT_VERSION:
            raise ValueError(f"layout version must be 3, got {self.v!r}")
        return self

    def to_payload(self) -> dict[str, Any]:
        return {"v": self.v, "cells": self.cells, "quickActions": self.quick_actions}


class DashLayout(BaseModel):
    """Validated dashboard layout body (v2 — superseded, still tolerated).

    HAL0-SUNSET: v1.2 — see the module docstring.
    """

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
    """Return the saved dashboard layout, or ``{}`` when none has been saved.

    ``layout_store.reconcile`` dispatches on the STORED payload's own version,
    so a v3 file comes back as v3 and a pre-#1061 v2 file still comes back
    reconciled under the v2 pin/span rules (#1460).
    """
    raw = layout_store.load()
    if not raw:
        return {}
    slot_names = await _get_slot_names(request)
    return layout_store.reconcile(raw, slot_names)


@router.put("/dashboard-layout", status_code=204)
async def put_dashboard_layout(request: Request) -> Response:
    """Validate, reconcile, and persist the dashboard layout.

    Dispatches on the body's ``v``: 3 is the canonical schema, 2 is still
    tolerated, anything else is a 422. Returns 204 No Content on success and
    422 with ``code: "layout.invalid"`` on schema/validation errors.
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

    version = body.get("v")
    if version == layout_v3.LAYOUT_VERSION:
        try:
            layout_body = DashLayoutV3.model_validate(body)
        except ValidationError as exc:
            raise LayoutInvalidError(
                "dashboard layout failed schema validation",
                details=_validation_error_details(exc),
            ) from exc
        # Reconcile against the cell whitelists, not just the JSON shape: an
        # out-of-whitelist or not-yet-built widget falls back to that cell's
        # default rather than being persisted and handed back on the next GET.
        layout_store.save(layout_v3.reconcile(layout_body.to_payload()))
        return Response(status_code=204)

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
