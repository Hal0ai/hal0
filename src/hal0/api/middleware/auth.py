"""Bearer-token + cookie-session authentication dependency.

This module exposes three FastAPI dependencies:

  - :func:`require_token` — gates a single route or router. Accepts any
    valid scope (used as the *reader* gate on admin routers).
  - :func:`require_writer` — gates a single mutating route. Requires an
    ``admin``- or ``all``-scoped credential; ``read-only`` and
    ``v1-only`` are rejected with 403 ``auth.forbidden``.
  - :func:`require_admin` — strictest gate, only ``admin`` scope. Used
    on the token-CRUD subrouter.

Scope × verb matrix (admin routers: ``/api/slots``, ``/api/models``,
``/api/settings``, ``/api/hardware``, ``/api/logs``, ``/api/providers``,
``/api/updates``, ``/api/images``)::

    scope       | GET (reader)       | POST/PUT/PATCH/DELETE (writer)
    ------------+--------------------+-------------------------------
    admin       | 200                | 200
    all         | 200                | 200
    read-only   | 200                | 403 auth.forbidden
    v1-only     | 200                | 403 auth.forbidden
    (no creds)  | 401 auth.required  | 401 auth.required
    (bad creds) | 401 auth.invalid   | 401 auth.invalid

When ``HAL0_AUTH_ENABLED`` is unset, every dependency is a pass-through
that returns ``identity="anonymous", scope="all"`` — so the gate
collapses to "open" on the trusted-LAN install posture.

Public routes (no allowlist)
----------------------------

ADR-0001 (Child B) deleted the ``PUBLIC_PATHS`` frozenset. A route is
public iff its router / handler does NOT declare an auth dependency —
that's the entire mechanism. The wizard endpoints
(``/api/install/state``, ``/api/install/probe``, ``/api/install/complete``,
``/api/install/curated-models``, ``/api/install/pick-default``), the
auth surface (``/api/auth/status``, ``/api/auth/login``,
``/api/auth/logout``, ``/api/auth/password``, ``/api/auth/me`` —
``me`` IS auth-gated, the rest are public), liveness / metrics
(``/api/status``, ``/api/health/system``, ``/api/metrics``,
``/api/features``), config discovery (``/api/config/urls``), and the
OpenAI pre-auth model probe (``GET /v1/models``) all live on bare /
auth-free routers. Everything else inherits an auth dep at
``include_router(...)`` time. See ``hal0.api.create_app`` for the wiring
table.

Auth precedence
---------------

1. ``Authorization: Bearer <token>`` present → validate against the token
   store. Failure ⇒ 401 (``auth.invalid``). Success ⇒ identity = token's
   label, scope = token's scope.

2. ``hal0_session`` cookie present (ADR-0001 Child A) → validate signed
   JWT claims. Failure ⇒ 401 (``auth.invalid``).

3. ``X-Forwarded-Email`` present (a trusted upstream proxy validated the
   identity and forwarded it) → trust it. Scope = ``"admin"``. This path
   stays around for users fronting hal0 with their own SSO proxy
   (Authelia, Authentik, Cloudflare Access, Pangolin etc.) — hal0's own
   Caddy no longer sets it.

4. Else → 401 (``auth.required``).

When ``HAL0_AUTH_ENABLED`` is unset / falsy, ``require_token`` is a
no-op pass-through that returns the literal identity ``"anonymous"`` —
preserving full backward compatibility with the pre-auth installs that
449 of the existing tests exercise.

Notes on the identity contract
------------------------------

The dependency returns an :class:`AuthIdentity` dataclass so callers that
*do* want to branch on scope (the admin-only token CRUD routes) can do
``Depends(require_admin)`` cleanly without re-parsing the auth headers.
Most routes can ignore the return value — the side effect of refusing
the request when auth fails is the whole point.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from hal0.api.auth.password import verify_session_token
from hal0.auth.tokens import (
    Token,
    TokenStore,
    auth_enabled,
    get_or_create_store,
)
from hal0.errors import Hal0Error

# Header names — lower-cased because Starlette normalises them on read.
_BEARER_HEADER = "authorization"
_BEARER_PREFIX = "Bearer "
_FORWARDED_EMAIL_HEADER = "x-forwarded-email"

# Session-cookie surface added by ADR-0001 Child A. The cookie name is
# part of the API contract — the Set-Cookie issued by /api/auth/login,
# the cookie read here, and the Set-Cookie expiration issued by
# /api/auth/logout must all agree on this constant.
SESSION_COOKIE_NAME: str = "hal0_session"

# CSRF tripwire headers. The cookie path requires *either* of these to
# pass on a writer-scoped route:
#
#   - ``X-Requested-With: XMLHttpRequest`` — a browser cannot set this
#     on a cross-origin form post without a preflight, so seeing it is
#     proof the request originated from same-origin JS.
#   - ``X-CSRF-Token`` matching the session token's first 16 chars —
#     the same-origin JS reads the cookie out of band and echoes a
#     bound prefix; a cross-site attacker cannot read the cookie value,
#     so they cannot fabricate the prefix.
#
# Bearer auth bypasses the check entirely because Bearer headers cannot
# be sent cross-origin from a browser without explicit fetch opt-in,
# which already requires the attacker to have CORS permission.
_CSRF_REQUESTED_WITH_HEADER = "x-requested-with"
_CSRF_REQUESTED_WITH_VALUE = "XMLHttpRequest"
_CSRF_TOKEN_HEADER = "x-csrf-token"
_CSRF_TOKEN_BINDING_LEN = 16

# Identity we return when auth is disabled. The literal string is used by
# log breadcrumbs ("authed_as=anonymous") so don't change it casually.
_ANONYMOUS_IDENTITY = "anonymous"
_ANONYMOUS_SCOPE = "all"

# Identity scope returned for an X-Forwarded-Email-only auth path. Caddy
# basic_auth users are dashboard owners, so they get admin.
_FORWARDED_SCOPE = "admin"

# Scopes permitted to mutate admin resources. "admin" can do anything
# (including token CRUD); "all" is the default minted scope for general
# clients and is treated as a writer for parity with pre-scope behaviour.
# "read-only" and "v1-only" are explicitly excluded — they get 403 on
# any mutating route.
_WRITER_SCOPES: frozenset[str] = frozenset({"admin", "all"})


# ── Typed errors ──────────────────────────────────────────────────────────────


class AuthRequired(Hal0Error):
    """No credentials presented for a protected route."""

    code = "auth.required"
    status = 401


class AuthInvalid(Hal0Error):
    """Credentials presented but didn't validate."""

    code = "auth.invalid"
    status = 401


class AuthForbidden(Hal0Error):
    """Authenticated but lacks the required scope."""

    code = "auth.forbidden"
    status = 403


class CSRFRequired(Hal0Error):
    """Cookie-authed writer route called without a CSRF tripwire.

    Raised when the request authenticates via the session cookie *and*
    targets a writer-scoped route, but does not carry either
    ``X-Requested-With: XMLHttpRequest`` or a matching ``X-CSRF-Token``.
    Bearer auth bypasses the check, so a 403 here means a browser
    cookie was used without the SPA's defensive headers — almost
    certainly a CSRF probe rather than a legitimate client.
    """

    code = "auth.csrf_required"
    status = 403


# ── Identity dataclass ──────────────────────────────────────────────────────


@dataclass
class AuthIdentity:
    """The caller's authenticated identity.

    ``identity`` is human-meaningful (token label or forwarded email or
    owner username). ``scope`` is the policy bucket — currently
    ``"admin" | "all" | "v1-only" | "read-only"``. ``source`` is one of:

      - ``"token"``       — Bearer auth via tokens.toml
      - ``"session"``     — hal0_session cookie (ADR-0001 Child A)
      - ``"forwarded"``   — X-Forwarded-Email from Caddy basic_auth
      - ``"anonymous"``   — auth disabled at the env-var level

    ``token`` is the underlying :class:`Token` row when source is
    ``"token"``, else None — useful for routes that need to log the
    token id or scope-check beyond the simple admin bit.

    ``session_token`` carries the raw session-cookie value when source
    is ``"session"`` and is None otherwise. The CSRF check in
    :func:`require_writer` reads it to compute the bound 16-char prefix
    that an X-CSRF-Token header must echo. Carrying it on the dataclass
    keeps the CSRF check colocated with the auth dependency without
    re-parsing cookies in the writer gate.
    """

    identity: str
    scope: str
    source: str
    token: Token | None = None
    session_token: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.scope == "admin"


# ── Dependencies ─────────────────────────────────────────────────────────────


def _resolve_bearer(request: Request) -> str | None:
    """Extract the raw token from ``Authorization: Bearer ...``.

    Returns None when the header is absent or malformed; callers fall
    through to the X-Forwarded-Email branch.
    """
    raw = request.headers.get(_BEARER_HEADER)
    if not raw:
        return None
    if not raw.startswith(_BEARER_PREFIX):
        return None
    candidate = raw[len(_BEARER_PREFIX) :].strip()
    return candidate or None


def _resolve_session_cookie(request: Request) -> str | None:
    """Extract the raw ``hal0_session`` cookie value, or None.

    Starlette already URL-decodes cookie values for us; the empty-string
    case (cookie present but blank) collapses to None so the caller
    falls through to the X-Forwarded-Email path instead of attempting
    to verify a nonexistent token.
    """
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw:
        return None
    return raw.strip() or None


def _resolve_forwarded_email(request: Request) -> str | None:
    """Extract the trusted email from the Caddy-forwarded header.

    The header is only trusted because Caddy strips inbound copies before
    forwarding (see Caddyfile template). On a misconfigured proxy this
    would let a client spoof identity — the deployment doc spells this
    out and the installer hard-fails if Caddy isn't fronting the API.
    """
    raw = request.headers.get(_FORWARDED_EMAIL_HEADER)
    if not raw:
        return None
    return raw.strip() or None


async def require_token(request: Request) -> AuthIdentity:
    """FastAPI dependency: gate the route on a valid identity.

    Precedence (first match wins):

      1. ``Authorization: Bearer <token>`` — programmatic clients. A
         malformed-but-present Bearer header still falls through to the
         next path; only a *parseable* Bearer that fails verification
         hard-fails with 401 ``auth.invalid``.
      2. ``hal0_session`` cookie — browser session (ADR-0001 Child A).
         A present-but-invalid cookie hard-fails with 401
         ``auth.invalid`` so an expired session prompts a clean
         re-login rather than silently downgrading.
      3. ``X-Forwarded-Email`` — Caddy-fronted basic_auth (the
         pre-Child-B path). Trusted only when no Bearer/cookie was
         presented.

    Bearer takes the first slot so existing programmatic clients (the
    OpenWebUI bridge, the haloai compat tests) behave identically to
    pre-ADR-0001 deployments. The session cookie comes second so a
    browser that has both a stale Bearer (e.g. from an OpenAPI doc
    page) and a fresh cookie sees the Bearer-flavoured error path
    rather than silently masking it.

    Returns the resolved :class:`AuthIdentity` so admin-only routes can
    assert on ``identity.scope`` via :func:`require_admin`.
    """
    if not auth_enabled():
        return AuthIdentity(
            identity=_ANONYMOUS_IDENTITY,
            scope=_ANONYMOUS_SCOPE,
            source="anonymous",
            token=None,
        )

    bearer = _resolve_bearer(request)
    if bearer is not None:
        store: TokenStore = get_or_create_store(request.app.state)
        match = store.verify(bearer)
        if match is None:
            raise AuthInvalid(
                "bearer token did not validate",
                details={"reason": "unknown_or_malformed_token"},
            )
        return AuthIdentity(
            identity=match.label,
            scope=match.scope,
            source="token",
            token=match,
        )

    cookie = _resolve_session_cookie(request)
    if cookie is not None:
        claims = verify_session_token(cookie)
        if claims is None:
            raise AuthInvalid(
                "session cookie did not validate",
                details={"reason": "expired_or_malformed_session"},
            )
        return AuthIdentity(
            identity=str(claims.get("sub") or "owner"),
            scope=str(claims.get("scope") or "admin"),
            source="session",
            token=None,
            session_token=cookie,
        )

    forwarded = _resolve_forwarded_email(request)
    if forwarded is not None:
        return AuthIdentity(
            identity=forwarded,
            scope=_FORWARDED_SCOPE,
            source="forwarded",
            token=None,
        )

    raise AuthRequired(
        "this endpoint requires authentication",
        details={
            "hint": (
                "send Authorization: Bearer <token> for programmatic clients, "
                "or POST /api/auth/login to obtain a hal0_session cookie"
            )
        },
    )


async def require_admin(
    request: Request,
    identity: Annotated[AuthIdentity, Depends(require_token)],
) -> AuthIdentity:
    """Like :func:`require_token` but additionally requires admin scope.

    Admin operations (token CRUD today; future per-user CRUD tomorrow)
    are strictly writer-scoped, so the CSRF tripwire applies here too
    when authentication arrives via cookie. Bearer auth bypasses,
    matching :func:`require_writer`.
    """
    if not auth_enabled():
        # When auth is off, treat everyone as admin — this preserves the
        # fully-trusted-LAN install posture. Flipping HAL0_AUTH_ENABLED=1
        # is what locks down the admin surface.
        return identity
    if not identity.is_admin:
        raise AuthForbidden(
            "this endpoint requires an admin-scoped credential",
            details={"identity": identity.identity, "scope": identity.scope},
        )
    _check_session_csrf(request, identity)
    return identity


def _check_session_csrf(request: Request, identity: AuthIdentity) -> None:
    """Raise CSRFRequired when a cookie-authed writer call lacks the tripwire.

    Bearer / forwarded / anonymous sources skip this check — only the
    cookie path goes through the CSRF gate, because only the cookie
    path can be re-played by a cross-origin form post that the browser
    attaches credentials to without the SPA's cooperation.

    Read methods (GET / HEAD / OPTIONS) skip the check entirely. Per
    RFC 7231 §4.2.1 those are "safe": a cross-origin tag-based fetch
    that hits one of them cannot mutate server state, so the CSRF
    surface doesn't exist. Limiting the check to mutating verbs lets
    ``require_admin`` reuse this helper without breaking the
    cookie-authed wizard's read calls.

    Accepted tripwires (either suffices):

      - ``X-Requested-With: XMLHttpRequest`` header.
      - ``X-CSRF-Token`` header matching the first 16 chars of the
        session cookie value. The binding is to the *cookie string*,
        not the decoded JWT claim — that way the client can grab it
        straight off ``document.cookie`` without re-signing anything.
    """
    if identity.source != "session":
        return
    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return
    headers = request.headers
    if headers.get(_CSRF_REQUESTED_WITH_HEADER, "") == _CSRF_REQUESTED_WITH_VALUE:
        return
    csrf = headers.get(_CSRF_TOKEN_HEADER, "")
    session_token = identity.session_token or ""
    expected = session_token[:_CSRF_TOKEN_BINDING_LEN]
    if csrf and expected and csrf == expected:
        return
    raise CSRFRequired(
        "writer routes require a CSRF tripwire when authenticated via cookie",
        details={
            "required_one_of": [
                f"{_CSRF_REQUESTED_WITH_HEADER}: {_CSRF_REQUESTED_WITH_VALUE}",
                f"{_CSRF_TOKEN_HEADER}: <first 16 chars of {SESSION_COOKIE_NAME} cookie>",
            ],
        },
    )


async def require_writer(
    request: Request,
    identity: Annotated[AuthIdentity, Depends(require_token)],
) -> AuthIdentity:
    """Gate a mutating route — requires ``admin`` or ``all`` scope.

    Attach this to every POST/PUT/PATCH/DELETE handler on the admin
    routers (slots, models, settings, hardware, providers, updates,
    images). GETs stay on plain :func:`require_token` so a
    ``read-only``-scoped token can still observe the system.

    When ``HAL0_AUTH_ENABLED`` is unset, this is a pass-through (same as
    :func:`require_token` and :func:`require_admin`).

    Cookie-authed callers must additionally satisfy the CSRF tripwire
    (see :func:`_check_session_csrf`). Bearer-authed callers bypass
    that check because a Bearer header can't be forged from a CSRF
    context without the SPA's explicit fetch opt-in.
    """
    if not auth_enabled():
        return identity
    if identity.scope not in _WRITER_SCOPES:
        raise AuthForbidden(
            "this endpoint requires a writer-scoped credential (admin or all)",
            details={
                "identity": identity.identity,
                "scope": identity.scope,
                "required": sorted(_WRITER_SCOPES),
            },
        )
    _check_session_csrf(request, identity)
    return identity


__all__ = [
    "SESSION_COOKIE_NAME",
    "AuthForbidden",
    "AuthIdentity",
    "AuthInvalid",
    "AuthRequired",
    "CSRFRequired",
    "require_admin",
    "require_token",
    "require_writer",
]
