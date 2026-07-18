"""In-process sliding-window rate limiter (KB-1 hardening tail).

A tiny, dependency-free limiter used to blunt brute-force against the
``POST /api/auth/login`` endpoint (KB-1 / §1 hardening). It is deliberately
*process-local*: hal0 is a single-node appliance, so there is no shared
store to coordinate — one API process owns one limiter instance, handed out
on ``app.state`` by ``create_app()`` (a fresh instance per app keeps tests
isolated).

The window is a true sliding window: each key keeps a bounded deque of the
monotonic timestamps of its recent events; entries older than the window are
evicted on every check. A key is allowed until it has accrued ``max_events``
within ``window_s``; the next event inside the window is refused. Refused
events are NOT recorded, so a caller that keeps hammering while blocked does
not extend its own lockout indefinitely — it clears as soon as the oldest
recorded event ages out.

The clock is injectable so tests can drive it deterministically without
sleeping (mirrors the ``now``-parameter convention used across hal0's cookie
and pull machinery).
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

DEFAULT_MAX_EVENTS = 10
DEFAULT_WINDOW_SECONDS = 60.0


class SlidingWindowRateLimiter:
    """Per-key sliding-window limiter. Thread-safe (login is low-frequency)."""

    def __init__(
        self,
        *,
        max_events: int = DEFAULT_MAX_EVENTS,
        window_s: float = DEFAULT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_events < 1:
            raise ValueError("max_events must be >= 1")
        if window_s <= 0:
            raise ValueError("window_s must be > 0")
        self._max = max_events
        self._window = window_s
        self._clock = clock
        # A monotonic-timestamp deque per key; each is bounded by construction
        # (never grows past ``max_events`` because we refuse before appending).
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, dq: deque[float], now: float) -> None:
        cutoff = now - self._window
        while dq and dq[0] <= cutoff:
            dq.popleft()

    def allow(self, key: str) -> bool:
        """Record an event for ``key`` and return whether it is permitted.

        Returns ``True`` and records the event when the key is under budget;
        returns ``False`` WITHOUT recording once the window is saturated.
        """
        now = self._clock()
        with self._lock:
            dq = self._hits[key]
            self._prune(dq, now)
            if len(dq) >= self._max:
                # Keep the map from leaking deques we no longer need to grow.
                return False
            dq.append(now)
            return True

    def retry_after(self, key: str) -> float:
        """Seconds until the key's oldest in-window event expires (0 if free)."""
        now = self._clock()
        with self._lock:
            dq = self._hits.get(key)
            if not dq:
                return 0.0
            self._prune(dq, now)
            if len(dq) < self._max or not dq:
                return 0.0
            return max(0.0, (dq[0] + self._window) - now)

    def reset(self) -> None:
        """Drop all recorded events (test isolation / manual clear)."""
        with self._lock:
            self._hits.clear()


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 1 else default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def login_limiter_from_env() -> SlidingWindowRateLimiter:
    """Build the login limiter, honouring env overrides.

    ``HAL0_LOGIN_RATELIMIT_MAX`` (default 10) and
    ``HAL0_LOGIN_RATELIMIT_WINDOW_S`` (default 60) tune the budget. The
    defaults still let a fat-fingered operator retry a handful of times a
    minute while cutting an automated key-guessing loop down to at most 10
    tries/minute/IP.
    """
    return SlidingWindowRateLimiter(
        max_events=_int_env("HAL0_LOGIN_RATELIMIT_MAX", DEFAULT_MAX_EVENTS),
        window_s=_float_env("HAL0_LOGIN_RATELIMIT_WINDOW_S", DEFAULT_WINDOW_SECONDS),
    )


__all__ = [
    "DEFAULT_MAX_EVENTS",
    "DEFAULT_WINDOW_SECONDS",
    "SlidingWindowRateLimiter",
    "login_limiter_from_env",
]
