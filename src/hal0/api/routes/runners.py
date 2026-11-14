"""Runner-specific dashboard endpoints beyond the generic knob table.

``GET /api/runners/xdna/knobs`` — the xdna runner's env/pmode/host_xrt
schema (xdna-runner plan §3), read straight off :mod:`hal0.runners.knobs`
so the dashboard never hardcodes the ``GGML_XDNA_*`` set, the valid
``pmode`` values, or the ``host_xrt`` doc string.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Request

from hal0.api.middleware.error_codes import NotFound
from hal0.runners.knobs import xdna_knob_schema

router = APIRouter()


@router.get("/xdna/knobs")
async def get_xdna_knobs() -> dict[str, Any]:
    """Return the xdna runner's knob schema for the dashboard's knob table."""
    return xdna_knob_schema()


def _is_gguf_llm(model: Any) -> bool:
    """GGUF chat row: llama-server ``backends`` decide, ``.gguf`` suffix only as fallback.

    Hub-cache and add-from-path rows store an extensionless sha-named blob
    (#1838), so the suffix alone would drop them; halogen/FLM rows carry other
    backend names and are excluded.
    """
    from hal0.registry.detect import _GGUF_BACKENDS
    from hal0.services.models_service import dispatch_type

    if dispatch_type(model.id, capabilities=getattr(model, "capabilities", None)) != "llm":
        return False
    backends = [str(b) for b in (getattr(model, "backends", None) or [])]
    if backends:
        return any(b in _GGUF_BACKENDS for b in backends)
    path = str(getattr(model, "path", "") or "")
    return path.lower().endswith(".gguf") and not os.path.isdir(path)


@router.get("/xdna/fit")
async def get_xdna_fit(request: Request, model: str | None = None) -> dict[str, Any]:
    """Fused vs per-op xdna fit for every registered GGUF chat model.

    A row whose file is missing/unreadable/not GGUF is still returned, with
    ``xdna_path: null`` and the reason -- never a 500.
    """
    from hal0.slots.model_fit import xdna_model_fit_cached

    registry = getattr(request.app.state, "model_registry", None)
    if registry is None:
        if model is not None:
            raise NotFound(f"unknown model {model!r}", code="runners.model_not_found")
        return {"models": {}}
    rows = [m for m in registry.list() if _is_gguf_llm(m)]
    if model is not None:
        rows = [m for m in rows if m.id == model]
        if not rows:
            raise NotFound(f"unknown model {model!r}", code="runners.model_not_found")
    out: dict[str, dict[str, Any]] = {}
    for m in rows:
        try:
            fit = xdna_model_fit_cached(m.path)
            out[m.id] = {"xdna_path": fit.xdna_path, "reason": fit.reason}
        except (OSError, ValueError) as exc:
            out[m.id] = {"xdna_path": None, "reason": str(exc) or type(exc).__name__}
    return {"models": out}


__all__ = ["router"]
