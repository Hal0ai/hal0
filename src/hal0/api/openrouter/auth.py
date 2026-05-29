"""OpenRouter OAuth PKCE callback route (Phase 0 scaffold).

This module registers ``GET /api/openrouter/auth/callback`` so the URL
exists, is reachable, and enforces the loopback guard from ADR-0020
before V1 lands the actual PKCE exchange flow. The handler returns
HTTP 501 with a pointer to ADR-0020 — V1's PR opens against this
branch and fills the body in.

See ``docs/internal/adr/0020-localhost-callback-only-oauth-pkce.md``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from hal0.api.openrouter._loopback import require_loopback

router = APIRouter()


@router.get(
    "/api/openrouter/auth/callback",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    tags=["openrouter", "auth"],
    summary="OpenRouter OAuth PKCE callback (scaffold; V1 lands the exchange)",
)
async def callback(
    request: Request,
    _loopback: None = Depends(require_loopback),
) -> JSONResponse:
    """Receive the OAuth authorization code from OpenRouter.

    Phase 0 (this PR) registers the route + loopback guard only. The
    PKCE code-for-token exchange + refresh-token persistence ship in
    V1 (Phase 1) — see ADR-0020 §"Implementation pointer".

    The 501 response is deliberate: it documents the contract for V1
    and lets the dashboard's "Linked Accounts" panel detect that the
    callback URL is reachable before exposing a button that would
    otherwise hang.
    """
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content={
            "detail": ("callback wired by V1; PR-#### lands the exchange flow"),
            "adr": "ADR-0020",
        },
    )
