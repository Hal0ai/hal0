"""OpenRouter integration surface (Phase 0 scaffold, ADR-0020).

This package owns hal0-api's side of the OpenRouter BYOK + delegate
flow. v0.3.x ships only the route scaffold + loopback guard so V1
(the OpenRouter-as-Hermes-upstream PR) inherits a baseline that
respects ADR-0012's auth-removed posture from day 1.

See ``docs/internal/adr/0020-localhost-callback-only-oauth-pkce.md``
for the architectural decision; the actual PKCE exchange flow lands in
V1 (Phase 1) on top of this scaffold.
"""

from hal0.api.openrouter.auth import router

__all__ = ["router"]
