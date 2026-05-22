"""Bearer-token + cookie-session authentication dependency.

This module exposes three FastAPI dependencies:

  - :func:`require_token` — gates a single route or router. Accepts any
    valid scope (used as the *reader* gate on admin routers).
  - :func:`require_writer` — gates a single mutating route. Requires an
    ``admin``- or ``all``-scoped credential; ``read-only`` and
    ``v1-only`` are rejected with 403 ``auth.forbidden``.
  - :func:`require_admin` — strictest gate, only ``admin`` scope. Used
    on the token-CRUD subrouter.

Scope x verb matrix (admin routers: ``/api/slots``, ``/api/models``,
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

Auth is **on by default** as of v1.0 (security review §36, 2026-05-21).
Set ``HAL0_AUTH_DISABLED=1`` to opt back into the pre-v1 trusted-LAN
pass-through (every dependency returns
``identity="anonymous", scope="all"`` and routes are open). The legacy
``HAL0_AUTH_ENABLED`` env var is honoured for compatibility — see
``hal0.auth.tokens.auth_enabled()`` for the full precedence table.

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

3. ``X-Forwarded-Email`` present AND ``HAL0_TRUST_FORWARDED_EMAIL=1``
   in the environment → trust it. Scope = ``"admin"``. The opt-in
   env-var is mandatory because hal0's own Caddy (post-ADR-0001
   Child B) no longer sets or strips this header — trusting it on a
   default install would let any LAN peer spoof admin. Operators
   fronting hal0 with their own SSO proxy (Authelia, Authentik,
   Cloudflare Access, Pangolin) flip the env var ON once they've
   configured their proxy to set + strip the header.

4. Else → 401 (``auth.required``).

When ``HAL0_AUTH_DISABLED=1`` (or the legacy ``HAL0_AUTH_ENABLED=0``),
``require_token`` is a no-op pass-through that returns the literal
identity ``"anonymous"`` — preserving the pre-v1 trusted-LAN posture
for operators who deliberately opted back into it.

Notes on the identity contract
------------------------------

The dependency returns an :class:`AuthIdentity` dataclass so callers that
*do* want to branch on scope (the admin-only token CRUD routes) can do
``Depends(require_admin)`` cleanly without re-parsing the auth headers.
Most routes can ignore the return value — the side effect of refusing
the request when auth fails is the whole point.
"""

from __future__ import annotations

import hmac
import os
import re
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
from hal0.config import paths
from hal0.errors import Hal0Error

# Header names — lower-cased because Starlette normalises them on read.
_BEARER_HEADER = "authorization"
_BEARER_PREFIX = "Bearer "
_FORWARDED_EMAIL_HEADER = "x-forwarded-email"

# X-Forwarded-Email opt-in env-var. ADR-0001 Child B removed Caddy's
# basic_auth, so hal0's own bundled Caddy no longer SETS this header —
# and the bundled Caddyfile.template does NOT strip inbound copies.
# Trusting the header by default would let any LAN peer authenticate as
# admin by sending ``X-Forwarded-Email: anyone@example.com``. Operators
# fronting hal0 with their own SSO proxy (Authelia, Authentik,
# Cloudflare Access, Pangolin, etc.) opt back in by setting this env
# var AND configuring their proxy to set + strip the header — see the
# deployment docs.
_FORWARDED_EMAIL_TRUST_ENV = "HAL0_TRUST_FORWARDED_EMAIL"

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


# ── First-run claim ──────────────────────────────────────────────────
# When auth is on AND no owner password is yet set AND the installer's
# ``.first-run.lock`` file is present on disk, a small set of paths
# stay reachable to anonymous callers so the wizard can claim
# ownership. The lockfile is mode 0600 and carries a one-time OTP that
# §28's follow-up consumes via Bearer-header presentation; this fix
# (§36) plants the file so the API can rely on it being there.
#
# Today every route in this list is mounted WITHOUT an auth dependency
# (the wizard endpoints live on a bare router; ``/api/auth/password``
# has its own gate-by-presence logic). The pass-through here is the
# coordination point for §28 / §29's follow-up PRs, which attach
# ``Depends(require_writer)`` to those routes: when they do, this
# helper keeps the first-run claim reachable without rewriting the
# router wiring.
#
# The wizard also calls a handful of writer-gated routes BEFORE the
# user finishes the password step (or chooses to skip it): persisting
# the storage dir picks, pulling capability models, and registering
# each pulled capability with the orchestrator. Those land below as
# prefix/regex matches because they carry path variables (model id,
# capability slot/child). The pre-password window is bounded by the
# lockfile — once /api/auth/password or /api/install/complete consumes
# it, this helper returns False for everything and the routes revert
# to their declared writer gate.
_FIRST_RUN_CLAIM_PATHS: frozenset[str] = frozenset(
    {
        "/api/install/state",
        "/api/install/probe",
        "/api/install/complete",
        "/api/install/curated-models",
        "/api/install/pick-default",
        "/api/auth/password",
        # Wizard step 2 — operator picks the model storage directories.
        # Gated by require_writer on the live router; admitted here so
        # the wizard can persist before a credential exists.
        "/api/config/models",
    }
)

# Prefix matches for routes whose path carries variables. Each pattern
# is anchored at the start of the path and used with ``re.fullmatch``,
# so a stray suffix does NOT slip through (e.g. /api/models/foo/pull
# matches but /api/models/foo/pull/extra does not).
_FIRST_RUN_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    # POST /api/models/{model_id}/pull — capability pulls launched from
    # the wizard's install step. The model id segment is anything that
    # isn't a slash; we deliberately keep it permissive because the
    # registry's own validation already gates which ids actually exist.
    re.compile(r"/api/models/[^/]+/pull"),
    # POST /api/capabilities/{slot}/{child} — orchestrator registration
    # the wizard fires after each capability pull lands. Slots + child
    # names are short identifiers (embed/voice/img/...); permissive
    # regex here is fine because the orchestrator rejects unknown
    # slot/child combos with its own typed error.
    re.compile(r"/api/capabilities/[^/]+/[^/]+"),
)


def _password_is_set(request: Request) -> bool:
    """True iff the owner password is set on the active token store.

    Resolves the store via the same ``get_or_create_store`` path the
    main auth dependency uses, so a test that swaps the store on
    ``app.state`` exercises the same code path as production.
    Failures (missing app state, store I/O error) collapse to False —
    we'd rather treat "I don't know" as "first-run still possible" so
    the wizard remains reachable than 500 the auth middleware.
    """
    try:
        store: TokenStore = get_or_create_store(request.app.state)
        return store.get_password_hash() is not None
    except Exception:
        return False


def _path_is_claim_eligible(path: str) -> bool:
    """True iff ``path`` is one of the routes the wizard reaches before
    the operator has a credential.

    Two layers:
      - :data:`_FIRST_RUN_CLAIM_PATHS` for the fixed routes (cheap set
        membership).
      - :data:`_FIRST_RUN_CLAIM_PATTERNS` for the routes that carry a
        variable segment (model id, capability slot/child) and need a
        regex full-match.

    Kept as a tiny pure helper so unit tests can exercise the
    path-matching logic without standing up the full request pipeline.
    """
    if path in _FIRST_RUN_CLAIM_PATHS:
        return True
    return any(pattern.fullmatch(path) for pattern in _FIRST_RUN_CLAIM_PATTERNS)


def _first_run_claim_active(request: Request) -> bool:
    """True iff the first-run claim window is currently open.

    Three conditions must hold:
      1. The route being requested matches the claim-eligible set (see
         :func:`_path_is_claim_eligible`).
      2. The owner password is not yet set.
      3. The installer's ``.first-run.lock`` file exists on disk
         (proof a fresh install just happened on this host).

    When all three hold, ``require_token`` short-circuits to an
    anonymous identity so the wizard's set-password call can land.
    The lockfile is deleted by the wizard's success path (or by the
    uninstaller); once it's gone, the only public surface is whatever
    the routers declare via absent-auth-dep.
    """
    if not _path_is_claim_eligible(request.url.path):
        return False
    if _password_is_set(request):
        return False
    try:
        return paths.first_run_lock().exists()
    except OSError:
        # ``first_run_lock()`` builds a path purely from env / FHS — an
        # OSError here would be a filesystem-level fault on the stat,
        # treat as "no claim window" to fail closed.
        return False


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


def _forwarded_email_trusted() -> bool:
    """Return True iff the operator opted into trusting X-Forwarded-Email.

    Disabled by default after ADR-0001 Child B because the bundled Caddy
    no longer strips inbound copies of the header — trusting it on a
    default install lets any LAN peer authenticate as admin. Operators
    fronting hal0 with their own SSO proxy (Authelia, Authentik,
    Cloudflare Access, Pangolin) set ``HAL0_TRUST_FORWARDED_EMAIL=1``
    once they've verified their proxy sets + strips the header.
    """
    val = os.environ.get(_FORWARDED_EMAIL_TRUST_ENV, "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _resolve_forwarded_email(request: Request) -> str | None:
    """Extract the trusted email from the upstream SSO-proxy-forwarded header.

    Returns None when the operator has not opted into trusting the header
    via ``HAL0_TRUST_FORWARDED_EMAIL=1``. On a default install (where
    Caddy is a dumb TLS terminator + reverse_proxy and does NOT strip
    inbound copies) the header is attacker-controlled and must not gate
    auth. Operators with their own SSO proxy configured to set + strip
    the header opt back in via the env var.
    """
    if not _forwarded_email_trusted():
        return None
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

    # First-run claim window: an installer just dropped ``.first-run.lock``
    # and no password is set yet. Allow anonymous on the wizard claim
    # paths so the operator can POST /api/auth/password without already
    # holding a credential. Every other route still demands creds.
    if _first_run_claim_active(request):
        return AuthIdentity(
            identity=_ANONYMOUS_IDENTITY,
            scope=_ANONYMOUS_SCOPE,
            source="first-run-claim",
            token=None,
        )

    bearer = _resolve_bearer(request)
    if bearer is not None:
        store: TokenStore = get_or_create_store(request.app.state)
        match = store.verify(bearer)
        if match is None:
            raise AuthInvalid(
                "bearer token didn't match any active token in the store",
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
                "session cookie is expired or malformed",
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
        "this route needs a token or a logged-in session",
        details={
            "hint": (
                "send Authorization: Bearer <token> for programmatic clients, "
                "or POST /api/auth/login to get a hal0_session cookie"
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
            "this route is admin-only and your credential isn't admin-scoped",
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
    # Constant-time compare so the per-request timing on the X-CSRF-Token
    # path doesn't leak a byte-by-byte oracle for the bound prefix. The
    # length-prefilter guards against compare_digest's "compares OK on
    # empty string" edge case and keeps the short-circuit on the missing-
    # header branch.
    if csrf and expected and hmac.compare_digest(csrf, expected):
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
            "this route needs a writer-scoped credential (admin or all). "
            "read-only and v1-only tokens get a 403 here.",
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


# Re-export for §28/§29's follow-up PRs — they need to know whether the
# first-run claim window is currently active when deciding whether to
# short-circuit to ``127.0.0.1``-only mode on the install routes.
__all__.append("_first_run_claim_active")
