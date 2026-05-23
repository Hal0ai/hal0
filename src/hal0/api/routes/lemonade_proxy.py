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
  - Auth: the catch-all sits behind the same ``require_token`` gate the
    rest of the /v1 inference surface uses (mounted with ``_v1_auth``
    dependencies in :mod:`hal0.api`). Lemonade itself binds loopback
    only — anyone reaching this proxy already crossed hal0-api's gate.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

router = APIRouter()


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
    """Construct the per-request httpx client.

    Lives as a module-level seam so tests can monkeypatch it to inject
    an ``httpx.MockTransport`` without spinning up a real socket.
    """
    return httpx.AsyncClient(timeout=timeout)


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
    headers = _filter_request_headers(request.headers)
    body = await request.body()
    params = list(request.query_params.multi_items())

    # Cap the per-request timeout generously — Lemonade can take tens of
    # seconds to load a model on /v1/load. Connect timeout is short so a
    # dead daemon surfaces as 503 quickly instead of hanging the
    # dashboard poll.
    timeout = httpx.Timeout(connect=2.0, read=120.0, write=30.0, pool=5.0)

    client = _build_client(timeout)
    try:
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
            return Response(
                content=json.dumps(envelope).encode("utf-8"),
                status_code=503,
                media_type="application/json",
            )
        except httpx.HTTPError as exc:
            envelope = {
                "error": {
                    "code": "lemonade.proxy_error",
                    "message": "error forwarding request to lemonade",
                    "details": {"target": target_url, "reason": str(exc)},
                }
            }
            return Response(
                content=json.dumps(envelope).encode("utf-8"),
                status_code=502,
                media_type="application/json",
            )
    finally:
        await client.aclose()

    response_headers = _filter_response_headers(upstream_resp.headers)
    media_type = upstream_resp.headers.get("content-type")

    return Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        headers=response_headers,
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
