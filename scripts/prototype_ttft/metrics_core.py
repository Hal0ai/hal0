"""Pure logic for per-slot TTFT samples + fleet-wide aggregation.

No FastAPI, httpx, or I/O. Drive it from a TUI (tui.py) or hook it
into the real request path later by calling:

    fleet.slot(slot_name).request_started(req_id)   # at dispatch
    fleet.slot(slot_name).first_chunk(req_id)       # in streaming wrapper
    fleet.set_kv_cache(slot_name, ratio)            # from /metrics scrape

Then read `fleet.avg_ttft()` and `fleet.avg_kv_cache()` for the
throughput card, `slot.current_ttft()` / `fleet.kv_cache.get(name)`
for per-slot tiles.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class SlotSamples:
    """Rolling TTFT samples + inflight-request map for one slot.

    Samples are (monotonic_ts, ttft_seconds). A sample older than
    `window_s` is treated as stale (excluded from current/avg reads).
    The deque cap (maxlen) is just a memory bound — the window is the
    real cutoff so a burst of requests can't push out a recent sample.
    """

    window_s: float = 60.0
    ttft_samples: deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=128))
    inflight: dict[str, float] = field(default_factory=dict)

    def request_started(self, req_id: str, now: float | None = None) -> None:
        self.inflight[req_id] = time.monotonic() if now is None else now

    def first_chunk(self, req_id: str, now: float | None = None) -> float | None:
        """Record TTFT for `req_id`. Returns the TTFT in seconds, or
        None if the request wasn't tracked (already completed, never
        started, or cancelled)."""
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
        """Most recent in-window TTFT sample."""
        recent = self._recent(now)
        return recent[-1] if recent else None

    def avg_ttft(self, now: float | None = None) -> float | None:
        """Mean TTFT across in-window samples."""
        recent = self._recent(now)
        return sum(recent) / len(recent) if recent else None

    def sample_count(self, now: float | None = None) -> int:
        return len(self._recent(now))


@dataclass
class FleetMetrics:
    """All slots' TTFT samples + latest KV-cache reading per slot.

    KV-cache is a gauge (scraped periodically from llama-server's
    /metrics), not a sample stream — so it's just `dict[name, ratio]`.
    Non-llama slots simply don't appear in the dict.
    """

    slots: dict[str, SlotSamples] = field(default_factory=dict)
    kv_cache: dict[str, float] = field(default_factory=dict)
    window_s: float = 60.0

    def slot(self, name: str) -> SlotSamples:
        s = self.slots.get(name)
        if s is None:
            s = SlotSamples(window_s=self.window_s)
            self.slots[name] = s
        return s

    def set_kv_cache(self, name: str, ratio: float | None) -> None:
        """Latest KV-cache reading for a slot. `None` removes it
        (e.g. slot stopped, or scrape failed)."""
        if ratio is None:
            self.kv_cache.pop(name, None)
        else:
            self.kv_cache[name] = max(0.0, min(1.0, float(ratio)))

    def avg_ttft(self, now: float | None = None) -> float | None:
        """Mean of per-slot avg TTFT, across slots that have data.

        Equally weights slots (one slot's churn doesn't drown another's
        single sample). Returns None when no slot has any recent
        sample so the UI can render '—' instead of a misleading zero.
        """
        per_slot = [s.avg_ttft(now) for s in self.slots.values()]
        present = [v for v in per_slot if v is not None]
        if not present:
            return None
        return sum(present) / len(present)

    def avg_kv_cache(self) -> float | None:
        """Mean KV-cache ratio across slots that report one.

        Non-llama slots aren't in self.kv_cache, so they're naturally
        excluded — no filter step needed.
        """
        if not self.kv_cache:
            return None
        return sum(self.kv_cache.values()) / len(self.kv_cache)
