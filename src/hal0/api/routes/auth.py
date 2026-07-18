"""KB-1 / §1 auth surface: ``POST /api/auth/login`` + ``GET /api/auth/status``.

Both routes are OPEN in :mod:`hal0.security.exposure` (they have to be --
you can't require a session cookie to obtain one, and the dashboard needs
``/status`` to render its own auth gate before any credential exists).

Mounted at ``/api/auth`` in ``create_app()``.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from hal0.api.agents._auth import set_session_cookie
from hal0.api.auth import (
    has_admin_key,
    require_auth_enabled,
    resolve_principal_from_scope,
    verify_admin_key,
)
from hal0.errors import Unauthorized

log = structlog.get_logger(__name__)

router = APIRouter()


class LoginRequest(BaseModel):
    key: str


@router.post("/login")
async def login(body: LoginRequest, response: Response) -> dict[str, object]:
    """Validate ``key`` against ``HAL0_ADMIN_KEY`` and mint the session cookie.

    Reuses the exact HMAC cookie machinery ``agents/_auth.py`` already
    ships for the chat-proxy WS gate (seam S9's cookie half) -- one
    cookie scheme, one verifier, admin-equivalent everywhere it's
    accepted. There is no client-key login: the client tier is
    Bearer/``?api_key=``-only by design (it's meant for programmatic /
    embedded callers, not a browser session).
    """
    if not verify_admin_key(body.key):
        log.warning("hal0.auth.login_failed")
        raise Unauthorized("invalid key", code="auth.invalid_key")
    set_session_cookie(response)
    log.info("hal0.auth.login_ok")
    return {"ok": True, "tier": "admin"}


@router.get("/status")
async def status(request: Request) -> dict[str, object]:
    """Posture report for the dashboard's own auth gate. Never returns secrets.

    ``auth_required`` lets the UI decide whether to show a login screen at
    all (dev-open installs skip it entirely); ``has_admin_key`` lets it
    distinguish "not configured yet" (first-run bootstrap window) from
    "configured, please log in"; ``tier`` is this caller's own resolved
    identity (useful for the dashboard to know if it's already logged in
    via an existing cookie).
    """
    principal = resolve_principal_from_scope(request)
    return {
        "auth_required": require_auth_enabled(),
        "has_admin_key": has_admin_key(),
        "tier": principal.tier,
    }


__all__ = ["router"]
