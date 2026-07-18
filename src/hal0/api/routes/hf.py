"""HuggingFace Hub discovery endpoints (mounted under ``/api/hf``).

Issue #311 — the dashboard's "Search HF" button is stubbed with an info
toast. This module ships the first piece of the fix: a small proxy
against HF's public model search API
(``https://huggingface.co/api/models?search=…&pipeline_tag=…&limit=…``)
that returns a typed, capped list for the UI to render.

The actual HF transport call + row normalisation lives in
:mod:`hal0.upstreams.huggingface` (shared with the Add-by-HF "Inspect"
modal's repo fetch); this route owns the request validation and the
short-lived TTL cache that collapses rapid dashboard filter toggles onto
one upstream call.

Error policy: the route must never 500 the dashboard. A transport
failure, 5xx upstream, or unparseable body degrades to an empty
result list. The UI renders an "no results" empty state in that case
without flickering the toast queue.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter

from hal0.upstreams.huggingface import HF_SEARCH_RESULT_CAP as _HF_RESULT_CAP
from hal0.upstreams.huggingface import search_models as _fetch_hf_search

router = APIRouter()

_HF_CACHE_TTL_S = 30.0

# In-process TTL cache. The dashboard's "Search HF" panel debounces a
# keystroke to a single fetch; this short cache collapses concurrent
# identical lookups (e.g. rapid filter toggles) onto one upstream call
# while still picking up a freshly-uploaded repo within ~30s.
_SEARCH_CACHE: dict[tuple[str, str, int], tuple[float, list[dict[str, Any]]]] = {}


def _cache_key(q: str, type_filter: str, limit: int) -> tuple[str, str, int]:
    """Normalised cache key (lowercased + trimmed) so casing differences hit cache."""
    return (q.strip().lower(), type_filter.strip().lower(), limit)


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
    shape (see :func:`hal0.upstreams.huggingface.search_models`). All
    failure paths degrade to ``{"results": []}`` so the dashboard
    renders an "no results" empty state instead of a 500 toast.
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

    rows = await _fetch_hf_search(q_norm, type_norm)
    _SEARCH_CACHE[cache_key] = (now, rows)
    return {"results": rows}
