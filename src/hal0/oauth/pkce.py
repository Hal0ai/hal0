"""PKCE (RFC 7636) verifier/challenge generation for the OAuth passthrough.

Used by :mod:`hal0.api.routes.oauth` when a provider's registry entry sets
``pkce = true`` — the code verifier is held server-side in the state nonce
(:mod:`hal0.oauth.state`) between ``start`` and ``callback`` so no client
(dashboard tab, CLI, agent) ever needs to see it.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass

# RFC 7636 §4.1: 43..128 chars of [A-Z a-z 0-9 - . _ ~]. token_urlsafe(64)
# yields 86 base64url chars (no padding), comfortably inside that range.
_VERIFIER_BYTES = 64


@dataclass(frozen=True)
class PkcePair:
    verifier: str
    challenge: str
    method: str = "S256"


def generate_pkce_pair() -> PkcePair:
    """Generate a fresh (verifier, S256 challenge) pair."""
    verifier = secrets.token_urlsafe(_VERIFIER_BYTES)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return PkcePair(verifier=verifier, challenge=challenge)
