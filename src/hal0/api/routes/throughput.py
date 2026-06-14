"""Throughput history endpoint — ``GET /api/stats/throughput/history``.

Returns bucketed token-throughput over a rolling time window, computed
from the per-slot rolling deques populated by
``v1._instrument_streaming_throughput``.

The data source (``app.state.tps_events``) stores monotonic timestamps;
this endpoint converts them to epoch-seconds once per request so the
response carries wall-clock timestamps the front-end can plot directly.

Route is registered with prefix ``/api`` by the lead router, so the
path below is relative (``/stats/throughput/history``).
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

import structlog
from fastapi import APIRouter, Request

router = APIRouter()
log = structlog.get_logger(__name__)

_BUCKETS_MIN = 1
_BUCKETS_MAX = 120
_WINDOW_MIN = 5
_WINDOW_MAX = 3600


@router.get("/stats/throughput/history")
def throughput_history(
    request: Request,
    buckets: int = 20,
    window_s: int = 100,
) -> dict[str, Any]:
    """Bucketed TPS history over the last ``window_s`` seconds.

    Query params
    ------------
    buckets : int
        Number of equal-width bins (clamped to 1-120, default 20).
    window_s : int
        Look-back window in seconds (clamped to 5-3600, default 100).

    Response shape
    --------------
    .. code-block:: json

        {
          "window_s": 100,
          "bucket_s": 5.0,
          "samples": [
            {"ts": 1718000005.0, "total_tps": 142.3, "serving_slots": 2},
            ...
          ],
          "per_slot": {
            "primary": [142.3, ...],
            "embed":   [0.0, ...]
          }
        }

    ``samples`` is ordered oldest → newest and contains ONLY bins that
    had at least one event (empty leading bins are omitted; the FE pads
    left).  ``per_slot`` arrays are aligned to ``samples`` (same length
    and order).
    """
    # ── param clamp ───────────────────────────────────────────────────────
    buckets = max(_BUCKETS_MIN, min(_BUCKETS_MAX, int(buckets)))
    window_s = max(_WINDOW_MIN, min(_WINDOW_MAX, int(window_s)))
    bucket_s: float = window_s / buckets

    # ── empty-store fast-path ─────────────────────────────────────────────
    store: defaultdict | None = getattr(request.app.state, "tps_events", None)
    empty_response: dict[str, Any] = {
        "window_s": window_s,
        "bucket_s": bucket_s,
        "samples": [],
        "per_slot": {},
    }
    if not store:
        return empty_response

    # ── monotonic → epoch offset (computed once per request) ──────────────
    mono_now = time.monotonic()
    epoch_now = time.time()
    epoch_offset = epoch_now - mono_now  # ev_epoch = ev_mono + epoch_offset

    window_start_mono = mono_now - window_s

    # ── bucket accumulators ───────────────────────────────────────────────
    # bin_index 0 = oldest, bin_index (buckets-1) = newest
    # bin right-edge mono = window_start_mono + (bin_index+1) * bucket_s
    #
    # For event at mono ts t: bin = int((t - window_start_mono) / bucket_s)
    # clamped to [0, buckets-1].

    # total tokens per bin across all slots
    bin_tokens: list[float] = [0.0] * buckets
    # distinct slot names per bin
    bin_slots: list[set[str]] = [set() for _ in range(buckets)]
    # per-slot tokens per bin
    per_slot_bins: dict[str, list[float]] = {}

    for slot_name, deque in store.items():
        slot_bins: list[float] = [0.0] * buckets
        for mono_ts, tokens in deque:
            if mono_ts < window_start_mono:
                continue
            raw_idx = int((mono_ts - window_start_mono) / bucket_s)
            idx = max(0, min(buckets - 1, raw_idx))
            tok = float(tokens)
            bin_tokens[idx] += tok
            bin_slots[idx].add(slot_name)
            slot_bins[idx] += tok
        per_slot_bins[slot_name] = slot_bins

    # ── build output (skip empty bins; FE pads left) ──────────────────────
    samples: list[dict[str, Any]] = []
    emitted_indices: list[int] = []

    for idx in range(buckets):
        if not bin_slots[idx]:
            continue  # no data in this bin — skip
        # right edge of this bin in epoch seconds
        right_mono = window_start_mono + (idx + 1) * bucket_s
        ts = right_mono + epoch_offset
        total_tps = bin_tokens[idx] / bucket_s
        samples.append(
            {
                "ts": ts,
                "total_tps": total_tps,
                "serving_slots": len(bin_slots[idx]),
            }
        )
        emitted_indices.append(idx)

    if not samples:
        return empty_response

    # ── per_slot aligned to emitted samples ───────────────────────────────
    per_slot: dict[str, list[float]] = {
        slot_name: [slot_bins[i] / bucket_s for i in emitted_indices]
        for slot_name, slot_bins in per_slot_bins.items()
    }

    return {
        "window_s": window_s,
        "bucket_s": bucket_s,
        "samples": samples,
        "per_slot": per_slot,
    }
