"""Hindsight engine admin surface — allowlisted forward under /api/memory.

The Hindsight daemon is loopback-only on CT105 (:9177) by design; hal0-api is
its sole front door. This router exposes the slice of the Hindsight REST API
(0.7.x, ``/v1/default/banks/{bank}/...``) the dashboard's Memory surface needs:
bank CRUD + stats + timeseries, graph + entity browse, memory/document browse,
recall/reflect consoles, mental models, directives, async operations, and
bank template export/import.

Design:

* ``GET /api/memory/engine`` is a fail-soft aggregator (never 5xx) so the
  dashboard can always paint an engine card — mirrors the comfyui status
  aggregator pattern.
* Everything else is a table-driven allowlisted passthrough through
  :meth:`HindsightRestClient.request_json` — query params and JSON bodies are
  forwarded verbatim, responses returned verbatim. The allowlist (not a
  wildcard proxy) keeps the surface reviewable and the OpenAPI doc honest.
* Gating: provider missing → 503 ``memory.unavailable`` (house seam);
  provider without a Hindsight client (cognee/pgvector engines) → 501
  ``memory.engine_unsupported``.
* Upstream errors: 4xx pass through status with code ``memory.engine_error``;
  upstream 5xx → 502; transport failure → 503 ``memory.engine_unreachable``.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx
from fastapi import APIRouter, Request

from hal0.api.routes import _memory_subgraph as _sg
from hal0.api.routes.memory import MemoryUnavailable
from hal0.errors import BadRequest, Hal0Error, NotFound, UnprocessableEntity

router = APIRouter()

#: Bank ids come from namespace_to_bank() (``private__<agent>``) or operator
#: input — kebab/snake alphanumerics only, no dots (blocks path tricks).
_BANK_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$")

#: Sub-resource ids (documents, operations, entities, …) — UUIDs and slugs;
#: dots allowed but never as a whole traversal segment.
_SEG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,255}$")


class MemoryEngineUnsupported(Hal0Error):
    """The active memory engine has no Hindsight admin surface."""

    code = "memory.engine_unsupported"
    status = 501


class MemoryEngineUnreachable(Hal0Error):
    """hal0-api could not reach the Hindsight daemon."""

    code = "memory.engine_unreachable"
    status = 503


class MemoryEngineError(Hal0Error):
    """Hindsight answered with an error; status mirrors upstream (4xx) or 502."""

    code = "memory.engine_error"
    status = 502


def _client(request: Request) -> Any:
    provider = getattr(request.app.state, "memory_provider", None)
    if provider is None:
        raise MemoryUnavailable("memory engine is not available on this hal0 instance")
    client = getattr(provider, "hindsight_client", None)
    if client is None:
        raise MemoryEngineUnsupported("the memory admin surface requires the hindsight engine")
    return client


def _validate_segments(path_params: dict[str, str]) -> dict[str, str]:
    for name, value in path_params.items():
        pattern = _BANK_RE if name == "bank_id" else _SEG_RE
        if not pattern.match(value) or value.strip(".") == "":
            raise BadRequest(
                f"invalid {name}: {value!r}",
                code="memory.invalid_bank" if name == "bank_id" else "memory.invalid_path",
            )
    return path_params


async def _read_body(request: Request) -> Any | None:
    raw = await request.body()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise BadRequest("request body must be valid JSON") from exc


async def _forward(
    client: Any,
    method: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    json_body: Any | None = None,
) -> Any:
    try:
        return await client.request_json(method, path, params=params, json_body=json_body)
    except httpx.HTTPStatusError as exc:
        upstream_status = exc.response.status_code
        try:
            detail: Any = exc.response.json()
        except ValueError:
            detail = {"body": exc.response.text[:500]}
        err = MemoryEngineError(
            "memory engine returned an error",
            details={"upstream_status": upstream_status, "upstream": detail},
        )
        if 400 <= upstream_status < 500:
            err.status = upstream_status
        raise err from exc
    except httpx.HTTPError as exc:
        raise MemoryEngineUnreachable(
            "memory engine is unreachable", details={"error": str(exc)}
        ) from exc


# ── GET /api/memory/engine — fail-soft aggregator ──────────────────────────────


async def _probe(client: Any, path: str) -> Any | None:
    try:
        return await client.request_json("GET", path)
    except Exception:
        return None


@router.get("/engine")
async def engine_status(request: Request) -> dict[str, Any]:
    """Engine card payload — never errors, so the dashboard can always render.

    Shape::

        {
          "enabled":     bool,        # memory provider initialised
          "engine":      "hindsight" | null,
          "reachable":   bool,        # daemon answered /version or /v1/default/banks
          "version":     "0.7.2" | null,
          "features":    {...} | null, # 0.7.x feature flags (observations, mcp, …)
          "banks_total": int | null,
        }
    """
    provider = getattr(request.app.state, "memory_provider", None)
    client = getattr(provider, "hindsight_client", None) if provider is not None else None
    if client is None:
        return {
            "enabled": provider is not None,
            "engine": None,
            "reachable": False,
            "version": None,
            "features": None,
            "banks_total": None,
        }
    version, banks = await asyncio.gather(
        _probe(client, "/version"), _probe(client, "/v1/default/banks")
    )
    return {
        "enabled": True,
        "engine": "hindsight",
        "reachable": version is not None or banks is not None,
        "version": (version or {}).get("api_version"),
        "features": (version or {}).get("features"),
        "banks_total": len(banks.get("banks", [])) if isinstance(banks, dict) else None,
    }


# ── composed subgraph endpoint (NOT a passthrough) ─────────────────────────────
#
# Server-side ego / top-K slice so the graph explorer renders a bounded, connected
# view of large banks instead of pulling+normalising the whole graph client-side.
# Mirrors the /engine aggregator: pulls the bank graph once (per-bank TTL cache),
# computes the slice with the pure helpers in _memory_subgraph, and returns the
# existing Cytoscape GraphPayload shape so the client adapter stays unchanged.
# Registered explicitly BEFORE the _FORWARDS loop — never via the table.

#: Module-level per-bank TTL cache singleton (rebound in tests).
_GRAPH_CACHE = _sg.GraphCache()


@router.get("/banks/{bank_id}/graph/subgraph")
async def bank_subgraph(request: Request, bank_id: str) -> dict[str, Any]:
    client = _client(request)
    _validate_segments({"bank_id": bank_id})
    qp = request.query_params
    kind = qp.get("kind", "memories")
    mode = qp.get("mode", "top")
    if kind not in ("memories", "entities"):
        raise UnprocessableEntity(f"invalid kind: {kind!r}", code="memory.invalid_query")
    if mode not in ("ego", "top"):
        raise UnprocessableEntity(f"invalid mode: {mode!r}", code="memory.invalid_query")
    limit = min(int(qp.get("limit", 240)), 500)

    upstream = (
        f"/v1/default/banks/{bank_id}/entities/graph"
        if kind == "entities"
        else f"/v1/default/banks/{bank_id}/graph"
    )
    # narrow the source fetch with forwarded type/q; cache by (bank,kind,type,q)
    src_params: dict[str, str] = {k: qp[k] for k in ("type", "q") if qp.get(k)}
    src_params.setdefault("limit", "2000")  # pull a generous source slab
    cache_key = f"{bank_id}:{kind}:{qp.get('type', '')}:{qp.get('q', '')}"
    graph = _GRAPH_CACHE.peek(cache_key)
    if graph is None:
        graph = await _forward(client, "GET", upstream, params=src_params)
        _GRAPH_CACHE.put(cache_key, graph)

    total_nodes = len(graph.get("nodes", []))
    total_edges = len(graph.get("edges", []))

    if mode == "ego":
        node = qp.get("node")
        if not node:
            raise UnprocessableEntity("ego mode requires ?node=", code="memory.invalid_query")
        depth = min(int(qp.get("depth", 1)), 2)
        keep = _sg.ego_bfs(graph, node, depth=depth, limit=limit)
        if not keep:
            raise NotFound(f"node {node!r} not in bank graph", code="memory.node_not_found")
    else:
        by = qp.get("by") or ("degree" if kind == "entities" else "recency")
        ranked = _sg.rank_by_degree(graph) if by == "degree" else _sg.rank_by_recency(graph)
        top_k = min(int(qp.get("top_k", 200)), 500)
        keep = set(ranked[: min(top_k, limit)])

    sub = _sg.induce_subgraph(graph, keep)
    out: dict[str, Any] = dict(sub)
    out["total_edges"] = total_edges
    out["total_entities" if kind == "entities" else "total_units"] = total_nodes
    out["returned_nodes"] = len(sub["nodes"])
    out["returned_edges"] = len(sub["edges"])
    out["truncated"] = len(sub["nodes"]) < total_nodes
    out["mode"] = mode
    out["center"] = qp.get("node")
    return out


# ── allowlisted passthrough table ──────────────────────────────────────────────
#
# (hal0 method, hal0 path under /api/memory, upstream path template).
# Query params and JSON bodies forward verbatim; see module docstring for the
# error-mapping contract. Upstream paths follow the 0.7.x OpenAPI spec —
# deprecated endpoints (background, entity regenerate) are deliberately absent.

_FORWARDS: tuple[tuple[str, str, str], ...] = (
    # banks
    ("GET", "/banks", "/v1/default/banks"),
    ("PUT", "/banks/{bank_id}", "/v1/default/banks/{bank_id}"),
    ("PATCH", "/banks/{bank_id}", "/v1/default/banks/{bank_id}"),
    ("DELETE", "/banks/{bank_id}", "/v1/default/banks/{bank_id}"),
    ("GET", "/banks/{bank_id}/stats", "/v1/default/banks/{bank_id}/stats"),
    (
        "GET",
        "/banks/{bank_id}/stats/timeseries",
        "/v1/default/banks/{bank_id}/stats/memories-timeseries",
    ),
    ("GET", "/banks/{bank_id}/profile", "/v1/default/banks/{bank_id}/profile"),
    ("PUT", "/banks/{bank_id}/profile", "/v1/default/banks/{bank_id}/profile"),
    ("GET", "/banks/{bank_id}/config", "/v1/default/banks/{bank_id}/config"),
    ("PATCH", "/banks/{bank_id}/config", "/v1/default/banks/{bank_id}/config"),
    ("DELETE", "/banks/{bank_id}/config", "/v1/default/banks/{bank_id}/config"),
    # graph + entities
    ("GET", "/banks/{bank_id}/graph", "/v1/default/banks/{bank_id}/graph"),
    ("GET", "/banks/{bank_id}/entities/graph", "/v1/default/banks/{bank_id}/entities/graph"),
    ("GET", "/banks/{bank_id}/entities", "/v1/default/banks/{bank_id}/entities"),
    (
        "GET",
        "/banks/{bank_id}/entities/{entity_id}",
        "/v1/default/banks/{bank_id}/entities/{entity_id}",
    ),
    # memory units
    ("GET", "/banks/{bank_id}/memories", "/v1/default/banks/{bank_id}/memories/list"),
    ("DELETE", "/banks/{bank_id}/memories", "/v1/default/banks/{bank_id}/memories"),
    (
        "GET",
        "/banks/{bank_id}/memories/{memory_id}",
        "/v1/default/banks/{bank_id}/memories/{memory_id}",
    ),
    (
        "GET",
        "/banks/{bank_id}/memories/{memory_id}/history",
        "/v1/default/banks/{bank_id}/memories/{memory_id}/history",
    ),
    # documents + chunks + tags
    ("GET", "/banks/{bank_id}/documents", "/v1/default/banks/{bank_id}/documents"),
    (
        "GET",
        "/banks/{bank_id}/documents/{document_id}",
        "/v1/default/banks/{bank_id}/documents/{document_id}",
    ),
    (
        "DELETE",
        "/banks/{bank_id}/documents/{document_id}",
        "/v1/default/banks/{bank_id}/documents/{document_id}",
    ),
    (
        "POST",
        "/banks/{bank_id}/documents/{document_id}/reprocess",
        "/v1/default/banks/{bank_id}/documents/{document_id}/reprocess",
    ),
    ("GET", "/banks/{bank_id}/tags", "/v1/default/banks/{bank_id}/tags"),
    # cognition consoles
    ("POST", "/banks/{bank_id}/recall", "/v1/default/banks/{bank_id}/memories/recall"),
    ("POST", "/banks/{bank_id}/reflect", "/v1/default/banks/{bank_id}/reflect"),
    # mental models
    ("GET", "/banks/{bank_id}/mental-models", "/v1/default/banks/{bank_id}/mental-models"),
    ("POST", "/banks/{bank_id}/mental-models", "/v1/default/banks/{bank_id}/mental-models"),
    (
        "GET",
        "/banks/{bank_id}/mental-models/{model_id}",
        "/v1/default/banks/{bank_id}/mental-models/{model_id}",
    ),
    (
        "PATCH",
        "/banks/{bank_id}/mental-models/{model_id}",
        "/v1/default/banks/{bank_id}/mental-models/{model_id}",
    ),
    (
        "DELETE",
        "/banks/{bank_id}/mental-models/{model_id}",
        "/v1/default/banks/{bank_id}/mental-models/{model_id}",
    ),
    (
        "POST",
        "/banks/{bank_id}/mental-models/{model_id}/refresh",
        "/v1/default/banks/{bank_id}/mental-models/{model_id}/refresh",
    ),
    (
        "GET",
        "/banks/{bank_id}/mental-models/{model_id}/history",
        "/v1/default/banks/{bank_id}/mental-models/{model_id}/history",
    ),
    # directives
    ("GET", "/banks/{bank_id}/directives", "/v1/default/banks/{bank_id}/directives"),
    ("POST", "/banks/{bank_id}/directives", "/v1/default/banks/{bank_id}/directives"),
    (
        "PATCH",
        "/banks/{bank_id}/directives/{directive_id}",
        "/v1/default/banks/{bank_id}/directives/{directive_id}",
    ),
    (
        "DELETE",
        "/banks/{bank_id}/directives/{directive_id}",
        "/v1/default/banks/{bank_id}/directives/{directive_id}",
    ),
    # async operations
    ("GET", "/banks/{bank_id}/operations", "/v1/default/banks/{bank_id}/operations"),
    (
        "GET",
        "/banks/{bank_id}/operations/{operation_id}",
        "/v1/default/banks/{bank_id}/operations/{operation_id}",
    ),
    (
        "DELETE",
        "/banks/{bank_id}/operations/{operation_id}",
        "/v1/default/banks/{bank_id}/operations/{operation_id}",
    ),
    (
        "POST",
        "/banks/{bank_id}/operations/{operation_id}/retry",
        "/v1/default/banks/{bank_id}/operations/{operation_id}/retry",
    ),
    ("POST", "/banks/{bank_id}/consolidate", "/v1/default/banks/{bank_id}/consolidate"),
    (
        "POST",
        "/banks/{bank_id}/consolidation/recover",
        "/v1/default/banks/{bank_id}/consolidation/recover",
    ),
    # bank templates
    ("GET", "/banks/{bank_id}/export", "/v1/default/banks/{bank_id}/export"),
    ("POST", "/banks/{bank_id}/import", "/v1/default/banks/{bank_id}/import"),
)

_BODY_METHODS = {"POST", "PUT", "PATCH"}


def _make_handler(method: str, template: str):
    async def handler(request: Request) -> Any:
        client = _client(request)
        segments = _validate_segments(dict(request.path_params))
        upstream = template.format(**segments) if segments else template
        body = await _read_body(request) if method in _BODY_METHODS else None
        params = dict(request.query_params) or None
        return await _forward(client, method, upstream, params=params, json_body=body)

    return handler


for _method, _path, _template in _FORWARDS:
    router.add_api_route(
        _path,
        _make_handler(_method, _template),
        methods=[_method],
        name=f"memory_admin_{_method.lower()}_{_template.rsplit('/', 2)[-1]}",
    )


__all__ = ["router"]
