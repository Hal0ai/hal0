"""Authentication endpoints (mounted under /api/auth).

  GET    /api/auth/status       — public; reports whether auth is
                                  enabled, whether a password is set,
                                  and the active auth mode.
  GET    /api/auth/me           — protected; returns the caller's
                                  resolved identity (label/email/owner
                                  + scope + source).
  POST   /api/auth/login        — public; validates {username, password}
                                  against the stored owner password and
                                  sets a ``hal0_session`` cookie on
                                  success. 401 on bad creds.
  GET    /api/auth/login        — legacy no-op kept so existing wizard
                                  probes don't 404 during the Wave 1
                                  rollout. Returns a hint pointing at
                                  the POST endpoint.
  POST   /api/auth/logout       — clears the session cookie; 204.
  POST   /api/auth/password     — set or rotate the owner password.
                                  Allowed *without* auth iff no password
                                  is currently set (first-run claim).
                                  Otherwise requires writer scope.
  GET    /api/auth/tokens       — admin-only; list token metadata.
  POST   /api/auth/tokens       — admin-only; mint a new token. The raw
                                  token value is in the response body
                                  exactly once and never re-exposed.
  DELETE /api/auth/tokens/{id}  — admin-only; revoke a token.

The router is wired in hal0.api.create_app() under prefix ``/api/auth``.
``Depends(require_admin)`` is attached to the token CRUD subrouter so we
don't have to remember to add it per route — adding new admin endpoints
just means appending to that subrouter.

ADR-0001 Child A (refs #54, #55) added the password + session surface.
Child B (Caddy reduction) and Child C (docs) are tracked separately and
must NOT be touched by edits to this file.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from hal0.api.auth.password import (
    create_session_token,
    hash_password,
    verify_password,
)
from hal0.api.middleware.auth import (
    SESSION_COOKIE_NAME,
    AuthIdentity,
    AuthInvalid,
    AuthRequired,
    require_admin,
    require_token,
    require_writer,
)
from hal0.auth.tokens import (
    DuplicateLabel,
    InvalidScope,
    TokenStore,
    auth_enabled,
    get_or_create_store,
)
from hal0.errors import BadRequest, Hal0Error

router = APIRouter()


# ── Constants / helpers ──────────────────────────────────────────────────────

# v1 is single-owner; the wizard hard-codes this username so the login
# form is a one-field "what's your password" prompt. When we grow to
# multi-user (post-v1), this constant disappears in favour of a
# per-user record on the store.
_OWNER_USERNAME: str = "owner"

# Owner password grants writer scope on every protected route. We use
# ``"admin"`` so the same cookie passes both ``require_writer`` and
# ``require_admin`` — there is only one human credential in v1, and the
# wizard would be unusable if logging in didn't unlock the Settings
# panel's token CRUD subrouter.
_OWNER_SCOPE: str = "admin"

# Minimum password length. 8 is the absolute floor; the wizard surfaces
# a stronger recommendation. Enforced server-side so a CLI/API caller
# can't bypass the UI hint.
_MIN_PASSWORD_LEN: int = 8


class PasswordTooShort(Hal0Error):
    """Set-password rejected for failing the minimum-length policy."""

    code = "auth.password_too_short"
    status = 400


def _store(request: Request) -> TokenStore:
    return get_or_create_store(request.app.state)


def _request_is_tls(request: Request) -> bool:
    """Return True if the request was served over TLS.

    We trust ``X-Forwarded-Proto`` because in production the only
    process that talks to the FastAPI app is Caddy (and in Child B's
    --no-tls mode, Traefik or similar). When the header is absent,
    fall back to the URL scheme Starlette resolved — that catches the
    direct-uvicorn dev case where http://localhost:8080 is the right
    answer (no Secure flag, cookie still works locally).
    """
    forwarded = request.headers.get("x-forwarded-proto", "").strip().lower()
    if forwarded:
        # Header can be a comma-separated chain (proxy in front of
        # proxy). The closest hop wins because it's the only one we've
        # configured to be honest.
        return forwarded.split(",", 1)[0].strip() == "https"
    return request.url.scheme == "https"


async def _read_json_object(request: Request) -> dict[str, Any]:
    """Parse the request body as a JSON object or raise BadRequest."""
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
    return body


# ── Public ───────────────────────────────────────────────────────────────────


@router.get("/status")
async def auth_status(request: Request) -> dict[str, Any]:
    """Report the auth mode without leaking any token data.

    Read by the Settings UI to render the "Authentication" panel header
    and by the wizard to decide whether to surface the "set password"
    step. ADR-0001 Child A added ``password_set`` and ``auth_mode`` so
    the wizard can tell first-run open mode from a password-protected
    install without a second round-trip.
    """
    store = _store(request)
    password_set = store.get_password_hash() is not None
    return {
        "enabled": auth_enabled(),
        "modes": ["bearer", "session", "forwarded-email"],
        "managed_via_installer": True,
        # ADR-0001 Child A additions:
        "password_set": password_set,
        # ``open`` when no password is set (first-run posture).
        # ``password`` once a password exists, regardless of whether the
        # caller currently holds a session cookie — the field reports
        # the install posture, not the per-request auth state.
        "auth_mode": "password" if password_set else "open",
    }


@router.get("/login")
async def login_hint() -> dict[str, Any]:
    """Legacy GET-/login compatibility shim.

    The real login flow is ``POST /api/auth/login`` (ADR-0001 Child A).
    This endpoint stays public so older wizard probes that GET the path
    to test reachability don't 404 mid-rollout. It returns a hint
    pointing at the POST endpoint and never sets a cookie.
    """
    return {
        "ok": True,
        "message": "POST {username, password} to this URL to obtain a session cookie.",
    }


@router.post("/login")
async def login(request: Request, response: Response) -> dict[str, Any]:
    """Validate owner credentials and set the ``hal0_session`` cookie.

    Body::

        {"username": "owner", "password": "..."}

    On success, sets a HttpOnly cookie carrying a signed JWT (HS256)
    with the owner's identity + scope. The cookie is Secure-when-TLS;
    detection is via ``X-Forwarded-Proto: https`` (Caddy-fronted) or a
    ``https`` request scheme (direct).

    On failure, returns 401 ``auth.invalid``. We deliberately use the
    same error for "no password configured", "wrong username", and
    "wrong password" so a probing client cannot enumerate which leg
    failed.
    """
    body = await _read_json_object(request)
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")

    store = _store(request)
    hash_str = store.get_password_hash()

    # Constant-ish failure path: every failure mode below collapses to
    # the same 401 envelope, so a probing client can't distinguish
    # "no password set" from "wrong password" from "wrong username".
    if not hash_str or username != _OWNER_USERNAME or not verify_password(password, hash_str):
        raise AuthInvalid(
            "username or password is incorrect",
            details={"reason": "bad_credentials"},
        )

    token = create_session_token(user=_OWNER_USERNAME, scope=_OWNER_SCOPE)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=_request_is_tls(request),
        # Path=/ so the cookie rides every API call from the dashboard,
        # not just /api/auth/*. The cookie is the SPA's primary auth
        # channel once logged in.
        path="/",
    )
    return {
        "user": _OWNER_USERNAME,
        "scope": _OWNER_SCOPE,
    }


@router.post("/logout")
async def logout() -> Response:
    """Clear the session cookie (204 No Content).

    Logout is always a 204 — there's no body to return, and the only
    side effect is "the client's next request will fall through the
    cookie path with nothing to verify". We never invalidate the JWT
    server-side because that would require a session table the rest of
    v1 doesn't have; the cookie deletion is what the client trusts.

    We construct a Response explicitly (rather than letting FastAPI
    serialize a return value) so the 204 status code is set on the
    actual outgoing response, not just on the response model FastAPI
    builds — that compose was emitting an empty status line for the
    /logout endpoint under TestClient.
    """
    response = Response(status_code=204)
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
    )
    return response


@router.post("/password")
async def set_password(request: Request) -> dict[str, Any]:
    """Set or rotate the owner password.

    Body::

        {"password": "..."}

    Auth contract:
      - When no password is yet set, the endpoint is callable
        **without any credentials** — that's the first-run "claim
        ownership" path the wizard drives.
      - Once a password is set, the endpoint requires writer scope
        (Bearer or cookie). The cookie path additionally goes through
        the CSRF tripwire because it's a writer-scoped mutation.

    Always 400s a password shorter than 8 chars (server-side floor;
    the wizard surfaces a stronger UX hint).
    """
    body = await _read_json_object(request)
    new_password = str(body.get("password") or "")
    if len(new_password) < _MIN_PASSWORD_LEN:
        raise PasswordTooShort(
            f"password must be at least {_MIN_PASSWORD_LEN} characters",
            details={"minimum": _MIN_PASSWORD_LEN, "received_length": len(new_password)},
        )

    store = _store(request)
    has_existing = store.get_password_hash() is not None

    if has_existing:
        # Rotation requires a writer-scoped credential. Manually invoke
        # the dependency chain so the first-run path can stay public on
        # the same router (FastAPI dependencies declared on the route
        # decorator can't be conditionally bypassed). We re-run
        # require_token + require_writer here for clarity — the cost is
        # one extra function call per rotation, which dominates
        # nothing.
        identity: AuthIdentity = await require_token(request)
        # require_writer also enforces CSRF on cookie auth — reuse it
        # rather than re-implementing the scope+CSRF logic.
        await require_writer(request, identity)

    new_hash = hash_password(new_password)
    store.set_password_hash(new_hash)
    return {
        "ok": True,
        "password_set": True,
        # ``rotated`` distinguishes first-run set from a subsequent
        # rotation. The wizard surfaces a different toast based on it.
        "rotated": has_existing,
    }


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
    body = await _read_json_object(request)

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
# (/status, /login, /logout, /me, /password) live alongside without
# inheriting the admin gate.
router.include_router(tokens_router, prefix="/tokens")


# Re-export AuthRequired so static checkers don't flag the "imported
# but unused" path — we keep the import for symmetry with AuthInvalid
# in case a future endpoint needs it.
__all__ = ["AuthRequired", "router"]
