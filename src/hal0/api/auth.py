"""KB-1 / §1 authentication: principal resolution, posture, enforcement.

Three-tier, deny-by-default auth built around one classification table
(:mod:`hal0.security.exposure`, seam S9). This module owns:

- :class:`AuthPrincipal` — the resolved identity of a request (``anon`` /
  ``client`` / ``admin``, plus ``bridge`` for an agent-bridge client key).
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
  scopes; a denied WebSocket is rejected pre-accept with close code 4403.
  An admitted one stays registered with :mod:`hal0.api.live_sockets`, which
  closes it with 4401 once a key rotation or session expiry means its
  credential would no longer pass this gate (#2801). A streaming HTTP
  response (SSE) is held to the same rule and ended (#2816).

Posture-coupled ADMIN gate (#1822, H1/H3)
------------------------------------------
``require_auth_enabled()`` OFF does not mean "every request is anon-open"
any more. hal0-api binds ``0.0.0.0:8080`` by default (``install.sh``), so
the shipped combination — auth off, bind wide open — let any device on the
LAN drive every ADMIN-class route (model pulls, slot deletes, config
writes, approval execution) with zero credential. :func:`_lan_admin_gate`
closes that gap without touching the ``require_auth`` toggle itself: an
ADMIN-classified request is treated exactly as if enforcement were on
(needs an admin session/key) whenever ALL of these hold —

1. an admin key has been configured (:func:`has_admin_key`) — otherwise
   there would be no way to satisfy the requirement at all, the same
   first-run chicken-egg :class:`~hal0.security.exposure.AuthClass`
   ``BOOTSTRAP`` already carves out;
2. the box is bound beyond loopback (:func:`hal0.config.network.is_loopback_bind`
   is false); and
3. *this request's* own peer did not arrive over loopback.

A loopback-bound box, or a loopback-originating request against a
LAN-bound box (the operator at the console), stays fully frictionless —
"auth off" still means "open on loopback". ``OPEN``/``CLIENT`` routes
are unaffected either way -- but note the gate is per *class*, not per
method: an ADMIN-classified ``GET`` (settings, memory, approvals, the
activity/log streams) is refused exactly like an ADMIN mutation.
:func:`admin_gated` and :func:`admin_sign_in_required` report the
per-caller outcome on ``GET /api/auth/status`` so the dashboard can ask
for the key at the front door instead of rendering pages whose reads all
401.

The ComfyUI GPU gate's token (#3058)
------------------------------------
The ``hal0_gpu_gate`` node in the img container is not an operator and never
holds the admin key. :mod:`hal0.comfyui.gate_credential` mints it a token of
its own, which admits exactly its four requests (:data:`_GATE_REQUESTS`), from
the box itself only, when the ADMIN gate would refuse them, and marks the request with
:data:`GATE_SCOPE_KEY` so the switch handler holds it to image mode. The token
is not a tier: :func:`resolve_principal` never returns it, so it opens no
other route, no WebSocket and no stream.

The bridge's own credential (ADR-0043)
--------------------------------------
A bridge client (a coding agent on another machine) carries a key of its own,
``hal0bk_<name>.<token>``. The operator mints it, and ``bridge.db``
(:mod:`hal0.bridge.store`) keeps only its hash. :func:`resolve_principal`
accepts it from ``Authorization: Bearer`` only, never from ``?api_key=``, and
resolves it to the ``bridge`` tier carrying the client's name and scopes; a
revoked, rotated-out or unknown key resolves to ``anon``. The tier opens the
BRIDGE class (``/mcp/agents``, ``GET /api/bridge/me``) and the MCP mounts its
scopes name (:data:`BRIDGE_SCOPE_PREFIXES`); every other route refuses it with
403. Both checks run ahead of the dev-open bypass: a BRIDGE-class request is
never waved through, and a bridge key is held to its scopes on a box with auth
off as well. OPEN routes stay open to it, as to anyone.

``[bridge].enabled`` is recorded here when hal0-api boots
(:func:`set_bridge_enabled`). While it is off no bridge key verifies, and the
store is not even opened for one. A request the middleware admits as a bridge
principal carries that principal on its ASGI scope
(:data:`BRIDGE_PRINCIPAL_SCOPE_KEY`), and the handlers behind it (the MCP
identity resolvers, ``GET /api/bridge/me``) read it there through
:func:`judged_principal` instead of verifying the key again. Only
:func:`_still_admitted` verifies a second time, live, so revoking a key ends
the streams it opened.

The browser session cookie is **reused, not reimplemented**: minting and
verification both delegate to :mod:`hal0.api.agents._auth` (one
HMAC-SHA256 cookie scheme), so
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
import ipaddress
import json
import os
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qs

import structlog

from hal0.api import live_sockets
from hal0.api.agents._auth import SESSION_COOKIE_NAME, verify_session_cookie
from hal0.bridge.keys import KEY_PREFIX, SCOPE_SERVERS
from hal0.comfyui.gate_version import GATE_USER_AGENT_PREFIX, GATE_VERSION_HEADER
from hal0.config.network import bind_host, is_loopback_bind
from hal0.security.exposure import AuthClass, classify

if TYPE_CHECKING:
    from hal0.bridge.store import BridgeStore

log = structlog.get_logger(__name__)

Tier = Literal["anon", "client", "admin", "bridge"]

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class AuthPrincipal:
    """The resolved identity of an in-flight request.

    ``source`` is diagnostic only (audit logging / debugging) -- never
    used for authorization decisions; ``tier`` is, and for a bridge
    principal so is ``scopes``.

    ``name`` and ``scopes`` are set for the ``bridge`` tier only: the bridge
    client's name and the scopes it was granted (ADR-0043). Every other tier
    leaves them empty.
    """

    tier: Tier
    source: str = "none"
    name: str | None = None
    scopes: frozenset[str] = frozenset()


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


def trust_forwarded_for_enabled() -> bool:
    """Should caller-IP attribution (the login/rotate rate limiter) read
    ``X-Forwarded-For`` instead of the raw TCP peer? (GH #1476)

    Precedence (highest first), mirroring :func:`require_auth_enabled`:

    1. ``HAL0_TRUST_FORWARDED_FOR`` env var — an explicit runtime override.
    2. The persisted ``[security].trust_forwarded_for`` config toggle.
    3. **OFF** — the safe default. Behind a reverse proxy every direct
       peer is the proxy's own IP, so the raw peer collapses every real
       caller onto one shared rate-limit bucket (one guesser 429-locks
       everyone out) — but honouring the header unconditionally lets ANY
       caller pick its own bucket by forging it. This is opt-in only for
       an operator who has verified their reverse proxy strips/overwrites
       any client-supplied ``X-Forwarded-For`` (Traefik/nginx/Caddy do
       this in their default configurations).
    """
    raw = os.environ.get("HAL0_TRUST_FORWARDED_FOR", "").strip().lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False

    persisted = _config_trust_forwarded_for()
    if persisted is not None:
        return persisted

    return False


# Same cheap live-read-with-mtime-cache pattern as ``_config_require_auth``.
_trust_forwarded_for_cache: tuple[str, float, bool | None] | None = None


def _config_trust_forwarded_for() -> bool | None:
    """Return the persisted ``[security].trust_forwarded_for`` value, or
    ``None`` if unset/unreadable. Best-effort — see ``_config_require_auth``."""
    global _trust_forwarded_for_cache
    try:
        from hal0.config import paths

        target = str(paths.hal0_toml())
        try:
            mtime = os.stat(target).st_mtime
        except OSError:
            return None  # no config file on disk → unset

        cached = _trust_forwarded_for_cache
        if cached is not None and cached[0] == target and cached[1] == mtime:
            return cached[2]

        from hal0.config.loader import load_hal0_config

        value = load_hal0_config().security.trust_forwarded_for
        _trust_forwarded_for_cache = (target, mtime, value)
        return value
    except Exception:  # pragma: no cover - defensive; never fail the gate
        return None


# ---------------------------------------------------------------------------
# The bridge's own credential (ADR-0043) — see the module docstring.

#: The MCP mount each bridge scope opens, derived from the one scope table
#: (:data:`hal0.bridge.keys.SCOPE_SERVERS`). ``agents`` has no entry: every
#: bridge client holds it, and the BRIDGE class (``/mcp/agents``,
#: ``GET /api/bridge/me``) admits the tier itself.
BRIDGE_SCOPE_PREFIXES: Mapping[str, str] = MappingProxyType(
    {scope: mount for scope, (_server, mount) in SCOPE_SERVERS.items() if scope != "agents"}
)

#: Set on the ASGI scope of a request the middleware admitted as a bridge
#: principal: the :class:`AuthPrincipal` it was judged as. Handlers read it
#: through :func:`judged_principal`; a client cannot set an ASGI scope key.
BRIDGE_PRINCIPAL_SCOPE_KEY = "hal0.bridge_principal"

_bridge_store_lock = threading.Lock()
_bridge_store_instance: BridgeStore | None = None
_bridge_store_warned = False

#: ``[bridge].enabled`` as hal0-api read it at boot (ADR-0043, Decision 6).
#: On until the ``bridge_store`` boot phase records otherwise, like the
#: shipped default.
_bridge_enabled = True


def set_bridge_enabled(enabled: bool) -> None:
    """Record ``[bridge].enabled`` for this process (the ``bridge_store`` boot phase).

    Off, no bridge key verifies: :func:`_bridge_principal` answers ``None``
    without opening ``bridge.db``, so a disabled bridge never creates the file
    on a request's behalf (ADR-0043).
    """
    global _bridge_enabled
    _bridge_enabled = bool(enabled)


def bridge_enabled() -> bool:
    """``[bridge].enabled`` as recorded when hal0-api started (ADR-0043)."""
    return _bridge_enabled


def bridge_store() -> BridgeStore:
    """Return the process's bridge client store, opening it on first use.

    Built from :func:`hal0.config.paths.bridge_db` and opened (created,
    migrated, narrowed to 0600) once, then reused; rebuilt only when that path
    changes (``HAL0_HOME`` moves between tests). Raises what
    :meth:`~hal0.bridge.store.BridgeStore.open` raises; the auth path catches
    it in :func:`_bridge_principal`. Thread-safe: the live-socket ticker
    thread reaches it through :func:`_still_admitted`.
    """
    global _bridge_store_instance
    from hal0.bridge import store as bridge_store_mod
    from hal0.config import paths

    path = paths.bridge_db()
    with _bridge_store_lock:
        current = _bridge_store_instance
        if current is None or current.path != path:
            current = bridge_store_mod.BridgeStore(path)
            current.open()
            _bridge_store_instance = current
        return current


def _bridge_principal(candidate: str) -> AuthPrincipal | None:
    """Resolve a ``hal0bk_`` key to its bridge principal, or ``None``.

    ``None`` while the bridge is disabled (without building the store), for
    an unknown, revoked or rotated-out key, and also when the store cannot be
    opened or read (an unwritable ``var_lib``, a damaged file): the key then
    simply does not verify. A store failure is logged once per process at
    warning, and never fails the request.
    """
    global _bridge_store_warned
    if not _bridge_enabled:
        return None
    try:
        row = bridge_store().verify_key(candidate)
    except Exception as exc:  # deny by default; never 500 the auth gate
        if not _bridge_store_warned:
            _bridge_store_warned = True
            log.warning("hal0.auth.bridge_store_unavailable", error=f"{type(exc).__name__}: {exc}")
        return None
    if row is None:
        return None
    return AuthPrincipal(tier="bridge", source="bearer", name=row.name, scopes=row.scopes)


def bridge_scope_admits(path: str, principal: AuthPrincipal) -> bool:
    """True iff ``path`` lies under the MCP mount of a scope ``principal`` holds.

    Only a ``bridge`` principal is ever admitted. The match is boundary-safe
    (``/mcp/memory2`` is not under ``/mcp/memory``), and a path with a ``.``
    or ``..`` segment is refused outright rather than reasoned about.
    """
    if principal.tier != "bridge":
        return False
    if any(segment in (".", "..") for segment in path.split("/")):
        return False
    for scope_name in principal.scopes:
        prefix = BRIDGE_SCOPE_PREFIXES.get(scope_name)
        if prefix is not None and (path == prefix or path.startswith(prefix + "/")):
            return True
    return False


def bears_bridge_key(scope: Any) -> bool:
    """True iff the request's bearer is shaped like a bridge key (no store read).

    Accepts a raw ASGI scope or a Starlette ``Request``/``WebSocket``. The
    MCP identity resolvers use it to refuse the ``X-hal0-Agent`` fallback to
    a bridge-shaped bearer the middleware did not admit as a bridge principal.
    """
    raw_scope = getattr(scope, "scope", scope)
    token = bearer_token(_headers_from_scope(raw_scope))
    return token is not None and token.startswith(KEY_PREFIX)


def judged_principal(scope: Any) -> AuthPrincipal:
    """The principal a handler acts as, without verifying a bridge key again.

    The bridge principal the middleware stored on the scope
    (:data:`BRIDGE_PRINCIPAL_SCOPE_KEY`) when it admitted the request, else
    the cookie or box key the request carries. A bridge-shaped bearer with no
    stored principal is never looked up here: it resolves as an unknown key
    does, so a store error or a revoke race can never hand a handler an
    identity the gate did not judge. Accepts a raw ASGI scope or a Starlette
    ``Request``/``WebSocket``.
    """
    raw_scope = getattr(scope, "scope", scope)
    stored = raw_scope.get(BRIDGE_PRINCIPAL_SCOPE_KEY)
    if isinstance(stored, AuthPrincipal) and stored.tier == "bridge":
        return stored
    return resolve_principal(raw_scope, verify_bridge=False)


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


def bearer_token(headers: Mapping[str, str]) -> str | None:
    """The ``Authorization: Bearer`` token in ``headers``, stripped, or ``None``.

    ``headers`` is keyed by lowercase name (:func:`_headers_from_scope`, or a
    Starlette ``Headers``). The one parser :func:`resolve_principal`,
    :func:`bears_bridge_key`, the ComfyUI gate check and the MCP mount's
    bearer read share, so they can never disagree on what a request carries.
    """
    authorization = headers.get("authorization", "")
    if authorization[:7].lower() != "bearer ":
        return None
    return authorization[7:].strip()


def _cookie_value(cookie_header: str, name: str) -> str | None:
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        if key.strip() == name:
            return value.strip()
    return None


def resolve_principal(scope: dict[str, Any], *, verify_bridge: bool = True) -> AuthPrincipal:
    """Resolve an :class:`AuthPrincipal` from a raw ASGI ``scope``.

    Priority (per ``spec-kb1-auth.md``): cookie -> ``Authorization:
    Bearer`` -> ``?api_key=``. A valid ``hal0_session`` cookie always
    resolves to ``admin`` -- it's the operator's own browser dashboard
    session (:mod:`hal0.api.agents._auth` mints it and is the ONLY place
    that ever does; this module never mints its own cookie signature).

    A bearer carrying the ``hal0bk_`` prefix is looked up in the bridge store
    (ADR-0043) and, when it verifies, resolves to the ``bridge`` tier. One
    that does not verify goes on to the box keys, which it cannot match, and
    so ends ``anon``. The ``?api_key=`` fallback never accepts a bridge key.
    Any other bearer never touches the store. ``verify_bridge=False`` skips
    the store for a bridge-shaped bearer too, treating it as unknown; that is
    what :func:`judged_principal` uses once the middleware has judged it.
    """
    headers = _headers_from_scope(scope)

    cookie_header = headers.get("cookie", "")
    if cookie_header:
        cookie_value = _cookie_value(cookie_header, SESSION_COOKIE_NAME)
        if cookie_value and verify_session_cookie(cookie_value):
            return AuthPrincipal(tier="admin", source="cookie")

    candidate = bearer_token(headers)
    if candidate is not None:
        if verify_bridge and candidate.startswith(KEY_PREFIX):
            bridge = _bridge_principal(candidate)
            if bridge is not None:
                return bridge
        tier = _tier_for_key(candidate)
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
      :func:`hal0.api.agents._auth.allowed_origins`) -> allow.
    * ``Origin`` equal to the request's own origin on an **IP-literal**
      ``Host`` -> allow (the dashboard reached by a LAN IP the install-time
      allowlist doesn't know, e.g. after a DHCP change), via
      :func:`hal0.api.agents._auth._is_same_origin`. A DNS-name ``Host``
      never takes this path: an attacker page can rebind its own name to
      the box and send a matching ``Origin``/``Host`` pair (#2327). Named
      origins (``hal0.local``, the box hostname, a reverse proxy's
      ``public_url``) reach the box through the allowlist instead.
    * otherwise -> deny.
    """
    headers = _headers_from_scope(scope)
    origin = headers.get("origin", "")
    if not origin:
        return True

    from hal0.api.agents._auth import _is_same_origin, allowed_origins

    if origin in allowed_origins():
        return True

    return _is_same_origin(origin, headers.get("host", ""), scope.get("scheme", "http"))


# ---------------------------------------------------------------------------
# Posture-coupled ADMIN gate (#1822) — see the module docstring.


def _is_loopback_peer(scope: Mapping[str, Any]) -> bool:
    """True iff the ASGI ``scope["client"]`` peer address is loopback.

    Missing client info (``None`` — some non-TCP test transports) is
    treated as NOT loopback: deny-by-default, same philosophy as
    :mod:`hal0.security.exposure`'s unclassified-path fallback. An
    unparseable address (shouldn't happen for a real ASGI server) is the
    same.
    """
    client = scope.get("client")
    if not client:
        return False
    try:
        return ipaddress.ip_address(client[0]).is_loopback
    except ValueError:
        return False


def _lan_admin_gate(auth_class: AuthClass, scope: dict[str, Any]) -> bool:
    """True iff this request must be treated as if enforcement were ON.

    Only ever true for :data:`AuthClass.ADMIN`. See the module docstring
    ("Posture-coupled ADMIN gate") for the full three-condition rule.
    """
    if auth_class is not AuthClass.ADMIN:
        return False
    if not has_admin_key():
        return False
    if is_loopback_bind():
        return False
    return not _is_loopback_peer(scope)


# ---------------------------------------------------------------------------
# Enforcement decision


# The 403 body names the remedy rather than echoing the caller's Origin.
# A named origin (a reverse proxy's domain, a router-assigned DNS name)
# passes only through the allowlist since #2327; `hal0 network` persists one
# in hal0.toml's [network] section (#2382).
_ORIGIN_FORBIDDEN_MSG = (
    "origin not allowed; to allow a reverse-proxy or other named origin, run "
    "`hal0 network public-url set <url>` or `hal0 network origins add <origin>`"
)


def _envelope(code: str, message: str) -> dict[str, Any]:
    # Mirrors hal0.api.middleware.error_codes._envelope's shape exactly so
    # a 401/403 from this middleware looks identical to one raised by a
    # route via hal0.errors.Unauthorized/Forbidden. Not imported directly:
    # that helper is private to error_codes.py and this middleware sits
    # OUTSIDE the exception-handling middleware Starlette wires those
    # into (it runs before routing), so it must build the JSON itself.
    return {"error": {"code": code, "message": message, "details": {}}}


# ---------------------------------------------------------------------------
# The ComfyUI GPU gate's own credential (#3058)

#: The only requests the ComfyUI GPU gate's token admits: its arbiter read,
#: its prompt hand-off, its switch request and its report of a refused prompt,
#: matched on the exact method and path. The token lives in
#: :mod:`hal0.comfyui.gate_credential`.
_GATE_REQUESTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/comfyui/arbiter"),
        ("POST", "/api/comfyui/prompt-handoff"),
        ("POST", "/api/comfyui/switchover"),
        ("POST", "/api/comfyui/gate-refusal"),
    }
)

#: Set to ``True`` on the ASGI scope of a request the gate's token admitted.
#: The switch handler reads it to hold such a request to a plain switch into
#: image mode (no inference, no ``force``, no ``pin``).
GATE_SCOPE_KEY = "hal0.comfyui_gate"


def _gate_peer(scope: Mapping[str, Any]) -> bool:
    """True iff the request comes from where the gate dials: the box itself.

    The img container shares the host's network, so the gate reaches hal0-api
    on loopback, or on the one address hal0-api binds (a request from the box
    to its own address arrives from that address). A token carried off the box
    is refused from anywhere else.
    """
    if _is_loopback_peer(scope):
        return True
    client = scope.get("client")
    if not client:
        return False
    try:
        return ipaddress.ip_address(client[0]) == ipaddress.ip_address(bind_host())
    except ValueError:
        return False


def _gate_admits(scope: dict[str, Any], method: str, path: str) -> bool:
    """True iff this is one of the gate's requests, from the box, carrying its token."""
    if scope.get("type") != "http" or (method, path) not in _GATE_REQUESTS:
        return False
    if not _gate_peer(scope):
        return False
    token = bearer_token(_headers_from_scope(scope))
    if token is None:
        return False
    from hal0.comfyui import gate_credential

    return gate_credential.verify(token)


#: When a request shaped like the ComfyUI gate's, from the box, without the gate's
#: version header, was last refused (epoch seconds), else ``None``. Only a marker
#: for ``GET /api/comfyui/status``: no decision here reads it.
_gate_path_refused_at: float | None = None

#: The requests whose refusal can mark an old gate: the gate's own, and the one v1.6.5's
#: gate made instead of ``/arbiter`` (its status read). Admission never reads this set;
#: it is :data:`_GATE_REQUESTS` alone.
_GATE_MARK_REQUESTS: frozenset[tuple[str, str]] = _GATE_REQUESTS | {
    ("GET", "/api/comfyui/status"),
}


def gate_path_refused_at() -> float | None:
    """When the auth layer last refused a version-less gate request, else ``None``.

    A gate that predates ``X-Hal0-Gate-Version`` (or the token) is refused here, on a
    box that enforces auth, before it reaches a route that could record its call. The
    marker is how ``/status`` still tells that engine to restart.
    """
    return _gate_path_refused_at


def _note_unversioned_gate_refusal(scope: dict[str, Any], method: str, path: str) -> None:
    """Set :data:`_gate_path_refused_at` for a refused request that looks like an old gate's.

    Called only after the middleware has decided to refuse. It reads the request and
    changes nothing about the refusal. All four must hold: one of the gate's
    ``(method, path)`` pairs or its predecessor's status read
    (:data:`_GATE_MARK_REQUESTS`, HTTP only), from where the gate dials
    (:func:`_gate_peer`), none of the gate's version header, and the gate's default HTTP
    client as its ``User-Agent``. For the gate's own pairs the first two are the checks
    :func:`_gate_admits` has just run on this request.

    The User-Agent test is a heuristic, scoped to those loopback gate paths: it keeps a
    browser or ``curl`` on the box (an operator, a dashboard session that expired) from
    marking an engine as old. Anything on the box can send that header, which costs
    nothing worse than ``/status`` saying "restart the engine" until the next gate call.
    """
    global _gate_path_refused_at
    if scope.get("type") != "http" or (method, path) not in _GATE_MARK_REQUESTS:
        return
    if not _gate_peer(scope):
        return
    headers = _headers_from_scope(scope)
    if GATE_VERSION_HEADER.lower() in headers:
        return
    if not headers.get("user-agent", "").startswith(GATE_USER_AGENT_PREFIX):
        return
    _gate_path_refused_at = time.time()


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

    if auth_class is AuthClass.BRIDGE:
        # ADR-0043: a bridge client key, or the operator's admin credential.
        if principal.tier in ("bridge", "admin"):
            return True, 200, ""
        code = "auth.required" if principal.tier == "anon" else "auth.forbidden"
        status = 401 if principal.tier == "anon" else 403
        return False, status, code

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


def _verdict(auth_class: AuthClass, principal: AuthPrincipal, path: str) -> tuple[bool, int, str]:
    """:func:`_decide`, with a bridge principal held to its scopes (ADR-0043).

    Outside OPEN and BRIDGE, a bridge principal is admitted only on the MCP
    mount of a scope it holds (:func:`bridge_scope_admits`) and refused with
    403 everywhere else, whatever class the path has.
    """
    if principal.tier == "bridge" and auth_class not in (AuthClass.OPEN, AuthClass.BRIDGE):
        if bridge_scope_admits(path, principal):
            return True, 200, ""
        return False, 403, "auth.forbidden"
    return _decide(auth_class, principal)


def _enforced_principal(auth_class: AuthClass, scope: dict[str, Any]) -> AuthPrincipal | None:
    """The principal this request is judged as, or ``None`` when it rides the dev-open bypass.

    The bypass (enforcement off and the posture-coupled ADMIN gate not in
    effect) never applies to a BRIDGE-class request, and never to a bridge
    key outside OPEN routes: a bridge key is held to its scopes whatever the
    posture (ADR-0043). Otherwise the bypass is as before and resolves no
    principal; only a bearer shaped like a bridge key costs a store read.
    """
    if (
        auth_class is AuthClass.BRIDGE
        or require_auth_enabled()
        or _lan_admin_gate(auth_class, scope)
    ):
        return resolve_principal(scope)
    if auth_class is AuthClass.OPEN or not bears_bridge_key(scope):
        return None
    principal = resolve_principal(scope)
    return principal if principal.tier == "bridge" else None


def admin_gated(scope: Any) -> bool:
    """True iff ADMIN-class requests from this caller are enforced at all.

    I.e. an admin session/key is what stands between this caller and ADMIN
    routes: explicit enforcement is on, or the posture-coupled gate
    (:func:`_lan_admin_gate`) applies to this request's own peer. This is
    exactly the condition under which :class:`AuthEnforcementMiddleware`
    stops waving an ADMIN request through, and it is independent of whether
    the caller currently holds a session -- which is the point: the
    dashboard uses it to tell a session that grants access (worth showing,
    worth logging out of) from a cookie held by a caller the gate exempts.

    Accepts a raw ASGI scope or a Starlette ``Request``/``WebSocket``.
    """
    raw_scope = getattr(scope, "scope", scope)
    return require_auth_enabled() or _lan_admin_gate(AuthClass.ADMIN, raw_scope)


def admin_sign_in_required(scope: Any) -> bool:
    """True iff an ADMIN-class request from this caller would be refused now.

    The dashboard's single front-door signal (``GET /api/auth/status``).
    ``auth_required`` alone cannot answer it: the posture-coupled gate
    (:func:`_lan_admin_gate`) enforces ADMIN routes -- reads included --
    while enforcement itself reads OFF, and whether it applies depends on
    this request's own peer, which only the server can see. Built from the
    same two predicates :class:`AuthEnforcementMiddleware` runs
    (:func:`admin_gated` + :func:`_decide`) so the answer cannot drift
    from what the middleware would actually do.

    Accepts a raw ASGI scope or a Starlette ``Request``/``WebSocket``.
    """
    raw_scope = getattr(scope, "scope", scope)
    if not admin_gated(raw_scope):
        return False
    allowed, _status, _code = _decide(AuthClass.ADMIN, resolve_principal(raw_scope))
    return not allowed


def _still_admitted(auth_class: AuthClass, scope: dict[str, Any]) -> bool:
    """Would this open WebSocket's upgrade, or streaming request, pass the gate now?

    Re-runs the middleware's own posture + :func:`_decide` against the
    credential the request carried (a WebSocket or an open stream cannot
    change its cookie, bearer or ``api_key`` later), so a rotated key, a
    replaced session secret or an expired session revokes exactly the
    sockets and streams it would refuse to open today. The Origin half is not
    repeated: it cannot change for an open connection.

    A request the middleware admitted as a bridge principal
    (:data:`BRIDGE_PRINCIPAL_SCOPE_KEY` on its scope) is held to that
    credential whatever the posture (ADR-0043). With enforcement off, a key
    that stops verifying leaves :func:`_enforced_principal` with nothing to
    judge on a CLIENT-class route, which reads as the dev-open bypass and
    would keep the stream alive. So the bearer is verified again, live, and
    the stream ends unless it still resolves to the same bridge client (not
    revoked, rotated out or switched off with the bridge). A stream that
    never held a bridge principal, an anonymous dev-open one, is judged as
    before. The stored principal is cleared on failure, so a handler behind
    the ended stream no longer acts as that client.
    """
    stored = scope.get(BRIDGE_PRINCIPAL_SCOPE_KEY)
    if isinstance(stored, AuthPrincipal) and stored.tier == "bridge":
        principal = resolve_principal(scope)
        if principal.tier != "bridge" or principal.name != stored.name:
            scope.pop(BRIDGE_PRINCIPAL_SCOPE_KEY, None)
            return False
    else:
        enforced = _enforced_principal(auth_class, scope)
        if enforced is None:
            return True
        principal = enforced
    allowed, _status, _code = _verdict(auth_class, principal, scope.get("path", "/"))
    return allowed


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
    suite green without modification. The one exception is the
    posture-coupled ADMIN gate (:func:`_lan_admin_gate`, #1822): an
    ADMIN-classified request from a non-loopback peer against a
    non-loopback bind with an admin key already configured is enforced
    even while the dev-open bypass is otherwise in effect. The bridge
    credential (ADR-0043) is the other: a BRIDGE-class request, and any
    request bearing a bridge key outside OPEN routes, is judged here at
    every posture (:func:`_enforced_principal`).
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        if scope_type not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "/")
        # A WebSocket upgrade is an HTTP GET at the transport level; there
        # is no scope["method"] for a websocket scope, so classify() gets
        # the same pseudo-method the exposure-CI test uses.
        method = scope.get("method", "GET") if scope_type == "http" else "GET"
        auth_class = classify(method, path)

        principal = _enforced_principal(auth_class, scope)
        if principal is None:
            await self.app(scope, receive, send)
            return

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
                # Truncated: the header is caller-supplied. Logged so an
                # operator behind a reverse proxy can see which origin to
                # allow (#2327).
                origin=_headers_from_scope(scope).get("origin", "")[:200],
            )
            if scope_type == "websocket":
                await send({"type": "websocket.close", "code": 4403})
                return
            payload = json.dumps(_envelope("auth.origin_forbidden", _ORIGIN_FORBIDDEN_MSG)).encode(
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

        allowed, status, code = _verdict(auth_class, principal, path)

        if allowed:
            if principal.tier == "bridge":
                # What the handlers behind this request act as (ADR-0043); they
                # read it through judged_principal() and never verify again.
                scope[BRIDGE_PRINCIPAL_SCOPE_KEY] = principal
            if scope_type == "websocket":
                # The upgrade was the only check; keep the socket only while
                # its credential still passes this same gate (#2801).
                await live_sockets.guard_websocket(
                    self.app,
                    scope,
                    receive,
                    send,
                    lambda: _still_admitted(auth_class, scope),
                )
                return
            if auth_class is AuthClass.OPEN:
                await self.app(scope, receive, send)
                return
            # Same for a response that streams (SSE feeds, streamed
            # completions): it ends once its credential stops passing (#2816).
            await live_sockets.guard_http_stream(
                self.app,
                scope,
                receive,
                send,
                lambda: _still_admitted(auth_class, scope),
            )
            return

        if _gate_admits(scope, method, path):
            scope[GATE_SCOPE_KEY] = True
            await self.app(scope, receive, send)
            return

        _note_unversioned_gate_refusal(scope, method, path)

        log.info(
            "hal0.auth.denied",
            path=path,
            method=method,
            auth_class=auth_class.value,
            principal_tier=principal.tier,
            bridge_client=principal.name,
            status=status,
        )

        if scope_type == "websocket":
            # Code 4403 = policy violation: one rejection code for
            # "you may not upgrade this socket".
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
    "BRIDGE_PRINCIPAL_SCOPE_KEY",
    "BRIDGE_SCOPE_PREFIXES",
    "GATE_SCOPE_KEY",
    "AuthEnforcementMiddleware",
    "AuthPrincipal",
    "Tier",
    "admin_gated",
    "admin_sign_in_required",
    "bearer_token",
    "bears_bridge_key",
    "bridge_enabled",
    "bridge_scope_admits",
    "bridge_store",
    "gate_path_refused_at",
    "has_admin_key",
    "judged_principal",
    "require_auth_enabled",
    "resolve_principal",
    "resolve_principal_from_scope",
    "set_bridge_enabled",
    "verify_admin_key",
]
