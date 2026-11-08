"""Runner-specific dashboard endpoints beyond the generic knob table.

``GET /api/runners/xdna/knobs`` — the xdna runner's env/pmode/host_xrt
schema (xdna-runner plan §3), read straight off :mod:`hal0.runners.knobs`
so the dashboard never hardcodes the ``GGML_XDNA_*`` set, the valid
``pmode`` values, or the ``host_xrt`` doc string.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from hal0.runners.knobs import xdna_knob_schema

router = APIRouter()


@router.get("/xdna/knobs")
async def get_xdna_knobs() -> dict[str, Any]:
    """Return the xdna runner's knob schema for the dashboard's knob table."""
    return xdna_knob_schema()


__all__ = ["router"]
