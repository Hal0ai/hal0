"""HuggingFace Hub discovery endpoints (mounted under ``/api/hf``).

Issue #311 — the dashboard's "Search HF" button is stubbed with an info
toast. This module ships the first piece of the fix: a small proxy
against HF's public model search API
(``https://huggingface.co/api/models?search=…&pipeline_tag=…&limit=…``)
that returns a typed, capped list for the UI to render.

We deliberately do NOT add a dependency on ``huggingface_hub`` — the
public HTTP endpoint is the same shape ``HfApi().list_models`` wraps,
and the inspect sibling already does the same dance with ``httpx``
(see :mod:`hal0.api.routes.models`). Adding a second client library
just to talk to the same URL is the wrong trade-off for one route.

Error policy: the route must never 500 the dashboard. A transport
failure, 5xx upstream, or unparseable body degrades to an empty
result list. The UI renders an "no results" empty state in that case
without flickering the toast queue.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
import structlog
from fastapi import APIRouter

logger = structlog.get_logger(__name__)

router = APIRouter()

# Hard cap on rows returned to the dashboard. Keeps the wire payload
# bounded so a wide search doesn't drag the renderer down; HF accepts
# ``limit=`` up to 100 so we ask for a little more than we surface in
# case HF swaps ordering (e.g. trending) between requests.
_HF_RESULT_CAP = 20
_HF_UPSTREAM_LIMIT = 30
_HF_SEARCH_TIMEOUT_S = 5.0
_HF_CACHE_TTL_S = 30.0

# In-process TTL cache. The dashboard's "Search HF" panel debounces a
# keystroke to a single fetch; this short cache collapses concurrent
# identical lookups (e.g. rapid filter toggles) onto one upstream call
# while still picking up a freshly-uploaded repo within ~30s.
_SEARCH_CACHE: dict[tuple[str, str, int], tuple[float, list[dict[str, Any]]]] = {}


def _cache_key(q: str, type_filter: str, limit: int) -> tuple[str, str, int]:
    """Normalised cache key (lowercased + trimmed) so casing differences hit cache."""
    return (q.strip().lower(), type_filter.strip().lower(), limit)


def _normalise_row(entry: Any) -> dict[str, Any] | None:
    """Project an HF models-list row onto the dashboard's flat shape.

    HF occasionally returns nulls or non-dict entries between real rows
    (the public list endpoint has a few soft spots when the index is
    being rebuilt); we drop those rather than 500 the caller. Numeric
    counters default to 0; ``gated`` is surfaced verbatim — HF uses
    ``false`` for open repos and a string ("manual", "auto", or
    sometimes a model id) for gated ones.
    """
    if not isinstance(entry, dict):
        return None
    model_id = entry.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    downloads = entry.get("downloads") or 0
    likes = entry.get("likes") or 0
    gated = entry.get("gated", False)
    pipeline_tag = entry.get("pipeline_tag") or ""
    library = entry.get("library_name") or ""
    last_modified = entry.get("last_modified") or ""
    return {
        "id": model_id,
        "downloads": int(downloads) if isinstance(downloads, (int, float)) else 0,
        "likes": int(likes) if isinstance(likes, (int, float)) else 0,
        "gated": gated,
        "pipeline_tag": str(pipeline_tag),
        "library": str(library),
        "last_modified": str(last_modified),
    }


async def _fetch_hf_search(q: str, type_filter: str, limit: int) -> list[dict[str, Any]]:
    """Hit HF's public models list and project it onto the row shape.

    Caller is responsible for capping; this returns whatever HF gave us
    after the ``limit=`` hint. Returns ``[]`` on every failure path
    with a single structlog line so operators can trace the cause
    without a 500 cluttering the dashboard's toast queue.
    """
    headers: dict[str, str] = {"Accept": "application/json"}
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"

    params: dict[str, str | int] = {
        "search": q,
        "limit": limit,
        "full": "false",
    }
    if type_filter:
        params["pipeline_tag"] = type_filter

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_HF_SEARCH_TIMEOUT_S),
            follow_redirects=True,
            headers=headers,
        ) as client:
            resp = await client.get("https://huggingface.co/api/models", params=params)
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        logger.warning(
            "hf_search_unreachable",
            q=q,
            type=type_filter,
            error=exc.__class__.__name__,
            detail=str(exc),
        )
        return []

    if resp.status_code >= 400:
        logger.warning(
            "hf_search_upstream_error",
            q=q,
            type=type_filter,
            status=resp.status_code,
        )
        return []

    try:
        payload = resp.json()
    except ValueError:
        logger.warning("hf_search_bad_json", q=q, type=type_filter)
        return []

    if not isinstance(payload, list):
        logger.warning("hf_search_unexpected_shape", q=q, type=type_filter)
        return []

    out: list[dict[str, Any]] = []
    for entry in payload:
        row = _normalise_row(entry)
        if row is None:
            continue
        out.append(row)
        if len(out) >= _HF_RESULT_CAP:
            break
    return out


@router.get("/search")
async def hf_search(
    q: str | None = None,
    type: str | None = None,
    limit: int = _HF_RESULT_CAP,
) -> dict[str, Any]:
    """Free-text search the HuggingFace Hub model catalog.

    Query params (all optional except ``q``, which is the trigger):

    * ``q``     — free-text query forwarded to HF as ``search=``.
    * ``type``  — pipeline_tag filter (e.g. ``text-generation``,
      ``feature-extraction``). Omitted → no filter.
    * ``limit`` — how many rows the *dashboard* wants; we ask HF for a
      few more and then cap at :data:`_HF_RESULT_CAP` so a wide query
      can't blow up the wire payload.

    Returns ``{"results": [...]}`` where each row is the normalised
    shape (:func:`_normalise_row`). All failure paths degrade to
    ``{"results": []}`` so the dashboard renders an "no results" empty
    state instead of a 500 toast.
    """
    q_norm = (q or "").strip()
    type_norm = (type or "").strip()
    # Empty ``q`` would be a wasted upstream call — return cheap empty
    # and skip the cache too. The dashboard debounces an empty input
    # into a no-op so the user sees an empty result box immediately.
    if not q_norm:
        return {"results": []}

    cap = max(1, min(int(limit or _HF_RESULT_CAP), _HF_RESULT_CAP))
    cache_key = _cache_key(q_norm, type_norm, cap)
    now = time.monotonic()
    cached = _SEARCH_CACHE.get(cache_key)
    if cached is not None and (now - cached[0]) < _HF_CACHE_TTL_S:
        return {"results": cached[1]}

    rows = await _fetch_hf_search(q_norm, type_norm, _HF_UPSTREAM_LIMIT)
    _SEARCH_CACHE[cache_key] = (now, rows)
    return {"results": rows}
