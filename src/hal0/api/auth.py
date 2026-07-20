"""KB-1 / §1 authentication: principal resolution, posture, enforcement.

Three-tier, deny-by-default auth built around one classification table
(:mod:`hal0.security.exposure`, seam S9). This module owns:

- :class:`AuthPrincipal` — the resolved identity of a request (``anon`` /
  ``client`` / ``admin``).
- Key resolution: cookie -> ``Authorization: Bearer`` -> ``?api_key=``,
  in that priority order (browsers can't set headers on a WebSocket
  upgrade, hence the query-param fallback for WS/SSE).
- :func:`require_auth_enabled` — the rollout posture. Defaults to
  enforcing only when the bind host is non-loopback or a key has been
  configured, so the existing ~700-test TestClient suite (loopback,
  no keys) keeps running dev-open, unchanged.
- :class:`AuthEnforcementMiddleware` — the pure-ASGI gate wired into
  ``create_app()`` right after ``log_scrub.install(app)``. Handles both
  ``http`` (incl. SSE, which is just a long-lived GET) and ``websocket``
  scopes; a denied WebSocket is rejected pre-accept with close code 4403
  (matching :mod:`hal0.api.agents.chat_proxy`'s existing convention).

The browser session cookie is **reused, not reimplemented**: minting and
verification both delegate to :mod:`hal0.api.agents._auth` (the existing
HMAC-SHA256 cookie already protecting the agent chat-proxy WS routes), so
there is exactly one cookie scheme and one secret file in the whole app. A
valid session cookie resolves to the ``admin`` tier — it's the local
operator's browser dashboard session, per ``spec-kb1-auth.md``.

Deliberately NOT covered here (deferred, see the KB-1 delivery report):
- Per-endpoint ``?api_key=`` coverage for every WS/SSE route (step 4) --
  the resolver above already reads ``?api_key=`` generically for ANY
  request, so this already works; step 4 is about auditing each
  individual streaming endpoint's UX around it, not the resolution logic.
- The §22 Settings Security page (step 5).
"""

from __future__ import annotations

import hmac
import json
import os
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

import structlog

from hal0.api.agents._auth import SESSION_COOKIE_NAME, verify_session_cookie
from hal0.security.exposure import AuthClass, classify

log = structlog.get_logger(__name__)

Tier = Literal["anon", "client", "admin"]

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class AuthPrincipal:
    """The resolved identity of an in-flight request.

    ``source`` is diagnostic only (audit logging / debugging) -- never
    used for authorization decisions, only ``tier`` is.
    """

    tier: Tier
    source: str = "none"


ANON: AuthPrincipal = AuthPrincipal(tier="anon", source="none")


# ---------------------------------------------------------------------------
# Keys + posture


def _env_key(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _admin_key() -> str | None:
    return _env_key("HAL0_ADMIN_KEY")


def _client_key() -> str | None:
    return _env_key("HAL0_CLIENT_KEY")


def has_admin_key() -> bool:
    """True iff an operator has configured ``HAL0_ADMIN_KEY``.

    Used both by :func:`require_auth_enabled` (posture derivation) and by
    the enforcement middleware's BOOTSTRAP handling (installer routes
    stay open only until this flips true).
    """
    return _admin_key() is not None


def _consteq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def verify_admin_key(candidate: str) -> bool:
    """True iff ``candidate`` matches the configured admin key (or there's none)."""
    admin = _admin_key()
    return admin is not None and bool(candidate) and _consteq(candidate, admin)


def _tier_for_key(candidate: str) -> Tier | None:
    """Resolve a presented key to a tier, or ``None`` if it matches nothing.

    Admin key is checked first: an admin key presented anywhere a client
    key would be accepted should still resolve to the more powerful tier,
    not be rejected for "being the wrong kind of key".
    """
    if not candidate:
        return None
    admin = _admin_key()
    if admin is not None and _consteq(candidate, admin):
        return "admin"
    client = _client_key()
    if client is not None and _consteq(candidate, client):
        return "client"
    return None


def require_auth_enabled() -> bool:
    """The rollout posture: should the enforcement middleware do anything?

    Precedence (highest first):

    1. ``HAL0_REQUIRE_AUTH`` env var (``1``/``true``/``yes``/``on`` or
       ``0``/``false``/``no``/``off``) — an explicit runtime override.
    2. The persisted ``[security].require_auth`` config toggle the
       dashboard Security page writes (``PUT /api/auth/require``).
    3. **OFF** — the shipped default (operator decision 2026-07-19): hal0
       runs trusted-LAN-open unless auth is explicitly enabled.

    This inverts KB-1's original derived default (auto-enable on a
    non-loopback bind OR when a key was configured). That auto-on locked
    operators out of a dashboard that shipped no login UI (finding O19),
    so they disabled auth wholesale to use the product — the posture
    defeated itself. Auth is now explicit-enable only; the enforcement
    machinery, keys, and login flow are all unchanged once it IS enabled.
    """
    raw = os.environ.get("HAL0_REQUIRE_AUTH", "").strip().lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False

    persisted = _config_require_auth()
    if persisted is not None:
        return persisted

    return False


# Cheap live read of the persisted ``[security].require_auth`` toggle for
# the per-request enforcement hot path. Cached on ``(path, mtime)`` so the
# common configured-box case doesn't re-parse hal0.toml on every request,
# yet a Security-page toggle (which rewrites the file, bumping its mtime)
# is picked up on the very next request — no restart, no reload call.
_require_auth_cache: tuple[str, float, bool | None] | None = None


def _config_require_auth() -> bool | None:
    """Return the persisted ``[security].require_auth`` value, or ``None``.

    ``None`` means *unset* (no config file, no ``[security]`` table, or a
    malformed config) — the caller then applies the OFF default. This is
    best-effort by design: a broken hal0.toml must never make the auth
    gate raise on the request path.
    """
    global _require_auth_cache
    try:
        from hal0.config import paths

        target = str(paths.hal0_toml())
        try:
            mtime = os.stat(target).st_mtime
        except OSError:
            return None  # no config file on disk → unset

        cached = _require_auth_cache
        if cached is not None and cached[0] == target and cached[1] == mtime:
            return cached[2]

        from hal0.config.loader import load_hal0_config

        value = load_hal0_config().security.require_auth
        _require_auth_cache = (target, mtime, value)
        return value
    except Exception:  # pragma: no cover - defensive; never fail the gate
        return None


# ---------------------------------------------------------------------------
# Principal resolution (cookie -> bearer -> api_key)


def _headers_from_scope(scope: dict[str, Any]) -> dict[str, str]:
    """Lowercase header-name -> value, first occurrence wins.

    Operates on the raw ASGI ``scope["headers"]`` list so this works
    identically for both ``http`` and ``websocket`` scopes (Starlette's
    ``Request``/``WebSocket`` wrapper types differ; the raw scope doesn't).
    """
    out: dict[str, str] = {}
    for raw_key, raw_value in scope.get("headers") or ():
        key = raw_key.decode("latin-1").lower()
        if key not in out:
            out[key] = raw_value.decode("latin-1")
    return out


def _cookie_value(cookie_header: str, name: str) -> str | None:
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        if key.strip() == name:
            return value.strip()
    return None


def resolve_principal(scope: dict[str, Any]) -> AuthPrincipal:
    """Resolve an :class:`AuthPrincipal` from a raw ASGI ``scope``.

    Priority (per ``spec-kb1-auth.md``): cookie -> ``Authorization:
    Bearer`` -> ``?api_key=``. A valid ``hal0_session`` cookie always
    resolves to ``admin`` -- it's the operator's own browser dashboard
    session (:mod:`hal0.api.agents._auth` mints it and is the ONLY place
    that ever does; this module never mints its own cookie signature).
    """
    headers = _headers_from_scope(scope)

    cookie_header = headers.get("cookie", "")
    if cookie_header:
        cookie_value = _cookie_value(cookie_header, SESSION_COOKIE_NAME)
        if cookie_value and verify_session_cookie(cookie_value):
            return AuthPrincipal(tier="admin", source="cookie")

    authorization = headers.get("authorization", "")
    if authorization[:7].lower() == "bearer ":
        tier = _tier_for_key(authorization[7:].strip())
        if tier is not None:
            return AuthPrincipal(tier=tier, source="bearer")

    query_string = scope.get("query_string") or b""
    if query_string:
        params = parse_qs(query_string.decode("latin-1"))
        candidates = params.get("api_key") or []
        if candidates:
            tier = _tier_for_key(candidates[0])
            if tier is not None:
                return AuthPrincipal(tier=tier, source="api_key")

    return ANON


def resolve_principal_from_scope(scope: Any) -> AuthPrincipal:
    """Compatibility alias accepting a Starlette ``Request``/``WebSocket``.

    ``request.scope`` / ``websocket.scope`` is the raw ASGI mapping
    :func:`resolve_principal` expects; routes that already have a
    ``Request`` object (e.g. ``GET /api/auth/status``) call this directly
    instead of re-deriving the raw scope themselves.
    """
    raw_scope = getattr(scope, "scope", scope)
    return resolve_principal(raw_scope)


# ---------------------------------------------------------------------------
# Origin defence-in-depth (CSRF / drive-by-WS belt-and-suspenders)

# Methods that can change server state. Safe methods (GET/HEAD/OPTIONS) are
# exempt: they carry no side effect, and OPTIONS is the CORS preflight.
_STATE_CHANGING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _origin_allowed(scope: dict[str, Any]) -> bool:
    """Belt-and-suspenders Origin check for browser-driven requests.

    This runs *alongside* the tier auth above, not instead of it. The threat
    it closes is the browser-only one: a malicious page in the operator's own
    browser driving a state-changing request or opening a WebSocket against
    hal0 using the operator's ambient session cookie (classic CSRF / drive-by
    WS). Token-bearing programmatic callers (curl, the OpenAI SDK) are NOT the
    target — they have to steal a key first, and a stolen key works from
    anywhere regardless of Origin.

    Policy:

    * No ``Origin`` header  -> allow. Only browsers set ``Origin`` on
      cross-origin/WS requests; its absence means a same-origin navigation or
      a non-browser client, neither of which is a cross-site vector.
    * ``Origin`` in the shared allowlist (``HAL0_ALLOWED_ORIGINS`` /
      :func:`hal0.api.agents._auth.allowed_origins`) -> allow. One allowlist
      for both this gate and the chat-proxy WS gate.
    * ``Origin`` whose scheme://host[:port] equals the request ``Host``
      -> allow (genuine same-origin: the dashboard served from the very host
      it is calling, regardless of which LAN name/IP that is).
    * otherwise -> deny.
    """
    headers = _headers_from_scope(scope)
    origin = headers.get("origin", "")
    if not origin:
        return True

    from hal0.api.agents._auth import allowed_origins

    if origin in allowed_origins():
        return True

    # Same-origin: compare the Origin's netloc against the Host header. The
    # browser always sends both; equality means the request targets its own
    # origin, which no cross-site attacker page can forge.
    host = headers.get("host", "")
    return bool(host) and urlsplit(origin).netloc == host


# ---------------------------------------------------------------------------
# Enforcement decision


def _envelope(code: str, message: str) -> dict[str, Any]:
    # Mirrors hal0.api.middleware.error_codes._envelope's shape exactly so
    # a 401/403 from this middleware looks identical to one raised by a
    # route via hal0.errors.Unauthorized/Forbidden. Not imported directly:
    # that helper is private to error_codes.py and this middleware sits
    # OUTSIDE the exception-handling middleware Starlette wires those
    # into (it runs before routing), so it must build the JSON itself.
    return {"error": {"code": code, "message": message, "details": {}}}


def _decide(auth_class: AuthClass, principal: AuthPrincipal) -> tuple[bool, int, str]:
    """Return ``(allowed, status_if_denied, error_code_if_denied)``."""
    if auth_class is AuthClass.OPEN:
        return True, 200, ""

    if auth_class is AuthClass.BOOTSTRAP:
        # Open only until an admin key exists (spec-kb1-auth.md risk #3:
        # the first-run chicken-egg). Once configured, the installer is
        # exactly as sensitive as any other ADMIN route.
        if not has_admin_key():
            return True, 200, ""
        auth_class = AuthClass.ADMIN

    if auth_class is AuthClass.CLIENT:
        if principal.tier in ("client", "admin"):
            return True, 200, ""
        code = "auth.required" if principal.tier == "anon" else "auth.forbidden"
        status = 401 if principal.tier == "anon" else 403
        return False, status, code

    # ADMIN (including BOOTSTRAP-once-keyed, above).
    if principal.tier == "admin":
        return True, 200, ""
    code = "auth.required" if principal.tier == "anon" else "auth.forbidden"
    status = 401 if principal.tier == "anon" else 403
    return False, status, code


# ---------------------------------------------------------------------------
# Pure-ASGI enforcement middleware


class AuthEnforcementMiddleware:
    """Deny-by-default gate wired into ``create_app()`` after ``log_scrub``.

    A pure-ASGI middleware (not a per-route ``Depends``) is deliberate:
    with 60+ routers, a per-route dependency is one omission away from a
    hole. This runs on every request before it reaches routing, for both
    ``http`` (incl. SSE, a long-lived GET) and ``websocket`` scopes.

    Dev-open bypass: when :func:`require_auth_enabled` is false (the
    default on loopback with no keys configured), every request passes
    through untouched -- this is what keeps the pre-existing TestClient
    suite green without modification.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        if scope_type not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        if not require_auth_enabled():
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "/")
        # A WebSocket upgrade is an HTTP GET at the transport level; there
        # is no scope["method"] for a websocket scope, so classify() gets
        # the same pseudo-method the exposure-CI test uses.
        method = scope.get("method", "GET") if scope_type == "http" else "GET"

        # Origin defence-in-depth: reject browser cross-site WebSocket
        # upgrades and state-changing requests BEFORE tier auth. Requests
        # with no Origin (SDKs/curl/same-origin) pass through untouched --
        # see _origin_allowed for the full policy.
        origin_gated = scope_type == "websocket" or method in _STATE_CHANGING_METHODS
        if origin_gated and not _origin_allowed(scope):
            log.info(
                "hal0.auth.origin_rejected",
                path=path,
                method=method,
                scope_type=scope_type,
            )
            if scope_type == "websocket":
                await send({"type": "websocket.close", "code": 4403})
                return
            payload = json.dumps(_envelope("auth.origin_forbidden", "origin not allowed")).encode(
                "utf-8"
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": payload})
            return

        auth_class = classify(method, path)
        principal = resolve_principal(scope)
        allowed, status, code = _decide(auth_class, principal)

        if allowed:
            await self.app(scope, receive, send)
            return

        log.info(
            "hal0.auth.denied",
            path=path,
            method=method,
            auth_class=auth_class.value,
            principal_tier=principal.tier,
            status=status,
        )

        if scope_type == "websocket":
            # Code 4403 = policy violation. Matches
            # hal0.api.agents.chat_proxy's existing convention -- one
            # rejection code for "you may not upgrade this socket".
            await send({"type": "websocket.close", "code": 4403})
            return

        message = "authentication required" if status == 401 else "insufficient scope"
        payload = json.dumps(_envelope(code, message)).encode("utf-8")
        # Build the JSON body ourselves via two raw ASGI send() events
        # (what Starlette's JSONResponse.__call__ does under the hood) --
        # no Response import needed, and no dependency on the
        # exception-handling middleware this gate sits outside of.
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": payload})


__all__ = [
    "ANON",
    "AuthEnforcementMiddleware",
    "AuthPrincipal",
    "Tier",
    "has_admin_key",
    "require_auth_enabled",
    "resolve_principal",
    "resolve_principal_from_scope",
    "verify_admin_key",
]
