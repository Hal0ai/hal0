"""Authentication endpoints (mounted under /api/auth).

  GET    /api/auth/status       — public; reports whether auth is enabled
                                  and which mode (Caddy basic + Bearer).
  GET    /api/auth/me           — protected; returns the caller's
                                  resolved identity (label/email + scope).
  GET    /api/auth/login        — public no-op (Caddy owns browser login).
  POST   /api/auth/logout       — public no-op (clears any client hint;
                                  Caddy basic_auth is browser-driven).
  GET    /api/auth/tokens       — admin-only; list token metadata.
  POST   /api/auth/tokens       — admin-only; mint a new token. The raw
                                  token value is in the response body
                                  exactly once and never re-exposed.
  DELETE /api/auth/tokens/{id}  — admin-only; revoke a token.

The router is wired in hal0.api.create_app() under prefix ``/api/auth``.
``Depends(require_admin)`` is attached to the token CRUD subrouter so we
don't have to remember to add it per route — adding new admin endpoints
just means appending to that subrouter.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from hal0.api.middleware.auth import (
    AuthIdentity,
    require_admin,
    require_token,
)
from hal0.auth.tokens import (
    DuplicateLabel,
    InvalidScope,
    TokenStore,
    auth_enabled,
    get_or_create_store,
)
from hal0.errors import BadRequest

router = APIRouter()


# ── Public ───────────────────────────────────────────────────────────────────


@router.get("/status")
async def auth_status() -> dict[str, Any]:
    """Report the auth mode without leaking any token data.

    Read by the Settings UI to render the "Authentication" panel header.
    Public so the dashboard can render the panel before the user picks a
    credential.
    """
    return {
        "enabled": auth_enabled(),
        "modes": ["bearer", "forwarded-email"],
        # The dashboard's modal uses this to point users at the installer
        # flag rather than offer an in-app enable that would lock them out
        # without a corresponding Caddy config.
        "managed_via_installer": True,
    }


@router.get("/login")
async def login() -> dict[str, Any]:
    """No-op login endpoint.

    Browser auth is owned by Caddy basic_auth at the edge — there is no
    in-app login page. We expose this route so a client that POSTs to
    ``/api/auth/login`` (a haloai habit) gets a polite "use Caddy" hint
    rather than a 404.
    """
    return {
        "ok": True,
        "message": (
            "Browser login is handled by the Caddy reverse proxy "
            "(basic_auth at the edge). For programmatic clients, send "
            "Authorization: Bearer <token>."
        ),
    }


@router.post("/logout")
async def logout() -> dict[str, Any]:
    """No-op logout endpoint (parity with /login)."""
    return {"ok": True}


@router.get("/me")
async def me(
    identity: Annotated[AuthIdentity, Depends(require_token)],
) -> dict[str, Any]:
    """Return the caller's resolved identity.

    Useful for the dashboard's user widget and for clients that want to
    verify their token works without touching a heavier endpoint.
    """
    return {
        "identity": identity.identity,
        "scope": identity.scope,
        "source": identity.source,
    }


# ── Admin: token CRUD ─────────────────────────────────────────────────────────
# Wrapped in a subrouter so every endpoint inherits the admin gate without
# us re-declaring it per function. New token-management endpoints land here
# and are admin-protected by default.


tokens_router = APIRouter(dependencies=[Depends(require_admin)])


def _store(request: Request) -> TokenStore:
    return get_or_create_store(request.app.state)


@tokens_router.get("")
async def list_tokens(request: Request) -> dict[str, Any]:
    """List token metadata (no hashes, no secrets)."""
    store = _store(request)
    return {"tokens": [t.metadata() for t in store.list()]}


@tokens_router.post("")
async def create_token(request: Request) -> dict[str, Any]:
    """Mint a new token.

    Body shape::

        {"label": "openwebui-bridge", "scope": "all"}

    Response::

        {"id": "<uuid8>", "label": "...", "scope": "...", "token": "hal0_..."}

    The ``token`` field is the only chance to capture the secret — the
    UI shows it in a copy-to-clipboard box and warns the user it cannot
    be retrieved afterwards.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest(
            "request body must be valid JSON",
            details={"error": str(exc)},
            code="request.invalid_json",
        ) from exc
    if not isinstance(body, dict):
        raise BadRequest("request body must be a JSON object", code="request.not_an_object")

    label = str(body.get("label") or "").strip()
    scope = str(body.get("scope") or "all").strip() or "all"

    store = _store(request)
    try:
        tok, raw = store.create(label=label, scope=scope)
    except (DuplicateLabel, InvalidScope):
        raise

    return {
        "id": tok.id,
        "label": tok.label,
        "scope": tok.scope,
        "created_at": tok.created_at,
        "token": raw,
        # The UI surfaces this banner verbatim — keeping it server-side
        # means a future copy change doesn't require a frontend rebuild.
        "warning": (
            "This token is shown once and cannot be retrieved later. "
            "Copy it now and store it in your secret manager."
        ),
    }


@tokens_router.delete("/{token_id}")
async def revoke_token(token_id: str, request: Request) -> dict[str, Any]:
    """Revoke a token by id.

    A 404 on an unknown id is preferred over a silent no-op so callers
    can distinguish "already revoked" from "the wrong id was sent".
    """
    store = _store(request)
    store.revoke(token_id)
    return {"ok": True, "revoked": token_id}


# Mount the admin subrouter under /tokens so the public endpoints above
# (/status, /login, /logout, /me) live alongside without inheriting the
# admin gate.
router.include_router(tokens_router, prefix="/tokens")


__all__ = ["router"]
