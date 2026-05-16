"""External upstream LLM providers (mounted under /api).

Endpoints:
  GET    /api/upstreams                  — list registered routing targets
  GET    /api/upstreams/{name}           — single upstream
  POST   /api/upstreams/{name}/test      — probe reachability + auth
  GET    /api/providers/catalog          — static integration catalog
  GET    /api/providers                  — configured providers (alias of upstreams)

Write paths (create/update/delete) are intentionally deferred — providers are
authored by editing /etc/hal0/upstreams.toml and reloading, which the
``hal0 config reload`` CLI surfaces. The Phase 1 design (PLAN §6) treats the
TOML files as the source of truth; a fully reactive editor lands later.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from hal0.api.middleware.auth import require_writer
from hal0.api.middleware.error_codes import Hal0Error
from hal0.upstreams.integrations import get_catalog
from hal0.upstreams.registry import UpstreamNotFound

# See slots.py for the writer-gate rationale.
_writer = [Depends(require_writer)]

router = APIRouter()


class UpstreamNotFoundHTTP(Hal0Error):
    code = "upstream.not_found"
    status = 404


def _serialize_upstream(u: Any, *, last_models: list[str] | None = None) -> dict[str, Any]:
    """Project an Upstream dataclass into the dashboard-friendly dict.

    Mirrors /api/slots' shape — `name`, `kind`, `url`, plus a sanitized
    auth descriptor that never leaks credential values.
    """
    return {
        "name": u.name,
        "kind": u.kind,
        "url": u.url,
        "auth_style": u.auth_style,
        "auth_value_env": u.auth_value_env,  # env-var *name*, not value
        "auth_configured": bool(u.auth_value_env),
        "timeout_seconds": u.timeout_seconds,
        "slot_name": u.slot_name,
        "warmup_strategy": u.warmup_strategy,
        "advertise_models": u.advertise_models,
        "models": last_models or [],
    }


@router.get("/upstreams")
async def list_upstreams(request: Request) -> list[dict[str, Any]]:
    """Return all configured upstreams (slots + remote providers).

    Pulled from ``app.state.upstreams`` — the same registry the dispatcher
    routes through. Each entry includes the cached model list when one has
    been fetched (typically primed on first /api/health hit).
    """
    upstreams = request.app.state.upstreams
    model_cache: dict[str, list[str]] = getattr(request.app.state, "upstream_models", {})
    return [_serialize_upstream(u, last_models=model_cache.get(u.name)) for u in upstreams.list()]


@router.get("/upstreams/{name}")
async def get_upstream(name: str, request: Request) -> dict[str, Any]:
    """Return a single upstream by name (404 if not registered)."""
    upstreams = request.app.state.upstreams
    u = upstreams.get(name)
    if u is None:
        raise UpstreamNotFoundHTTP(f"upstream {name!r} not found", {"name": name})
    model_cache: dict[str, list[str]] = getattr(request.app.state, "upstream_models", {})
    return _serialize_upstream(u, last_models=model_cache.get(name))


@router.post("/upstreams/{name}/test", dependencies=_writer)
async def test_upstream(name: str, request: Request) -> dict[str, Any]:
    """Probe ``/v1/models`` on ``name`` and return a reachability report.

    Shape: ``{ok, status?, latency_ms, models_count?, error?}``. Used by the
    Slots/Upstreams settings view to give a "test connection" button.
    """
    upstreams = request.app.state.upstreams
    try:
        return await upstreams.test(name)
    except UpstreamNotFound as exc:
        raise UpstreamNotFoundHTTP(str(exc), {"name": name}) from exc


@router.get("/providers/catalog")
async def providers_catalog() -> dict[str, dict[str, Any]]:
    """Return the static integration catalog (built-in upstream templates).

    Used by the "Add upstream" form to populate the dropdown of known
    providers (Anthropic, OpenAI, OpenRouter, hal0 self, custom, …).
    """
    return get_catalog()


@router.get("/providers")
async def list_providers(request: Request) -> list[dict[str, Any]]:
    """Return remote (kind=='remote') upstreams only.

    The dashboard's Providers tab is essentially "show me the third-party
    catalogs I've wired up" — slot upstreams are managed under /api/slots.
    """
    upstreams = request.app.state.upstreams
    model_cache: dict[str, list[str]] = getattr(request.app.state, "upstream_models", {})
    return [
        _serialize_upstream(u, last_models=model_cache.get(u.name))
        for u in upstreams.list()
        if u.kind != "slot"
    ]
