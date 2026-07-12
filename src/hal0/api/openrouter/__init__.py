"""OpenRouter integration surface (Phase 0 scaffold).

This package owns hal0-api's side of the OpenRouter BYOK + delegate
flow. v0.3.x ships only the route scaffold + loopback guard so V1
(the OpenRouter-as-Hermes-upstream PR) inherits a baseline that
respects the auth-removed posture from day 1.

The actual PKCE exchange flow lands in V1 (Phase 1) on top of this
scaffold.
"""

from hal0.api.openrouter.auth import router

__all__ = ["router"]
