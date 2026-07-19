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

from hal0.api.agents._auth import SESSION_COOKIE_NAME, set_session_cookie
from hal0.api.auth import (
    has_admin_key,
    require_auth_enabled,
    resolve_principal_from_scope,
    verify_admin_key,
)
from hal0.config.loader import load_hal0_config, save_hal0_config
from hal0.errors import BadRequest, TooManyRequests, Unauthorized

log = structlog.get_logger(__name__)

router = APIRouter()


class LoginRequest(BaseModel):
    key: str


class RequireAuthRequest(BaseModel):
    require_auth: bool


def _client_ip(request: Request) -> str:
    """Best-effort caller IP for the login rate-limit key.

    Falls back to a constant bucket when the ASGI scope carries no client
    tuple (some test transports) so those requests still share ONE budget
    rather than silently escaping the limiter.
    """
    client = request.client
    return client.host if client is not None else "unknown"


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict[str, object]:
    """Validate ``key`` against ``HAL0_ADMIN_KEY`` and mint the session cookie.

    Reuses the exact HMAC cookie machinery ``agents/_auth.py`` already
    ships for the chat-proxy WS gate (seam S9's cookie half) -- one
    cookie scheme, one verifier, admin-equivalent everywhere it's
    accepted. There is no client-key login: the client tier is
    Bearer/``?api_key=``-only by design (it's meant for programmatic /
    embedded callers, not a browser session).

    Brute-force guard: every attempt (success OR failure) is metered by a
    per-IP sliding-window limiter (``app.state.login_limiter``) BEFORE the
    key is checked, so an automated guesser is capped at a handful of tries
    per minute per source. The check runs first — a blocked caller never
    reaches the constant-time key compare. The limiter is optional on
    ``app.state`` so a bare/mocked app still logs in.
    """
    limiter = getattr(request.app.state, "login_limiter", None)
    if limiter is not None:
        client_ip = _client_ip(request)
        if not limiter.allow(client_ip):
            log.warning("hal0.auth.login_rate_limited", client=client_ip)
            raise TooManyRequests(
                "too many login attempts; slow down and retry shortly",
                code="auth.rate_limited",
                details={"retry_after_s": int(limiter.retry_after(client_ip)) + 1},
            )
    if not verify_admin_key(body.key):
        log.warning("hal0.auth.login_failed")
        raise Unauthorized("invalid key", code="auth.invalid_key")
    set_session_cookie(response)
    log.info("hal0.auth.login_ok")
    return {"ok": True, "tier": "admin"}


@router.post("/logout")
async def logout(response: Response) -> dict[str, object]:
    """Clear the browser session cookie so the operator returns to anonymous.

    OPEN (see :mod:`hal0.security.exposure`): clearing *your own* cookie is
    harmless and must work regardless of posture. The cookie is
    ``HttpOnly`` so JS can never delete it client-side — this route is the
    only way the dashboard can end a session. Deleting a cookie the caller
    doesn't have is a no-op, so an anonymous hit is fine too.
    """
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    log.info("hal0.auth.logout")
    return {"ok": True}


@router.put("/require")
async def set_require_auth(body: RequireAuthRequest, request: Request) -> dict[str, object]:
    """Persist the ``[security].require_auth`` enforcement toggle. ADMIN-gated.

    Writes the durable posture the dashboard Security page controls.
    :func:`hal0.api.auth.require_auth_enabled` reads this on the next
    request (its cache keys on the file mtime, which the atomic write
    bumps), so the change **applies live** — no restart, no reload call.

    Lockout guard: enabling enforcement with no ``HAL0_ADMIN_KEY``
    configured would lock EVERYONE out (``/login`` rejects every attempt
    when no admin key exists), so we refuse it with a clear 400. The
    caller (and UI) must configure a key first. Disabling is always
    allowed.

    ADMIN classification matters only once auth is already ON: while it's
    OFF the middleware doesn't enforce, so the first enable succeeds
    unauthenticated (the intended "operator turns it on" path); after that
    the operator logs in and any later toggle rides their admin cookie.
    """
    if body.require_auth and not has_admin_key():
        raise BadRequest(
            "cannot enable auth with no admin key configured — set HAL0_ADMIN_KEY "
            "first, or you will lock yourself out",
            code="auth.no_admin_key",
        )

    cfg = load_hal0_config()
    cfg.security.require_auth = body.require_auth
    save_hal0_config(cfg)
    # Keep the in-process settings view (app.state.hal0_config) coherent with
    # what other settings surfaces read, matching routes/settings.py.
    request.app.state.hal0_config = cfg
    log.info("hal0.auth.require_toggled", require_auth=body.require_auth)
    return {"require_auth": body.require_auth, "applies_live": True}


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
