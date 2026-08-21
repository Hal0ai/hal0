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
  provider without a Hindsight client (e.g. the pgvector engine) → 501
  ``memory.engine_unsupported``.
* Upstream errors: 4xx pass through status with code ``memory.engine_error``;
  upstream 5xx → 502; transport failure → 503 ``memory.engine_unreachable``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response

from hal0.api._audit import record_action
from hal0.api.routes import _memory_subgraph as _sg
from hal0.api.routes.memory import MemoryUnavailable
from hal0.errors import BadRequest, Hal0Error, NotFound, UnprocessableEntity

router = APIRouter()

log = logging.getLogger(__name__)

#: Bank ids come from namespace_to_bank() (``private__<agent>``), operator
#: input, or foreign agents writing to the shared engine directly — e.g. the
#: hindsight plugin's ``dynamicBankId`` mints ``claude::<project>`` and other
#: stacks use ``global:hal0`` / ``private:claude``. Colons are legal in a URL
#: path segment and carry no traversal risk, so they are allowed (after the
#: first char); dots stay blocked (path tricks).
_BANK_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_:-]{0,127}$")

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


class MemoryEngineShape(Hal0Error):
    """Hindsight answered 2xx but with an envelope the dashboard can't consume.

    The recall/reflect/directives consoles are verbatim passthroughs whose UI
    hooks assume a specific response shape (``{results}`` / ``{text}`` /
    ``{items}``). If Hindsight drifts that shape (version bump, breaking change)
    the console would break *silently* with a blank panel. This guard turns that
    silent break into a loud, attributable 502 so the drift is diagnosable
    (issue #1026).
    """

    code = "memory.engine_shape"
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


def _int_param(qp: Any, name: str, default: int) -> int:
    """Parse an int query param, 422ing (not 500ing) on garbage input.

    ``int(qp.get(name, default))`` lets a malformed value (``?limit=abc``)
    escape as an unhandled ``ValueError`` all the way to
    ``ServerErrorMiddleware`` → 500. Every other invalid-input path on these
    composed endpoints (``sort=bogus``, ``kind=bogus``, ...) 422s with
    ``memory.invalid_query``; this keeps int params on the same contract.
    See PR #1987 review M4.
    """
    raw = qp.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise UnprocessableEntity(f"invalid {name}: {raw!r}", code="memory.invalid_query") from None


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


#: Every one of these being zero/absent on an otherwise-200 stats response is
#: the shape Hindsight returns for a bank id that was never created (#1796) —
#: it never itself 404s the way its sibling routes (``/profile``, ``/config``,
#: ...) do. A genuinely empty-but-real bank looks identical over this same
#: key set, so this is only a trigger to go double-check existence, never a
#: verdict on its own.
_ZERO_STATS_KEYS = ("total_nodes", "total_documents", "total_observations")


def _looks_like_placeholder_stats(stats: Any) -> bool:
    if not isinstance(stats, dict):
        return False
    return all(not stats.get(k) for k in _ZERO_STATS_KEYS)


@router.get("/banks/{bank_id}/stats")
async def bank_stats(request: Request, bank_id: str) -> Any:
    """Per-bank stats — 404 for an unknown bank instead of a zeroed 200.

    Hindsight's own ``GET /v1/default/banks/{bank}/stats`` answers 200 with
    every counter at zero for a bank id that was never created (#1796),
    indistinguishable from a real, merely-empty bank — unlike an upstream
    4xx/5xx on this same route, which still passes through unchanged (a
    real Hindsight-side error is not this route's business to reinterpret).
    Only when the 2xx body itself looks like the all-zero placeholder do we
    pay for a second call — confirming against the authoritative
    ``GET /v1/default/banks`` listing — rather than on every request.
    """
    client = _client(request)
    _validate_segments({"bank_id": bank_id})
    stats = await _forward(client, "GET", f"/v1/default/banks/{bank_id}/stats")
    if not _looks_like_placeholder_stats(stats):
        return stats
    banks_resp = await _forward(client, "GET", "/v1/default/banks")
    banks = banks_resp.get("banks") if isinstance(banks_resp, dict) else banks_resp
    known_ids = (
        {(b.get("bank_id") if isinstance(b, dict) else b) for b in banks}
        if isinstance(banks, list)
        else set()
    )
    if bank_id not in known_ids:
        raise NotFound(f"bank {bank_id!r} does not exist", code="memory.bank_not_found")
    return stats


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
    limit = min(_int_param(qp, "limit", 240), 500)

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
        depth = min(_int_param(qp, "depth", 1), 10)
        keep = _sg.ego_bfs(graph, node, depth=depth, limit=limit)
        if not keep:
            raise NotFound(f"node {node!r} not in bank graph", code="memory.node_not_found")
    else:
        by = qp.get("by") or ("degree" if kind == "entities" else "recency")
        ranked = _sg.rank_by_degree(graph) if by == "degree" else _sg.rank_by_recency(graph)
        top_k = min(_int_param(qp, "top_k", 200), 500)
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


# ── composed ranked units endpoint (NOT a passthrough) ─────────────────────────
#
# Upstream /memories/list only understands q/type/state/document_id/limit/offset
# (0.8.4); tag, time-window filtering and salience ranking are computed here
# over a cached slab — same tradeoff, and the same per-bank graph cache, as
# bank_subgraph. Registered explicitly BEFORE the _FORWARDS loop.

#: 0.8.4 list rows carry no ``created_at`` — timestamps come from whichever of
#: these is present (first match wins).
_UNIT_TS_KEYS = ("mentioned_at", "date")

#: Upstream ``/memories/list``'s ``type`` param is single-value
#: exact-equality (0.8.4) — ``type=world,experience`` silently returns an
#: empty page, no 422. hal0 fails loudly on an unknown value instead, and
#: does the OR itself hal0-side when more than one is selected.
_VALID_FACT_TYPES = ("world", "experience", "observation")


def _unit_ts(row: dict[str, Any]) -> str:
    for key in _UNIT_TS_KEYS:
        value = row.get(key)
        if value:
            return str(value)
    return ""  # missing sorts last


@router.get("/banks/{bank_id}/units")
async def bank_units(request: Request, bank_id: str) -> dict[str, Any]:
    """Ranked, filterable fact slice for the Bank workspace (memory_v2 ask #2).

    Each returned unit is the upstream ``/memories/list`` row verbatim plus
    computed ``salience`` (graph-degree weighted, see ``_sg.degree_by_node``)
    and ``link_counts_by_type`` (whatever link types the bank graph emits —
    ``temporal``/``semantic``/``entity`` today; never hardcoded). ``tags``,
    ``from``/``to`` and ``sort=salience`` are hal0-side; ``q``/``type``/
    ``state``/``document_id`` forward upstream verbatim (``state`` matters:
    invalidated facts live in a separate archive excluded from the default
    listing, so the inspector's revert flow needs ``state=invalidated`` to
    have anything to list).

    Both the unit listing and the salience graph are pulled as a single
    ``limit=2000``-row slab (see ``src_params`` below, and the unfiltered
    bank-graph fetch further down); a bank with more than 2000 matching units
    or more than 2000 graph nodes is silently clipped at that slab boundary.
    The response's ``truncated`` flag covers the unit-listing slab
    (``total_matched``/paging are only accurate within it). A unit outside
    the 2000-node graph slab has genuinely unknown connectivity — it reads
    back as ``salience: null`` and ``link_counts_by_type: {}`` rather than a
    false ``0.0``, and sorts after every scored row under ``sort=salience``.
    See PR #1987 review B2.
    """
    client = _client(request)
    _validate_segments({"bank_id": bank_id})
    qp = request.query_params
    sort = qp.get("sort", "recency")
    if sort not in ("recency", "salience"):
        raise UnprocessableEntity(f"invalid sort: {sort!r}", code="memory.invalid_query")
    # max(1, ...) closes #1987/B3: limit=0 made next_offset==offset (infinite
    # client-side pagination loop) and limit<0 dropped the last row via
    # negative-index slicing (kept[0:-1]).
    limit = max(1, min(_int_param(qp, "limit", 20), 200))
    offset = max(_int_param(qp, "offset", 0), 0)

    type_values = [t for t in (qp.get("type") or "").split(",") if t]
    if any(t not in _VALID_FACT_TYPES for t in type_values):
        raise UnprocessableEntity(f"invalid type: {qp.get('type')!r}", code="memory.invalid_query")
    types = set(type_values)

    # NOTE: build upstream params explicitly, one at a time — the generic
    # forward's dict(request.query_params) collapses repeated params to the
    # last value. All params here are single-valued so that's moot today, but
    # if 0.8.5+ ever lands multi-value tag forwarding, that must use
    # request.query_params.multi_items() + an httpx list-of-tuples, not a
    # comma-join into a single string like the hal0-side `tags` filter below.
    src_params: dict[str, str] = {k: qp[k] for k in ("q", "state", "document_id") if qp.get(k)}
    if len(type_values) == 1:
        # exactly one type: upstream's exact-equality `type` still applies.
        src_params["type"] = type_values[0]
    # 2+ types: do NOT forward `type` — upstream can't OR them (0.8.4 single-
    # value exact match; a comma-joined value silently matches nothing). Pull
    # the unfiltered slab instead and OR them hal0-side below.
    src_params["limit"] = "2000"
    listing = await _forward(
        client, "GET", f"/v1/default/banks/{bank_id}/memories/list", params=src_params
    )
    rows = listing.get("items") if isinstance(listing, dict) else listing
    if not isinstance(rows, list):
        raise MemoryEngineShape("memories/list returned no item list")
    # B2: the src_params slab is capped at limit=2000 above — surface that
    # cap instead of letting total_matched/next_offset silently describe only
    # the slab. Mirrors bank_subgraph's own ``truncated`` (line ~313).
    truncated = len(rows) >= 2000

    tags = {t for t in (qp.get("tags") or "").split(",") if t}
    lo, hi = qp.get("from"), qp.get("to")
    kept = [
        r
        for r in rows
        if isinstance(r, dict)
        and (len(types) <= 1 or r.get("fact_type") in types)  # multi-type OR, hal0-side
        and (not tags or tags & set(r.get("tags") or ()))
        and (not (lo or hi) or _unit_ts(r) != "")  # window given: undated rows drop
        and (not lo or _unit_ts(r) >= lo)
        and (not hi or _unit_ts(r)[: len(hi)] <= hi)
    ]

    # Salience + per-type link counts from the cached bank graph (45s TTL).
    #
    # Deliberately UNFILTERED — no `type`/`q` forwarded to `/graph`, always,
    # regardless of this request's listing filters. Two independent reasons,
    # both footguns, so do not "fix" this by threading qp['type']/qp['q']
    # through again:
    #   1. Same upstream single-value exact-equality trap as `/memories/list`
    #      — a comma-joined `type` would silently fetch an empty graph.
    #   2. Even a single valid `type` would be wrong here: upstream's edge
    #      query only returns an edge when BOTH endpoints are in the filtered
    #      node set, so cross-type edges (e.g. an `experience` fact linked to
    #      a `world` fact) vanish and every node's degree is understated.
    # Cache key is therefore constant per bank (no type/q component) so this
    # slab is shared across every /units call and with bank_subgraph's
    # unfiltered fetches.
    #
    # Also note: upstream caps this endpoint at ~10k edges (max_edges) and
    # silently truncates dense banks past that — no behavior change here,
    # just a known ceiling on the salience/link-count accuracy for very
    # large/dense banks.
    cache_key = f"{bank_id}:memories::"
    graph = _GRAPH_CACHE.peek(cache_key)
    if graph is None:
        graph = await _forward(
            client, "GET", f"/v1/default/banks/{bank_id}/graph", params={"limit": "2000"}
        )
        _GRAPH_CACHE.put(cache_key, graph)
    adj = _sg.adjacency(graph)
    salience_by_node = _sg.degree_by_node(graph)
    # The slab is capped at the 2000 most-recent units — a row outside it has
    # genuinely unknown connectivity, not zero. Score it null (never a false
    # 0) and mark it out-of-slab in link_counts_by_type too.
    graph_node_ids = {(n.get("data") or n).get("id") for n in graph.get("nodes", [])}
    for r in kept:
        rid = r.get("id")
        if rid not in graph_node_ids:
            r["link_counts_by_type"] = {}
            r["salience"] = None
            continue
        nbrs = adj.get(rid, [])
        counts: dict[str, int] = {}
        for _t, link_type, _w in nbrs:
            counts[link_type] = counts.get(link_type, 0) + 1
        r["link_counts_by_type"] = counts
        r["salience"] = salience_by_node.get(rid, (0, 0.0))[1]

    if sort == "salience":
        # Scored rows first (highest salience first, ties by recency);
        # unscored (out-of-slab, salience is None) rows after, stable by
        # recency among themselves rather than an arbitrary/false ranking.
        scored = [r for r in kept if r["salience"] is not None]
        unscored = [r for r in kept if r["salience"] is None]
        scored.sort(key=lambda r: (-r["salience"], _unit_ts(r)))
        unscored.sort(key=lambda r: _unit_ts(r), reverse=True)
        kept = scored + unscored
    else:
        kept.sort(key=lambda r: _unit_ts(r), reverse=True)
    page = kept[offset : offset + limit]
    nxt = offset + limit if offset + limit < len(kept) else None
    return {
        "items": page,
        "total_matched": len(kept),
        "next_offset": nxt,
        "truncated": truncated,
    }


# ── DELETE /banks/{bank_id} — guarded (NOT a passthrough) ──────────────────────
#
# Bank deletion is irreversible upstream (drops every memory/document/entity in
# the bank), so it is special-cased OUT of the _FORWARDS table and gated behind
# an explicit confirmation that must echo the target bank id — via ?confirm=
# and/or a JSON body ``{"confirm": ...}``. Registered before the _FORWARDS loop
# so the guarded route owns the verb; only forwards once confirmation matches.

#: #1024 dry-run preview: bank-stats keys carrying stored-item counts. Best
#: effort — Hindsight versions differ, so absent keys are simply omitted.
#: These are the real hindsight-api 0.8.4 ``/v1/default/banks/{bank}/stats``
#: keys (verified live) — NOT the ``memory_count``/``document_count``/
#: ``entity_count`` keys that endpoint has never returned (#1653).
_PREVIEW_COUNT_KEYS = ("total_nodes", "total_documents", "total_observations")


async def _delete_preview(client: Any, bank_id: str) -> dict[str, Any]:
    """Fail-soft blast-radius preview for a bank delete (#1024).

    Returns ``{bank_id, item_count, counts, stats_available}`` so the confirm
    dialog can show *how much* a wipe would destroy before the operator echoes
    the id back. ``item_count`` is the headline memory-unit count when known.
    The stats probe is a convenience, never a gate: any failure degrades to
    ``stats_available=False`` and the request is still rejected loudly below.
    """
    preview: dict[str, Any] = {"bank_id": bank_id, "item_count": None, "stats_available": False}
    try:
        stats = await client.request_json("GET", f"/v1/default/banks/{bank_id}/stats")
    except Exception:
        return preview
    if not isinstance(stats, dict):
        return preview
    counts = {k: stats[k] for k in _PREVIEW_COUNT_KEYS if isinstance(stats.get(k), int)}
    preview["stats_available"] = True
    preview["counts"] = counts
    preview["item_count"] = counts.get("total_nodes")
    return preview


async def _require_echoed_confirm(request: Request, client: Any, bank_id: str, what: str) -> None:
    """Enforce the #1024 echoed-id gate, or raise 400 with a dry-run preview.

    Shared by every route in ``exposure.CONFIRM_GUARDED_MEMORY_ROUTES`` — the
    bank delete and the bank-memories wipe. #1457 is what happens when one
    bank-scoped irreversible route gets this treatment and its sibling is left
    in the generic ``_FORWARDS`` passthrough: same blast radius, zero friction.
    Duplicating the check per route is how they diverge, so there is one.

    The echo is read from ``?confirm=`` first, then a JSON body ``confirm``
    key, because the dashboard sends the former and CLI/API callers the latter.
    """
    body = await _read_body(request)
    confirm = request.query_params.get("confirm")
    if confirm is None and isinstance(body, dict):
        confirm = body.get("confirm")
    if confirm == bank_id:
        return
    # #1024: return a dry-run preview (bank id + item counts) instead of a
    # bare rejection so operators see the blast radius. Status stays 400 and
    # the DELETE is NOT forwarded — the echoed-id confirm is still required.
    preview = await _delete_preview(client, bank_id)
    raise BadRequest(
        f"confirm={bank_id} required to {what}",
        code="memory.confirm_required",
        details={"bank_id": bank_id, "requires_confirm": True, "preview": preview},
    )


@router.delete("/banks/{bank_id}")
async def delete_bank(request: Request, bank_id: str) -> Any:
    client = _client(request)
    _validate_segments({"bank_id": bank_id})
    await _require_echoed_confirm(request, client, bank_id, f"delete bank {bank_id}")
    log.warning("memory_admin: deleting bank %r (confirmed)", bank_id)
    # #1024 hardening: every confirmed destructive op lands in the audit store
    # (actor + timestamp + outcome) so a bank wipe is attributable after the
    # fact. Bank deletion is the highest-blast-radius op on this surface.
    async with record_action(
        request, category="memory", action="memory.bank.delete", target=bank_id
    ):
        return await _forward(client, "DELETE", f"/v1/default/banks/{bank_id}")


@router.delete("/banks/{bank_id}/memories")
async def delete_bank_memories(request: Request, bank_id: str) -> Any:
    """Wipe every memory unit in a bank — guarded like the bank delete (#1457).

    Upstream is ``DELETE /v1/default/banks/{bank_id}/memories``, which
    Hindsight documents as "a destructive operation that cannot be undone".
    It used to ride ``_FORWARDS`` with an audit row and nothing else, so the
    ``record_action`` entry described a wipe that had already happened on a
    call anyone could make. It is special-cased out of the table for the same
    reason ``DELETE /banks/{bank_id}`` is: the generic handler cannot ask for
    an echo it knows nothing about.

    Upstream query filters (e.g. ``?types=``) still forward verbatim;
    ``confirm`` is hal0's own gate and is stripped so it never reaches the
    engine as an unrecognised filter.
    """
    client = _client(request)
    _validate_segments({"bank_id": bank_id})
    await _require_echoed_confirm(
        request, client, bank_id, f"delete every memory unit in bank {bank_id}"
    )
    params = {k: v for k, v in request.query_params.items() if k != "confirm"} or None
    log.warning("memory_admin: wiping memories in bank %r (confirmed)", bank_id)
    async with record_action(
        request, category="memory", action="memory.memories.delete", target=bank_id
    ):
        return await _forward(
            client, "DELETE", f"/v1/default/banks/{bank_id}/memories", params=params
        )


# ── /banks/{bank_id}/document-transfer — cross-bank migration (NOT a table
#    passthrough) ─────────────────────────────────────────────────────────────
#
# hindsight-api>=0.8.0 (source-verified against the v0.8.4 tag; absent from
# the 0.7.2 instance this router was built against — gate on
# ``features.document_export_api``/``document_import_api`` from ``/version``,
# not the version string). GET returns a ZIP file (source bank's documents +
# optionally their observations); POST accepts that ZIP as a multipart
# upload and starts an async transfer into the target bank
# (``?on_conflict=skip|replace|new-id``, default ``skip``), returning
# ``202 {operation_id}`` — poll the existing ``operations/{id}`` passthrough
# for ``result_metadata`` (documents_imported / facts_imported /
# observations_imported). Both verbs move raw bytes, so they can't go
# through ``_forward``/``request_json`` (JSON-only) — they reach into the
# Hindsight client's private ``_http``/``_headers()`` rather than adding a
# second public method to hal0.memory.hindsight_client, which is owned by a
# parallel unified-bank effort (see PLAN note in the CLI's migrate_unify
# docstring for the full boundary rationale).


async def _raise_transfer_error(exc: Exception) -> None:
    if isinstance(exc, httpx.HTTPStatusError):
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
    if isinstance(exc, httpx.HTTPError):
        raise MemoryEngineUnreachable(
            "memory engine is unreachable", details={"error": str(exc)}
        ) from exc
    raise exc


@router.get("/banks/{bank_id}/document-transfer")
async def bank_document_transfer_export(request: Request, bank_id: str) -> Response:
    client = _client(request)
    _validate_segments({"bank_id": bank_id})
    include_observations = request.query_params.get("include_observations", "true")
    try:
        resp = await client._http.get(
            f"/v1/default/banks/{bank_id}/document-transfer",
            headers=client._headers(),
            params={"include_observations": include_observations},
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        await _raise_transfer_error(exc)
    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "application/zip"),
    )


@router.post("/banks/{bank_id}/document-transfer")
async def bank_document_transfer_import(request: Request, bank_id: str) -> Any:
    client = _client(request)
    _validate_segments({"bank_id": bank_id})
    on_conflict = request.query_params.get("on_conflict", "skip")
    if on_conflict not in ("skip", "replace", "new-id"):
        raise BadRequest(
            f"invalid on_conflict: {on_conflict!r} (skip|replace|new-id)",
            code="memory.invalid_query",
        )
    form = await request.form()
    upload = form.get("file")
    if upload is None:
        raise BadRequest("multipart 'file' field is required", code="memory.invalid_body")
    content = await upload.read()
    headers = {k: v for k, v in client._headers().items() if k.lower() != "content-type"}
    try:
        async with record_action(
            request,
            category="memory",
            action="memory.bank.document_transfer_import",
            target=bank_id,
        ):
            resp = await client._http.post(
                f"/v1/default/banks/{bank_id}/document-transfer",
                headers=headers,
                params={"on_conflict": on_conflict},
                files={
                    "file": (
                        getattr(upload, "filename", None) or "transfer.zip",
                        content,
                        "application/zip",
                    )
                },
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        await _raise_transfer_error(exc)
    if not resp.content:
        return {}
    return resp.json()


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
    # DELETE /banks/{bank_id} is NOT here — it is special-cased into the guarded
    # ``delete_bank`` handler above (irreversible; requires ?confirm=<bank_id>).
    # GET /banks/{bank_id}/stats is NOT here either — see ``bank_stats`` below
    # (#1796: the plain passthrough returned 200 with all-zero counts for an
    # unknown bank id instead of 404).
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
    # DELETE /banks/{bank_id}/memories is NOT here — same treatment as
    # DELETE /banks/{bank_id}: it wipes every memory unit in the bank, so it
    # is special-cased into the guarded ``delete_bank_memories`` handler
    # above (irreversible; requires ?confirm=<bank_id>). See #1457.
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
    # memory_curate (MCP parity): edit text/context/fact_type/entities or
    # invalidate a unit (state="invalidated", reason). Reversible → no
    # _DELETE_ACTIONS entry, no approval gate.
    (
        "PATCH",
        "/banks/{bank_id}/memories/{memory_id}",
        "/v1/default/banks/{bank_id}/memories/{memory_id}",
    ),
    # documents + chunks + tags
    ("GET", "/banks/{bank_id}/documents", "/v1/default/banks/{bank_id}/documents"),
    (
        "GET",
        "/banks/{bank_id}/documents/{document_id}",
        "/v1/default/banks/{bank_id}/documents/{document_id}",
    ),
    (
        # ``PATCH .../documents/{id}`` is the only tag-edit surface Hindsight
        # has (no per-memory tag field on UpdateMemoryRequest) — body
        # ``{"tags": [...]}`` is a full replace that propagates to every
        # memory unit under the document and invalidates derived
        # observations (queues re-consolidation). ``migrate unify --add-tag``
        # relies on this: read-merge-write, never a bare overwrite.
        "PATCH",
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
    #
    # NB two distinct ``recall`` contracts exist under /api/memory (issue #1026):
    #   * POST /api/memory/recall            (hal0.api.routes.memory) — NAMESPACE
    #     recall: ACL-scoped, cross-bank fan-out, returns ``{items: MemoryItem[]}``.
    #   * POST /api/memory/banks/{bank}/recall (this table)          — BANK recall:
    #     a single-bank verbatim Hindsight passthrough, returns ``{results: [...]}``.
    # Same verb, different scope + envelope — do not conflate them.
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


# ── #1026: response-shape guards for the cognition consoles ─────────────────────
#
# The recall/reflect/directives passthroughs feed UI hooks that assume a fixed
# envelope. We validate the *presence + type* of the load-bearing key (not the
# full schema) so upstream drift surfaces as a loud, attributable 502
# (``memory.engine_shape``) instead of a silently-blank console panel.


def _require(payload: Any, key: str, kind: type, upstream: str) -> None:
    if not isinstance(payload, dict) or not isinstance(payload.get(key), kind):
        raise MemoryEngineShape(
            f"memory engine response missing expected {key!r} ({kind.__name__})",
            details={
                "upstream": upstream,
                "expected_key": key,
                "expected_type": kind.__name__,
                "got_keys": sorted(payload.keys()) if isinstance(payload, dict) else None,
            },
        )


#: (method, upstream template) → validator run against the 2xx response body.
_SHAPE_GUARDS: dict[tuple[str, str], Callable[[Any], None]] = {
    ("POST", "/v1/default/banks/{bank_id}/memories/recall"): lambda p: _require(
        p, "results", list, "recall"
    ),
    ("POST", "/v1/default/banks/{bank_id}/reflect"): lambda p: _require(p, "text", str, "reflect"),
    ("GET", "/v1/default/banks/{bank_id}/directives"): lambda p: _require(
        p, "items", list, "directives"
    ),
}


#: #1024: DELETE forwards audited via record_action. Template → audit action.
_DELETE_ACTIONS: dict[str, str] = {
    "/v1/default/banks/{bank_id}/config": "memory.bank_config.delete",
    "/v1/default/banks/{bank_id}/memories": "memory.memories.delete",
    "/v1/default/banks/{bank_id}/documents/{document_id}": "memory.document.delete",
    "/v1/default/banks/{bank_id}/directives/{directive_id}": "memory.directive.delete",
    "/v1/default/banks/{bank_id}/operations/{operation_id}": "memory.operation.delete",
    "/v1/default/banks/{bank_id}/mental-models/{model_id}": "memory.mental_model.delete",
}

#: #1987/M7: curate (memory_curate MCP parity) is a soft-delete-capable edit
#: (state="invalidated" invalidates a unit) but rode _FORWARDS with no audit
#: trail at all — unlike every DELETE above. It stays out of
#: DESTRUCTIVE_MEMORY_ROUTES / CONFIRM_GUARDED_MEMORY_ROUTES (reversible, no
#: confirm gate needed) but still gets a record_action row so "who
#: invalidated this, when" is answerable.
_CURATE_TEMPLATE = ("PATCH", "/v1/default/banks/{bank_id}/memories/{memory_id}")
_CURATE_AUDIT_ACTION = "memory.memory.curate"

#: #1987/M5: forwards whose bank-scoped _GRAPH_CACHE slab (salience,
#: link_counts_by_type) goes stale the moment they succeed. Cleared
#: post-forward so a curate → units/subgraph GET within the 45s TTL never
#: serves the pre-mutation slab.
_CACHE_CLEARING_FORWARDS: set[tuple[str, str]] = {_CURATE_TEMPLATE}


def _make_handler(method: str, template: str):
    guard = _SHAPE_GUARDS.get((method, template))
    audit_action = _DELETE_ACTIONS.get(template) if method == "DELETE" else None
    if (method, template) == _CURATE_TEMPLATE:
        audit_action = _CURATE_AUDIT_ACTION
    clears_cache = (method, template) in _CACHE_CLEARING_FORWARDS

    async def handler(request: Request) -> Any:
        client = _client(request)
        segments = _validate_segments(dict(request.path_params))
        upstream = template.format(**segments) if segments else template
        body = await _read_body(request) if method in _BODY_METHODS else None
        params = dict(request.query_params) or None
        if audit_action is not None:
            # #1024: audit destructive ops with a truthful outcome — the block
            # records outcome=error if the forward raises, outcome=ok otherwise.
            async with record_action(
                request, category="memory", action=audit_action, target=upstream
            ):
                result = await _forward(client, method, upstream, params=params, json_body=body)
        else:
            result = await _forward(client, method, upstream, params=params, json_body=body)
        if clears_cache:
            bank_id = segments.get("bank_id")
            if bank_id:
                _GRAPH_CACHE.clear_bank(bank_id)
        if guard is not None:
            guard(result)
        return result

    return handler


for _method, _path, _template in _FORWARDS:
    router.add_api_route(
        _path,
        _make_handler(_method, _template),
        methods=[_method],
        name=f"memory_admin_{_method.lower()}_{_template.rsplit('/', 2)[-1]}",
    )


__all__ = ["router"]
