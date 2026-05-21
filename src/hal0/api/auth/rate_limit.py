"""IP-bucket rate limiter for the auth surface (FINDINGS §32).

A small in-memory token bucket keyed by source IP. Used to throttle
``POST /api/auth/login`` and ``POST /api/auth/password`` so an attacker
on the LAN can't mount a meaningful dictionary attack against the
owner password even when bcrypt cost 12 is the only per-attempt cost.

Design notes:

  - In-process only — a restart resets the counter. That's fine. The
    attacker still pays the bcrypt cost per attempt, and the legitimate
    operator restart cadence is much rarer than a brute-force loop.
  - Keyed by ``request.client.host`` which Starlette resolves from the
    socket peer. Behind a reverse proxy this is the proxy address; the
    deployment already requires the proxy to live on the same host, so
    the loopback fall-through is fine. ``X-Forwarded-For`` is NOT
    trusted (an attacker could forge it to evade the limit).
  - The bucket is a simple count-per-window: N attempts in
    ``window_seconds`` ⇒ 429 with ``Retry-After`` set to the time until
    the oldest event in the window ages out.
  - Defaults: 5 attempts per 60s. Tunable per-route via constructor.

Usage pattern in routes::

    from hal0.api.auth.rate_limit import RateLimitExceeded, check_rate_limit

    try:
        check_rate_limit(request, scope="login")
    except RateLimitExceeded as exc:
        # Build a JSONResponse with the Retry-After header — the global
        # Hal0Error handler can't set per-response headers, so the route
        # constructs the response directly.

The limiter is attached to ``request.app.state.auth_rate_limiter`` by
:func:`install`, mirroring the existing event-bus + token-store wiring.
"""

from __future__ import annotations

import collections
import threading
import time
from collections.abc import Callable

import structlog
from fastapi import FastAPI, Request

from hal0.errors import Hal0Error

log = structlog.get_logger(__name__)

# Per-scope defaults. Tuned to (a) be invisible during legitimate use
# (a wizard re-attempts after a typo'd password 2-3 times) and (b)
# meaningful against a brute-force loop (5 attempts/minute = 7200/day,
# vs. an unthrottled bcrypt-only loop at ~4 attempts/sec = 345600/day).
_DEFAULT_LIMIT: int = 5
_DEFAULT_WINDOW_SECONDS: float = 60.0


class RateLimitExceeded(Hal0Error):
    """Raised when an IP exceeds the per-scope attempt cap.

    The route catches this and turns it into a 429 response with a
    ``Retry-After`` header (computed from :attr:`retry_after_seconds`).
    Carrying ``retry_after_seconds`` on the exception means the
    constructor stays callable from anywhere in the stack while the
    HTTP-shape concern stays in the route.
    """

    code = "auth.rate_limited"
    status = 429

    def __init__(self, message: str, *, retry_after_seconds: int, scope: str) -> None:
        super().__init__(
            message,
            details={
                "retry_after_seconds": retry_after_seconds,
                "scope": scope,
            },
        )
        self.retry_after_seconds = retry_after_seconds
        self.scope = scope


class IpRateLimiter:
    """Token-bucket-ish counter keyed by (scope, ip).

    Each (scope, ip) tracks a deque of monotonic timestamps. On a new
    attempt we evict timestamps older than ``window_seconds`` and then
    test the remaining length against ``limit``. The deque grows
    bounded by ``limit + 1`` (the +1 is the just-pushed event used to
    compute retry_after); old IPs that stop attempting are pruned by
    :meth:`_evict_idle` opportunistically.

    Thread-safe: an internal Lock guards the bucket dict + per-IP
    deques. The test suite and Starlette's threadpool may both touch
    the limiter; the lock is uncontended in the steady state because
    the critical section is microseconds.
    """

    def __init__(
        self,
        *,
        limit: int = _DEFAULT_LIMIT,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        # Test seam: tests inject a fake clock to drive the
        # "after 1min the limit resets" assertion without sleep().
        self._clock = clock or time.monotonic
        self._buckets: dict[tuple[str, str], collections.deque[float]] = {}
        self._lock = threading.Lock()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def window_seconds(self) -> float:
        return self._window

    def check(self, *, scope: str, ip: str) -> None:
        """Record an attempt and raise ``RateLimitExceeded`` if over the cap.

        The "record then test" order matches typical rate-limiter
        semantics: the Nth attempt is allowed, the (N+1)th is blocked.
        With ``limit=5`` the 6th attempt within ``window`` returns 429.
        """
        now = self._clock()
        key = (scope, ip)
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = collections.deque(maxlen=self._limit + 1)
                self._buckets[key] = bucket
            # Evict events older than window before counting.
            cutoff = now - self._window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            # Push the current attempt onto the bucket. We push BEFORE
            # the over-limit test so a stuck attacker sees the
            # Retry-After climb (oldest entry still inside the window).
            bucket.append(now)
            if len(bucket) > self._limit:
                # Retry-After: seconds until the oldest in-window event
                # ages out of the bucket. Always at least 1 so clients
                # don't get a Retry-After: 0 race-friendly value.
                oldest = bucket[0]
                retry = max(1, int(self._window - (now - oldest)) + 1)
                raise RateLimitExceeded(
                    f"too many attempts for scope {scope!r}; retry later",
                    retry_after_seconds=retry,
                    scope=scope,
                )

    def reset(self, *, scope: str | None = None, ip: str | None = None) -> None:
        """Clear bucket(s). Test-only helper.

        Drops entries matching the filter; ``scope=None, ip=None`` is
        "drop everything". Useful in tests where the same TestClient
        runs multiple login matrices against a single app instance.
        """
        with self._lock:
            if scope is None and ip is None:
                self._buckets.clear()
                return
            keys = [
                k
                for k in self._buckets
                if (scope is None or k[0] == scope) and (ip is None or k[1] == ip)
            ]
            for k in keys:
                self._buckets.pop(k, None)


def client_ip(request: Request) -> str:
    """Resolve the caller's IP for rate-limit keying.

    Uses ``request.client.host`` directly (Starlette's resolved peer
    address). ``X-Forwarded-For`` is deliberately ignored — trusting it
    would let an attacker spoof a unique-per-attempt key and evade the
    limiter entirely. If the deployment is behind a reverse proxy on
    the same host, every attempt collapses to the proxy's loopback
    address; that's fine, it's the rate-limit cap that matters.
    """
    client = request.client
    if client is None:
        # Starlette omits ``client`` in some test contexts; treat as
        # an "unknown" bucket so a missing-attribute path still
        # exercises the limiter rather than skipping it.
        return "unknown"
    return client.host or "unknown"


def check_rate_limit(request: Request, *, scope: str) -> None:
    """Convenience wrapper used from routes.

    Pulls the limiter off ``request.app.state.auth_rate_limiter`` (set
    by :func:`install`) and calls :meth:`IpRateLimiter.check`. Routes
    keep this one-liner shape so the rate-limit concern doesn't grow a
    tail of imports per call site.
    """
    limiter: IpRateLimiter | None = getattr(request.app.state, "auth_rate_limiter", None)
    if limiter is None:
        # Defensive: if the install step was skipped (e.g. a test
        # constructs a TestClient without our wiring), the limiter is
        # absent and the check is a no-op. Production goes through
        # create_app() which always installs.
        return
    ip = client_ip(request)
    try:
        limiter.check(scope=scope, ip=ip)
    except RateLimitExceeded:
        log.warning(
            "auth.rate_limited",
            client_ip=ip,
            scope=scope,
            limit=limiter.limit,
            window_seconds=limiter.window_seconds,
        )
        raise


def install(app: FastAPI, *, limiter: IpRateLimiter | None = None) -> IpRateLimiter:
    """Attach the limiter to ``app.state.auth_rate_limiter``.

    Called from :func:`hal0.api.create_app` after the auth router is
    wired. The optional ``limiter`` parameter is a test seam: pass a
    pre-built ``IpRateLimiter`` with a stub clock to drive the
    "after 1min, attempts succeed again" assertion deterministically.
    """
    if limiter is None:
        limiter = IpRateLimiter()
    app.state.auth_rate_limiter = limiter
    return limiter


__all__ = [
    "IpRateLimiter",
    "RateLimitExceeded",
    "check_rate_limit",
    "client_ip",
    "install",
]
