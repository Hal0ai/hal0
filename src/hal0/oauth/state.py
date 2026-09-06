"""In-process OAuth state-nonce store.

Mirrors :class:`hal0.security.ratelimit.SlidingWindowRateLimiter`: hal0 is a
single-node appliance, so there is no shared store to coordinate — one
hal0-api process owns one :class:`OAuthStateStore` instance, handed out on
``app.state`` by ``create_app()``. Nonces are the CSRF/replay defense for the
OAuth ``state`` parameter (RFC 6749 §10.12) and are single-use: a successful
``pop`` deletes the entry, so a replayed callback with the same ``state``
finds nothing and is refused.

A nonce additionally carries the PKCE code verifier (when the provider uses
PKCE) and the provider id, bound at ``start`` time — the callback handler
resolves both from the nonce rather than trusting anything in the incoming
redirect's query string, closing the same "attacker picks the provider"
class of bug ODS's file-based nonce design documents.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

DEFAULT_TTL_SECONDS = 600  # 10 min — comfortably above a provider's own code TTL.


@dataclass(frozen=True)
class OAuthNonce:
    provider_id: str
    code_verifier: str | None
    created_at: float


class OAuthStateStore:
    """Single-use, TTL-bounded state-nonce store. Thread-safe."""

    def __init__(self, *, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._nonces: dict[str, OAuthNonce] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        expired = [
            state for state, nonce in self._nonces.items() if now - nonce.created_at > self._ttl
        ]
        for state in expired:
            self._nonces.pop(state, None)

    def issue(self, provider_id: str, *, code_verifier: str | None = None) -> str:
        """Mint a fresh, single-use ``state`` value bound to ``provider_id``."""
        now = time.time()
        with self._lock:
            self._prune(now)
            state = secrets.token_urlsafe(32)
            self._nonces[state] = OAuthNonce(
                provider_id=provider_id, code_verifier=code_verifier, created_at=now
            )
        return state

    def pop(self, state: str) -> OAuthNonce | None:
        """Consume ``state``, returning its nonce, or None if unknown/expired.

        Single-use: a second call with the same ``state`` always returns
        None, closing the replay window a race between two callbacks could
        otherwise open.
        """
        now = time.time()
        with self._lock:
            self._prune(now)
            return self._nonces.pop(state, None)
