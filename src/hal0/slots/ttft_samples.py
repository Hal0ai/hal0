"""Per-slot TTFT samples + fleet-wide aggregation.

Hooked into the request path by `api/routes/v1.py`:

  * `_dispatch_and_forward` records ``t_start`` at dispatch time and
    threads it into the streaming response wrapper.
  * `_instrument_streaming_throughput` marks the first non-empty
    emitted chunk and appends ``(monotonic_ts, ttft_seconds)`` to the
    per-slot deque on ``app.state.ttft_events``.

Surfaced by `api/routes/slots.py:_local_slot_metrics` as
``ttft_seconds`` (latest in-window sample) and ``ttft_avg_seconds``
(mean over the window) per slot, plus fleet-wide averages used by
the dashboard's throughput card.

The shape of this module is mirrored in
``scripts/prototype_ttft/metrics_core.py`` — the teaching TUI. Keep
them in sync; see ``docs/internal/metrics-prototype.md``.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field

# 60s window matches what the prototype TUI defaults to — long enough
# that a quiet slot's most recent sample still shows in the UI, short
# enough that a sample from a stale workload ages out before it
# misleads someone reading the throughput card.
DEFAULT_WINDOW_S = 60.0


@dataclass
class SlotSamples:
    """Rolling TTFT samples + inflight-request map for one slot.

    Samples are ``(monotonic_ts, ttft_seconds)``. A sample older than
    ``window_s`` is treated as stale (excluded from current/avg reads).
    ``maxlen`` is a memory bound only; the window is the real cutoff,
    so a burst of requests can't push a recent slow sample out of view.
    """

    window_s: float = DEFAULT_WINDOW_S
    ttft_samples: deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=128))
    inflight: dict[str, float] = field(default_factory=dict)

    def request_started(self, req_id: str, now: float | None = None) -> None:
        self.inflight[req_id] = time.monotonic() if now is None else now

    def first_chunk(self, req_id: str, now: float | None = None) -> float | None:
        """Record TTFT for ``req_id``. Returns the TTFT in seconds, or
        ``None`` if the request wasn't tracked (already completed,
        never started, or cancelled)."""
        start = self.inflight.pop(req_id, None)
        if start is None:
            return None
        end = time.monotonic() if now is None else now
        ttft = max(0.0, end - start)
        self.ttft_samples.append((end, ttft))
        return ttft

    def request_cancelled(self, req_id: str) -> None:
        self.inflight.pop(req_id, None)

    def _recent(self, now: float | None = None) -> list[float]:
        cutoff = (time.monotonic() if now is None else now) - self.window_s
        return [t for ts, t in self.ttft_samples if ts >= cutoff]

    def current_ttft(self, now: float | None = None) -> float | None:
        recent = self._recent(now)
        return recent[-1] if recent else None

    def avg_ttft(self, now: float | None = None) -> float | None:
        recent = self._recent(now)
        return sum(recent) / len(recent) if recent else None

    def sample_count(self, now: float | None = None) -> int:
        return len(self._recent(now))


def avg_ttft_across(slots: Iterable[SlotSamples], now: float | None = None) -> float | None:
    """Mean of per-slot avg TTFT across slots that have data.

    Equally weights slots — one slot's churn doesn't drown another's
    single sample. Returns ``None`` when no slot has any in-window
    sample so the UI can render '—' instead of a misleading zero.
    """
    per_slot = [s.avg_ttft(now) for s in slots]
    present = [v for v in per_slot if v is not None]
    if not present:
        return None
    return sum(present) / len(present)


def avg_kv_cache_across(kv_cache: dict[str, float]) -> float | None:
    """Mean KV-cache ratio across slots that report one.

    Non-llama slots aren't in the dict (their scrape returns no
    ``kv_cache_usage`` key), so they're naturally excluded — no
    explicit filter is needed.
    """
    if not kv_cache:
        return None
    return sum(kv_cache.values()) / len(kv_cache)


def samples_from_events(
    events: deque[tuple[float, float]],
    window_s: float = DEFAULT_WINDOW_S,
) -> SlotSamples:
    """Adapt a raw ``app.state.ttft_events[slot]`` deque to a
    ``SlotSamples`` view for read-only aggregation.

    The capture path appends directly to the deque to keep allocation
    out of the hot streaming loop; this is the lazy view used at
    /api/slots/metrics serialisation time.
    """
    s = SlotSamples(window_s=window_s)
    s.ttft_samples = events
    return s
