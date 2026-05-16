"""Bearer-token + Caddy-edge authentication dependency.

This module exposes four FastAPI dependencies:

  - :func:`require_token` — gates a single route or router. Accepts any
    valid scope (used as the *reader* gate on admin routers).
  - :func:`require_token_unless_public` — gates a router globally but
    bypasses the auth check for paths in :data:`PUBLIC_PATHS`. This is
    what we attach at ``include_router(...)`` time on routers that mix
    public and protected endpoints (``/v1`` and ``/api/install``).
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

Public route allowlist
----------------------

The following stay public under ``HAL0_AUTH_ENABLED=1``:

  - ``GET  /api/health/system``        — liveness probe
  - ``GET  /api/status``               — dashboard liveness ping
  - ``GET  /api/metrics``              — JSON metrics / dashboard scrape
  - ``GET  /api/features``             — feature-flag inspection
  - ``GET  /api/install/state``        — first-run gating
  - ``POST /api/install/complete``     — first-run sentinel write
  - ``GET  /api/config/urls``          — host-aware URL hints
  - ``GET  /api/auth/status``          — auth-mode discovery
  - ``GET  /api/auth/login``           — placeholder (Caddy owns real login)
  - ``POST /api/auth/logout``          — clears local session hints
  - ``GET  /v1/models``                — OpenAI clients probe before auth

Everything else under ``/v1/*`` and the admin ``/api/*`` routes is
gated. Protected single-purpose routers (slots, models, providers,
hardware, logs, settings, updater) get the dep at router-include time;
mixed routers (``/v1``, ``/api/install``) get the path-aware variant
that consults :data:`PUBLIC_PATHS`.

Auth precedence
---------------

1. ``Authorization: Bearer <token>`` present → validate against the token
   store. Failure ⇒ 401 (``auth.invalid``). Success ⇒ identity = token's
   label, scope = token's scope.

2. ``X-Forwarded-Email`` present (Caddy verified basic_auth at the edge,
   then forwarded the identity) → trust it. Scope = ``"admin"`` because
   Caddy's basic_auth users are the dashboard owners.

3. Else → 401 (``auth.required``).

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


# Routes that bypass auth even when HAL0_AUTH_ENABLED=1. Match exact paths
# (case-sensitive) — all of these are well-known endpoints whose contracts
# pre-date the auth gate. Adding to this list is a security decision; do
# it deliberately.
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        # Liveness / observability — must be reachable for monitoring
        # tools that pre-date a per-deployment token.
        #
        # NOTE (issue #36): /api/metrics/prometheus used to live here but
        # no Prometheus exposition route ever shipped. Listing a 404 path
        # as "public" was confusing for scraper operators following the
        # documented bypass list. Add it back if/when a real exporter
        # ships — keep this list in lockstep with packaging/caddy's
        # @public matcher.
        "/api/health/system",
        "/api/status",
        "/api/metrics",
        "/api/features",
        # First-run wizard gating — the dashboard hits these before the
        # user has any chance to mint a token.
        "/api/install/state",
        "/api/install/complete",
        # Host-aware URL hints — used by the FirstRun wizard to render
        # the OpenWebUI link with the right host.
        "/api/config/urls",
        # Auth surface itself — discovery + Caddy-owned login parity.
        "/api/auth/status",
        "/api/auth/login",
        "/api/auth/logout",
        # OpenAI compat — many clients probe /v1/models before sending
        # an Authorization header. Models list isn't sensitive.
        "/v1/models",
    }
)


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


# ── Identity dataclass ──────────────────────────────────────────────────────


@dataclass
class AuthIdentity:
    """The caller's authenticated identity.

    ``identity`` is human-meaningful (token label or forwarded email).
    ``scope`` is the policy bucket — currently ``"admin" | "all" |
    "v1-only" | "read-only"``. ``source`` is one of:

      - ``"token"``       — Bearer auth via tokens.toml
      - ``"forwarded"``   — X-Forwarded-Email from Caddy basic_auth
      - ``"anonymous"``   — auth disabled at the env-var level

    ``token`` is the underlying :class:`Token` row when source is
    ``"token"``, else None — useful for routes that need to log the
    token id or scope-check beyond the simple admin bit.
    """

    identity: str
    scope: str
    source: str
    token: Token | None = None

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

    See module docstring for the precedence rules. Returns the resolved
    :class:`AuthIdentity` so admin-only routes can assert on
    ``identity.scope`` via :func:`require_admin`.
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
                "or access via the Caddy-fronted dashboard for browser sessions"
            )
        },
    )


async def require_token_unless_public(request: Request) -> AuthIdentity:
    """Path-aware dependency: bypass for :data:`PUBLIC_PATHS`, else gate.

    Used at ``include_router(...)`` time on routers that mix public and
    protected endpoints (``/v1``, ``/api/install``). For single-purpose
    routers where every endpoint is protected, attach plain
    :func:`require_token` instead — the path lookup is cheap but
    unnecessary noise.

    Path matching is exact on ``request.url.path``. Sub-path patterns
    (e.g. ``/v1/models/{id}``) need to match the resolved path, not the
    template — which means ``/v1/models/foo`` would NOT match
    ``"/v1/models"`` in the allowlist. That's intentional: the bare
    ``/v1/models`` listing is OpenAI's pre-auth probe, but
    ``/v1/models/{id}`` is a model-detail call that warrants the same
    gate as everything else under ``/v1``. When that turns out to be
    wrong in practice, add the model-detail path to PUBLIC_PATHS — don't
    invert the matcher.
    """
    if request.url.path in PUBLIC_PATHS:
        # Mirror the auth-disabled identity so callers that *do* depend
        # on the dataclass downstream (none today, but the door is open)
        # see a consistent shape.
        return AuthIdentity(
            identity=_ANONYMOUS_IDENTITY,
            scope=_ANONYMOUS_SCOPE,
            source="public",
            token=None,
        )
    return await require_token(request)


async def require_admin(
    identity: Annotated[AuthIdentity, Depends(require_token)],
) -> AuthIdentity:
    """Like :func:`require_token` but additionally requires admin scope."""
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
    return identity


async def require_writer(
    identity: Annotated[AuthIdentity, Depends(require_token)],
) -> AuthIdentity:
    """Gate a mutating route — requires ``admin`` or ``all`` scope.

    Attach this to every POST/PUT/PATCH/DELETE handler on the admin
    routers (slots, models, settings, hardware, providers, updates,
    images). GETs stay on plain :func:`require_token` so a
    ``read-only``-scoped token can still observe the system.

    When ``HAL0_AUTH_ENABLED`` is unset, this is a pass-through (same as
    :func:`require_token` and :func:`require_admin`).
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
    return identity


__all__ = [
    "PUBLIC_PATHS",
    "AuthForbidden",
    "AuthIdentity",
    "AuthInvalid",
    "AuthRequired",
    "require_admin",
    "require_token",
    "require_token_unless_public",
    "require_writer",
]
