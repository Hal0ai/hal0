"""Lemonade reverse-proxy for the un-routed /v1/* surface.

Issue #212. hal0-api at port 8080 already serves a curated /v1 surface
backed by the multi-upstream dispatcher (chat, completions, embeddings,
rerankings, audio, images, models). That surface is intentional — it
aggregates across every registered upstream so the dashboard / OpenAI
clients see one catalogue.

But Lemonade also exposes a SECOND tier of /v1 endpoints that aren't
inference (``/v1/health``, ``/v1/stats``, ``/v1/load``, ``/v1/unload``,
``/v1/system-info``, ``/v1/params`` …). The v3 React dashboard polls
those via the ``useLemonadeHealth`` hook to render the sidebar
'lemond connected' status block. They have no aggregation semantics —
there's only one Lemonade, on loopback — so the right move is a
straight reverse-proxy to ``http://127.0.0.1:13305``.

This module mounts a catch-all GET/POST/DELETE/PUT/PATCH at
``/v1/{path:path}`` and forwards verbatim. FastAPI matches routes in
registration order, so we mount this AFTER ``v1.public_router`` +
``v1.router`` — every dispatcher-owned path keeps its handler, and only
the un-covered paths fall through to the proxy.

Out of scope (handled elsewhere or deferred):
  - WebSocket upgrade (lemond exposes /logs/stream + /realtime). The
    existing ``lemonade_logs`` router already proxies /logs/stream → SSE
    under ``/api/lemonade/logs/stream``; a generic /v1 WS proxy is
    deferred to a follow-up issue.
  - Auth: removed in ADR-0012. Lemonade itself binds loopback only —
    this proxy is open on the local network.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

router = APIRouter()

# Per-request timeout. Connect is short so a dead daemon surfaces as 503
# quickly; read is generous because /v1/load can take tens of seconds.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=2.0, read=120.0, write=30.0, pool=5.0)

# Issue #474: the dashboard's useLemonadeHealth (2s) + useLemonadeStats (5s)
# hooks poll through this proxy, once PER open browser tab. lemond is a
# cpp-httplib server with a hardcoded 8-thread pool + accept backlog of 5, so an
# unbounded fan-out of fresh TCP connections starves its control plane (FIN-
# WAIT-2 pile-up, /health timeouts). Two guards:
#   1. A single shared, pooled client caps concurrent upstream connections.
#   2. A short TTL + single-flight cache on the read-only /v1/health and
#      /v1/stats polls collapses N tabs into one upstream call per window.
_POOL_LIMITS = httpx.Limits(max_connections=4, max_keepalive_connections=2)

# GET paths cheap + safe to coalesce (global pool snapshots, no side effects).
_CACHEABLE_GET_PATHS = frozenset({"health", "stats"})
_CACHE_TTL_S = 2.0

# Module-level shared client + cache. The client is built lazily (so importing
# this module opens no sockets) and lives for the process; closed by the app
# lifespan via aclose_client(). _reset_state() exists for test isolation.
_client: httpx.AsyncClient | None = None
_cache: dict[str, tuple[float, int, bytes, str | None, dict[str, str]]] = {}
_cache_locks: dict[str, asyncio.Lock] = {}


# Headers that MUST NOT round-trip across the proxy.  ``host`` would
# poison Lemonade's URL parser; the hop-by-hop set (RFC 7230 §6.1) is
# illegal to forward; ``content-length`` is recomputed by httpx +
# Starlette from the actual body bytes.
_REQUEST_HOP_BY_HOP = frozenset(
    {
        "host",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
    }
)

# Response-side hop-by-hop. ``content-encoding`` is dropped because we
# stream the decoded body (httpx auto-decodes gzip / br); echoing the
# encoding header would mislead the client into a second decode.
_RESPONSE_HOP_BY_HOP = frozenset(
    {
        "connection",
        "content-encoding",
        "content-length",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


def _lemonade_base_url() -> str:
    """Return the Lemonade loopback base URL.

    Env override matches :mod:`hal0.api.__init__` so a single
    ``LEMONADE_BASE_URL=...`` re-points both the OmniRouter and this
    proxy in development. The default is ADR-0008 §1's pinned loopback
    URL.
    """
    return os.environ.get("LEMONADE_BASE_URL", "http://127.0.0.1:13305").rstrip("/")


def _build_client(timeout: httpx.Timeout) -> httpx.AsyncClient:
    """Construct the shared httpx client.

    Lives as a module-level seam so tests can monkeypatch it to inject
    an ``httpx.MockTransport`` without spinning up a real socket. The pool
    limits (#474) cap concurrent connections to lemond's control plane.
    """
    return httpx.AsyncClient(timeout=timeout, limits=_POOL_LIMITS)


def _get_client() -> httpx.AsyncClient:
    """Return the process-wide shared proxy client, building it lazily.

    Reused across requests so dashboard polls amortise keep-alive instead of
    opening a fresh TCP connection per poll (#474). The check-and-set is atomic
    under asyncio (no await between), so concurrent first-callers can't race in
    two clients.
    """
    global _client
    if _client is None:
        _client = _build_client(_DEFAULT_TIMEOUT)
    return _client


async def aclose_client() -> None:
    """Close the shared client on app shutdown. Idempotent."""
    global _client
    if _client is not None:
        with contextlib.suppress(Exception):
            await _client.aclose()
        _client = None


def _reset_state() -> None:
    """Drop the shared client + cache. For test isolation only."""
    global _client
    _client = None
    _cache.clear()
    _cache_locks.clear()


def _cache_get(path: str) -> tuple[float, int, bytes, str | None, dict[str, str]] | None:
    """Return a live cache entry for ``path`` or None (evicting if expired)."""
    entry = _cache.get(path)
    if entry is None:
        return None
    if time.monotonic() >= entry[0]:
        _cache.pop(path, None)
        return None
    return entry


def _response_from_cache(
    entry: tuple[float, int, bytes, str | None, dict[str, str]],
) -> Response:
    return _make_response(entry[1:])


def _filter_request_headers(headers: Any) -> dict[str, str]:
    """Strip hop-by-hop + host headers from an incoming Starlette Headers."""
    return {k: v for k, v in headers.items() if k.lower() not in _REQUEST_HOP_BY_HOP}


def _filter_response_headers(headers: Any) -> dict[str, str]:
    """Strip hop-by-hop + length headers from an httpx response."""
    return {k: v for k, v in headers.items() if k.lower() not in _RESPONSE_HOP_BY_HOP}


async def _proxy(request: Request, path: str) -> Response:
    """Forward ``request`` to Lemonade and return the response.

    The catch-all targets Lemonade's non-streaming surfaces (health,
    stats, load, unload, system-info, params, …) — JSON in, JSON out,
    bodies small enough that buffering is cheaper than the extra
    streaming machinery. Lemonade's real streaming surfaces (the
    /logs/stream WS and chat-completion SSE) are handled by dedicated
    routers and don't fall through here.

    Errors:
      - When Lemonade is unreachable (connection refused, DNS, timeout)
        we surface a 503 with a hal0-shaped envelope so the dashboard's
        ``useLemonadeHealth`` hook treats it as 'lemond down' without
        crashing.
      - When Lemonade returns an error status, we propagate it verbatim
        — the body + status code + content-type. Clients see Lemonade's
        own envelope, not a hal0-rewrap.
    """
    base = _lemonade_base_url()
    # ``path`` arrives without a leading slash courtesy of FastAPI's
    # path converter; we anchor it ourselves so the upstream sees the
    # full /v1/<path>.
    target_url = f"{base}/v1/{path}"

    # Non-cacheable (writes, query-bearing, or non-health/stats GETs) go
    # straight upstream over the shared pooled client.
    cacheable = (
        request.method == "GET" and path in _CACHEABLE_GET_PATHS and not request.query_params
    )
    if not cacheable:
        return _make_response(await _forward(request, target_url))

    # Fast path: serve a fresh cache entry without touching lemond.
    hit = _cache_get(path)
    if hit is not None:
        return _response_from_cache(hit)

    # Single-flight: the first caller fills the cache while concurrent callers
    # for the same path wait on the lock and then re-check the cache — so N
    # dashboard tabs collapse into one upstream poll per TTL window (#474).
    lock = _cache_locks.setdefault(path, asyncio.Lock())
    async with lock:
        hit = _cache_get(path)
        if hit is not None:
            return _response_from_cache(hit)
        status, content, media_type, headers = await _forward(request, target_url)
        if 200 <= status < 300:
            _cache[path] = (
                time.monotonic() + _CACHE_TTL_S,
                status,
                content,
                media_type,
                headers,
            )
        return _make_response((status, content, media_type, headers))


async def _forward(
    request: Request, target_url: str
) -> tuple[int, bytes, str | None, dict[str, str]]:
    """Forward one request to lemond over the shared client.

    Returns the response parts ``(status, content, media_type, headers)`` —
    including hal0-shaped 503/502 envelopes when lemond is unreachable or
    errors — so the caller can both build a Response and (for cacheable GETs)
    store the parts.
    """
    headers = _filter_request_headers(request.headers)
    body = await request.body()
    params = list(request.query_params.multi_items())

    client = _get_client()
    try:
        upstream_resp = await client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body if body else None,
            params=params,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        envelope = {
            "error": {
                "code": "lemonade.unavailable",
                "message": "lemonade is not reachable on loopback",
                "details": {"target": target_url, "reason": str(exc)},
            }
        }
        return 503, json.dumps(envelope).encode("utf-8"), "application/json", {}
    except httpx.HTTPError as exc:
        envelope = {
            "error": {
                "code": "lemonade.proxy_error",
                "message": "error forwarding request to lemonade",
                "details": {"target": target_url, "reason": str(exc)},
            }
        }
        return 502, json.dumps(envelope).encode("utf-8"), "application/json", {}

    return (
        upstream_resp.status_code,
        upstream_resp.content,
        upstream_resp.headers.get("content-type"),
        _filter_response_headers(upstream_resp.headers),
    )


def _make_response(parts: tuple[int, bytes, str | None, dict[str, str]]) -> Response:
    status, content, media_type, headers = parts
    return Response(
        content=content,
        status_code=status,
        headers=dict(headers),
        media_type=media_type,
    )


# Catch-all. We register one handler per HTTP method so OpenAPI sees a
# usable surface (FastAPI's "match any method" isn't well-supported on
# path operations). ``path`` carries everything after ``/v1/``.
#
# HEAD intentionally omitted — Starlette derives HEAD from GET when the
# response is streaming, which is the right behaviour here.


@router.get("/{path:path}", include_in_schema=False)
async def proxy_get(request: Request, path: str) -> Response:
    return await _proxy(request, path)


@router.post("/{path:path}", include_in_schema=False)
async def proxy_post(request: Request, path: str) -> Response:
    return await _proxy(request, path)


@router.put("/{path:path}", include_in_schema=False)
async def proxy_put(request: Request, path: str) -> Response:
    return await _proxy(request, path)


@router.delete("/{path:path}", include_in_schema=False)
async def proxy_delete(request: Request, path: str) -> Response:
    return await _proxy(request, path)


@router.patch("/{path:path}", include_in_schema=False)
async def proxy_patch(request: Request, path: str) -> Response:
    return await _proxy(request, path)


__all__ = ["router"]
