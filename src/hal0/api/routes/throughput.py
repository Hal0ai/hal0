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

import math
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

    ``samples`` is ordered oldest → newest. Empty LEADING bins (before the
    first event in the window) are omitted and the FE pads left; every bin
    from the first event onwards is emitted, an empty one as a measured
    ``total_tps: 0.0`` with ``serving_slots: 0``, so the newest sample reads
    0 as soon as nothing is generating (#3859, ADR-0044 decision 4).
    ``per_slot`` arrays are aligned to ``samples`` (same length and order).
    ``pending_tokens`` (additive, integer) is the token total of the
    still-open interval, which ``samples`` leaves out until it closes.

    Bins are COMPLETED, wall-clock-anchored intervals: right edges sit on
    multiples of ``bucket_s`` epoch seconds and the newest bin ends at the
    last such edge, so the in-progress interval is not reported until it
    closes. A bin's ``ts`` does not shift between polls and its value is
    stable unless a non-streaming completion finishes: its backdated events
    can land in bins already reported (as 0 while it was decoding).
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
        "pending_tokens": 0,
    }
    if not store:
        return empty_response

    # ── monotonic → epoch offset (computed once per request) ──────────────
    mono_now = time.monotonic()
    epoch_now = time.time()
    epoch_offset = epoch_now - mono_now  # ev_epoch = ev_mono + epoch_offset

    # ── anchored bin edges ────────────────────────────────────────────────
    # Right edge of bin idx (0 = oldest, buckets-1 = newest), in epoch
    # seconds: (last_edge_n - (buckets - 1 - idx)) * bucket_s. Computing it
    # from an integer edge index keeps the value bit-identical across polls.
    last_edge_n = math.floor(epoch_now / bucket_s)
    window_end_mono = last_edge_n * bucket_s - epoch_offset
    window_start_mono = window_end_mono - buckets * bucket_s

    # total tokens per bin across all slots
    bin_tokens: list[float] = [0.0] * buckets
    # distinct slot names per bin
    bin_slots: list[set[str]] = [set() for _ in range(buckets)]
    # per-slot tokens per bin
    per_slot_bins: dict[str, list[float]] = {}

    pending_tokens = 0
    for slot_name, deque in store.items():
        slot_bins: list[float] = [0.0] * buckets
        for mono_ts, tokens in deque:
            # The in-progress interval (>= window_end) is reported once it
            # closes; its token total is exposed as ``pending_tokens`` so a
            # reader asking "did anything move recently?" (the bench GPU gate)
            # does not miss traffic from the last, still-open interval.
            if mono_ts >= window_end_mono:
                pending_tokens += int(tokens)
                continue
            if mono_ts < window_start_mono:
                continue
            raw_idx = int((mono_ts - window_start_mono) / bucket_s)
            idx = max(0, min(buckets - 1, raw_idx))
            tok = float(tokens)
            bin_tokens[idx] += tok
            bin_slots[idx].add(slot_name)
            slot_bins[idx] += tok
        per_slot_bins[slot_name] = slot_bins

    # ── build output (skip leading empty bins; FE pads left) ──────────────
    first_idx = next((i for i in range(buckets) if bin_slots[i]), None)
    if first_idx is None:
        return {**empty_response, "pending_tokens": pending_tokens}
    emitted_indices = list(range(first_idx, buckets))

    samples: list[dict[str, Any]] = [
        {
            "ts": (last_edge_n - (buckets - 1 - idx)) * bucket_s,
            "total_tps": bin_tokens[idx] / bucket_s,
            "serving_slots": len(bin_slots[idx]),
        }
        for idx in emitted_indices
    ]

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
        "pending_tokens": pending_tokens,
    }
