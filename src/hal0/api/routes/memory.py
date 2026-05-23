"""Memory endpoints — ADR-0014 graph-extraction gate + status.

Mounted under ``/api/memory/*``. The dashboard's Memory tab + the
``hal0 memory graph {enable,disable,status}`` CLI both read + write
through this surface; there is no other writer for ``[memory.graph]``
so a swap-flip from either client lands atomically through the same
``save_hal0_config`` pipeline.

The actual cognify dispatch lives in :class:`hal0.memory.CogneeWrapper`;
this module is the thin HTTP veneer that:

  - Returns ``graph_status()`` (enabled / route / counters / last-built).
  - Validates the toggle payload against :class:`MemoryGraphConfig`.
  - Persists to ``hal0.toml`` via the existing atomic writer.
  - Flips the live wrapper so callers don't need a restart.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import ValidationError

from hal0.api.middleware.error_codes import Hal0Error
from hal0.config.loader import load_hal0_config, save_hal0_config
from hal0.config.schema import GraphUpstreamConfig, MemoryGraphConfig

router = APIRouter()


class MemoryGraphConfigInvalid(Hal0Error):
    """Schema validation failure for ``[memory.graph]``."""

    code = "config.memory_graph_invalid"
    status = 400


class MemoryUnavailable(Hal0Error):
    """The Cognee wrapper failed to initialise at boot.

    Returned when the API got far enough to mount the router but the
    underlying memory engine isn't usable — e.g. a cognee import
    failure on a stripped-down install. Letting this surface as a 503
    instead of a generic 500 means the dashboard can paint a clear
    "Memory engine unavailable" state rather than a red toast.
    """

    code = "memory.unavailable"
    status = 503


def _wrapper(request: Request) -> Any:
    """Return the live :class:`CogneeWrapper` or raise 503."""
    wrapper = getattr(request.app.state, "memory_wrapper", None)
    if wrapper is None:
        raise MemoryUnavailable("memory engine is not available on this hal0 instance")
    return wrapper


def _validation_error_details(exc: ValidationError) -> dict[str, str]:
    out: dict[str, str] = {}
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        out[loc or "<root>"] = err.get("msg", "invalid")
    return out


# ── GET /api/memory/graph/status ───────────────────────────────────────────


@router.get("/graph/status")
async def graph_status(request: Request) -> dict[str, Any]:
    """Return live + configured graph-extraction state.

    Response shape (stable contract — the dashboard depends on every
    key being present)::

        {
          "enabled":        bool,
          "route":          "upstream" | "primary" | "agent",
          "upstream":       {"provider": str, "model": str} | None,
          "in_flight":      int,
          "builds_ok":      int,
          "errors":         int,
          "last_built_at":  iso8601 | None,
          "last_error":     str | None,
        }

    ``upstream`` is the configured upstream block (NOT the live
    wrapper's — wrappers don't hold it). Returned alongside the
    wrapper's runtime counters so the dashboard can render one panel
    from one fetch.
    """
    wrapper = _wrapper(request)
    cfg = load_hal0_config()
    upstream_payload: dict[str, str] | None = None
    if cfg.memory.graph.upstream is not None:
        upstream_payload = {
            "provider": cfg.memory.graph.upstream.provider,
            "model": cfg.memory.graph.upstream.model,
        }
    status = wrapper.graph_status()
    status["upstream"] = upstream_payload
    return status


# ── PUT /api/memory/graph ──────────────────────────────────────────────────


@router.put("/graph")
async def update_graph_config(request: Request) -> dict[str, Any]:
    """Replace the ``[memory.graph]`` section.

    Body shape: any subset of :class:`MemoryGraphConfig` fields. The
    merge preserves un-set fields (e.g. PATCH-style "flip enabled but
    keep route") because dashboards typically send the delta, not the
    whole block.

    On success persists ``hal0.toml`` atomically AND flips the live
    wrapper so subsequent ``memory_add`` calls observe the new gate
    without a restart.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise Hal0Error("request body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise Hal0Error("request body must be a JSON object")

    wrapper = _wrapper(request)
    cfg = load_hal0_config()
    current_raw = cfg.memory.graph.model_dump(mode="python")
    # Merge upstream block one level deep — a top-level dict merge
    # would let a caller's partial upstream payload (e.g. only model)
    # blow away the configured provider.
    if "upstream" in body and isinstance(body["upstream"], dict):
        current_upstream = current_raw.get("upstream") or {}
        body["upstream"] = {**current_upstream, **body["upstream"]}
    merged_raw = {**current_raw, **body}

    try:
        new_cfg = MemoryGraphConfig.model_validate(merged_raw)
    except ValidationError as exc:
        raise MemoryGraphConfigInvalid(
            "memory.graph config failed schema validation",
            details=_validation_error_details(exc),
        ) from exc

    cfg.memory.graph = new_cfg
    try:
        save_hal0_config(cfg)
    except OSError as exc:
        raise Hal0Error(
            f"could not persist hal0 config: {exc}",
            details={"error": str(exc), "errno": getattr(exc, "errno", None)},
        ) from exc

    # Flip the live wrapper so the very next memory_add observes the
    # new gate. ADR-0014 §6: disable cancels in-flight builds —
    # handled inside set_graph_enabled.
    try:
        wrapper.set_graph_enabled(new_cfg.enabled, route=new_cfg.route)
    except ValueError as exc:
        raise MemoryGraphConfigInvalid(str(exc)) from exc

    out = new_cfg.model_dump(mode="json")
    # Echo the live status so the dashboard's optimistic-update path
    # gets the counters in the same round trip without a second fetch.
    out["status"] = wrapper.graph_status()
    return out


# ── REST shims for /api/memory/{add,search,list,delete} (#302) ─────────────
#
# Plain-HTTP veneer over CogneeWrapper for callers that don't speak the
# MCP protocol (Hermes bootstrap CLI, dashboard Agents > Peers tab,
# in-process scripts). The MCP transport at /mcp/memory/mcp stays
# available for proper MCP clients; these routes are a parallel path
# for the much-larger HTTP-only audience.
#
# Why: #302 surfaced that the bootstrap + CLI + dashboard were all
# POSTing to /mcp/memory as if it were one-shot JSON-RPC. Real FastMCP
# transport needs initialize + session-tagged subsequent calls — that's
# work for a future MCP-SDK-client refactor. Until then, REST shims are
# the cheapest unblock so identity cards actually get written.


@router.post("/add")
async def memory_add(request: Request) -> dict[str, Any]:
    """Add a memory item. Body: ``{text, dataset?, tags?, source?, metadata?}``.

    Returns ``{id, timestamp}`` from :meth:`CogneeWrapper.add`. The
    ``dataset`` defaults to ``"shared"``; tags + metadata are
    free-form lists / dict respectively.
    """
    body = await _read_json_body(request)
    text = body.get("text")
    if not isinstance(text, str) or not text:
        raise Hal0Error(
            "memory_add requires 'text' (non-empty string)",
            details={"path": "/api/memory/add"},
        )
    wrapper = _wrapper(request)
    return await wrapper.add(
        text=text,
        dataset=body.get("dataset", "shared"),
        tags=body.get("tags") or [],
        source=body.get("source"),
        metadata=body.get("metadata") or {},
    )


@router.post("/search")
async def memory_search(request: Request) -> dict[str, Any]:
    """Search memory. Body: ``{query, limit?, dataset?, tags?, before?, after?}``.

    Returns ``{items: [MemoryRecord, ...]}`` — wrapped in an envelope so
    we can add ``next_cursor`` / counters later without breaking clients.
    """
    body = await _read_json_body(request)
    query = body.get("query")
    if not isinstance(query, str) or not query:
        raise Hal0Error(
            "memory_search requires 'query' (non-empty string)",
            details={"path": "/api/memory/search"},
        )
    wrapper = _wrapper(request)
    items = await wrapper.search(
        query=query,
        limit=int(body.get("limit", 10)),
        dataset=body.get("dataset", "shared"),
        tags=body.get("tags") or [],
        before=body.get("before"),
        after=body.get("after"),
    )
    return {"items": items}


@router.get("/list")
async def memory_list(
    request: Request,
    dataset: str = "shared",
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Paginated list. Returns ``{items: [...], next_cursor: str | null}``."""
    wrapper = _wrapper(request)
    return await wrapper.list_items(dataset=dataset, cursor=cursor, limit=limit)


@router.post("/delete")
async def memory_delete(request: Request) -> dict[str, int]:
    """Delete by id. Body: ``{ids: [...]}``. Returns ``{deleted: int}``."""
    body = await _read_json_body(request)
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        raise Hal0Error(
            "memory_delete requires 'ids' (non-empty list)",
            details={"path": "/api/memory/delete"},
        )
    wrapper = _wrapper(request)
    return await wrapper.delete(ids=ids)


async def _read_json_body(request: Request) -> dict[str, Any]:
    """Tolerant JSON body parser (mirrors v1.py:_read_json_body)."""
    try:
        body = await request.json()
    except Exception as exc:
        raise Hal0Error(
            "request body must be valid JSON",
            details={"error": str(exc)},
        ) from exc
    if not isinstance(body, dict):
        raise Hal0Error("request body must be a JSON object")
    return body


# ── Helper exports for tests ────────────────────────────────────────────────


__all__ = [
    "GraphUpstreamConfig",
    "MemoryGraphConfig",
    "MemoryGraphConfigInvalid",
    "MemoryUnavailable",
    "router",
]
