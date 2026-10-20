"""Origin allowlist + HMAC session-cookie helpers for the dashboard.

Two browser-facing seams live here:

1. The Origin allowlist (:func:`allowed_origins`). Configured via
   ``HAL0_ALLOWED_ORIGINS`` (comma-separated), seeded by the installer
   (``hal0.install.network``) and extended by ``[network]`` in
   ``hal0.toml``. :func:`_is_same_origin` is the IP-literal same-origin
   fallback, so a LAN IP change never strands the dashboard (#2277).
   ``hal0.api.auth._origin_allowed`` enforces both on every REST call and
   WebSocket upgrade.

2. The HMAC session cookie (``hal0_session``) that ``POST /api/auth/login``
   mints and the enforcement middleware verifies. The cookie payload is
   JSON ``{"session_id": "<uuid>", "expires_at": <unix-ts>}``. The
   signature is ``HMAC-SHA256(<secret>, <base64url(payload)>)``. The secret
   comes from ``/var/lib/hal0/agents/secret.bin`` (chmod 0600, generated on
   first use). The cookie is set ``HttpOnly``, ``SameSite=Lax``, and
   ``Secure`` whenever the request arrived over TLS (directly or via a
   proxy's ``X-Forwarded-Proto``). Replacing the secret
   (:func:`regenerate_secret`) signs every session out at once.

The module sits under ``hal0.api.agents`` because the agent chat proxy
introduced it; that proxy is gone (ADR-0033) and the seams outlived it.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import tempfile
import time
import uuid
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from fastapi import Request, Response

# Default-deny set of browser origins permitted to call hal0-api from a
# browser. Operators can override at boot via ``HAL0_ALLOWED_ORIGINS``
# (comma-separated). The default covers:
#   - http://hal0.local         — mDNS / hosts-file alias
#   - http://localhost:5173     — Vite dev server (UI hot-reload)
#   - http://localhost:8080     — bundled SPA served from hal0-api
#   - http://127.0.0.1:8080       (both loopback spellings)
# The installer replaces this with a host-derived list
# (hal0.install.network). ``localhost`` is listed explicitly because the
# same-origin fallback (:func:`_is_same_origin`) accepts IP literals only
# (#2327); a loopback name is not a rebinding vector, since browsers
# resolve ``localhost`` locally and an attacker page cannot own that origin.
DEFAULT_ALLOWED_ORIGINS: Final[tuple[str, ...]] = (
    "http://hal0.local",
    "http://localhost:5173",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
)

# Cookie name + default lifetime. 8h covers a workday session.
SESSION_COOKIE_NAME: Final[str] = "hal0_session"
SESSION_COOKIE_TTL_SECONDS: Final[int] = 8 * 60 * 60

# "Remember me" lifetime (``POST /api/auth/login`` with ``remember: true``).
# Same cookie, same signature, later ``expires_at`` -- an operator who ticks
# it is not sent back to the login every morning. There is no server-side
# session table, so a cookie cannot be revoked one at a time: replacing the
# HMAC secret (:func:`regenerate_secret`, run by an admin key rotation)
# invalidates every session at once.
SESSION_COOKIE_REMEMBER_TTL_SECONDS: Final[int] = 30 * 24 * 60 * 60

# Secret file location. Lives under /var/lib/hal0 so the systemd unit's
# ``ReadWritePaths=/var/lib/hal0`` already covers it. The path is
# overridable for tests via ``HAL0_AGENT_SECRET_PATH``.
DEFAULT_SECRET_PATH: Final[str] = "/var/lib/hal0/agents/secret.bin"


def _secret_path() -> Path:
    """Resolve the on-disk path for the HMAC secret.

    Honours ``HAL0_AGENT_SECRET_PATH`` for tests + alternate installs.
    """
    return Path(os.environ.get("HAL0_AGENT_SECRET_PATH", DEFAULT_SECRET_PATH))


def _load_or_create_secret() -> bytes:
    """Return the HMAC secret, generating it on first call.

    The file is created with mode 0600 so only the hal0 service user can
    read it. The directory is created with mode 0700 for the same
    reason — leaking the secret would let any LAN host mint cookies.
    """
    path = _secret_path()
    if path.exists():
        # Re-tighten perms on every read in case an operator's chmod has
        # drifted. Cheap and idempotent.
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        return path.read_bytes()

    # First-run: generate 32 bytes from urandom + drop a tight mode.
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path.parent, 0o700)
    secret = secrets.token_bytes(32)
    # Write via a tmp + rename so a torn write never leaves an empty
    # secret on disk.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(secret)
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return secret


def regenerate_secret() -> bool:
    """Replace the HMAC secret, which signs out every session at once (#2633).

    Every ``hal0_session`` cookie is signed with this secret and nothing else,
    so a new secret makes all of them stop verifying on their next request.
    The new value is written to a unique tmpfile beside the old one (mode
    0600, same owner and group as the file it replaces, fsynced) and swapped
    in with ``os.replace``, so a reader sees the old secret or the new one and
    never a torn or empty file. A failure before the rename leaves the old
    secret in place and raises.

    Returns ``False`` without writing when no secret exists yet: then no
    cookie can verify anyway, and creating the file here (``hal0 auth
    reset-key`` runs as root) could leave one the service user cannot read.
    The next mint creates it as usual.
    """
    path = _secret_path()
    try:
        st = path.stat()
    except FileNotFoundError:
        return False

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        if os.geteuid() == 0 and (st.st_uid, st.st_gid) != (0, os.getegid()):
            # Keep the service user's ownership when root rotates from the CLI.
            # Only root may chown; the service user rotating its own secret
            # keeps the file as its own even if the old gid was one it is not
            # a member of, rather than failing the whole rotation.
            os.fchown(fd, st.st_uid, st.st_gid)
        os.write(fd, secrets.token_bytes(32))
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(tmp, path)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return True


def _b64url_encode(data: bytes) -> str:
    """URL-safe base64 without padding (matches JWT conventions)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """Reverse of :func:`_b64url_encode`. Restores padding before decode."""
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def allowed_origins() -> tuple[str, ...]:
    """Effective allowlist: the env list (or default) plus ``[network]`` origins.

    ``HAL0_ALLOWED_ORIGINS`` (installer-managed, replaces the default when
    set) comes first. A misconfigured env (empty string) falls back to the
    default rather than denying everything — first-run dev convenience.
    On top of that, the persisted ``hal0.toml`` ``[network]`` section adds
    the origin of ``public_url`` and every ``extra_origins`` entry (#2382),
    deduplicated in order. Those survive an installer re-run, which
    rewrites the env list.
    """
    raw = os.environ.get("HAL0_ALLOWED_ORIGINS", "").strip()
    parsed = tuple(o.strip() for o in raw.split(",") if o.strip()) if raw else ()
    base = parsed or DEFAULT_ALLOWED_ORIGINS
    extra = _config_network_origins()
    if not extra:
        return base
    return tuple(dict.fromkeys((*base, *extra)))


# Live read of ``[network]`` for the per-request origin gates, cached on the
# file's path plus ``(mtime_ns, inode, size)``
# (:func:`hal0.config.loader.config_file_key`): the common case never
# re-parses hal0.toml, yet a Settings save, a ``hal0 network`` command or a
# hand edit takes effect on the very next request, with no restart.
_network_origins_cache: tuple[str, tuple[int, int, int], tuple[str, ...]] | None = None


def _config_network_origins() -> tuple[str, ...]:
    """Origins persisted in ``[network]``: ``public_url``'s, then ``extra_origins``.

    Best-effort: no file, no ``[network]`` table, or an unloadable config
    yields ``()``, so a broken hal0.toml never makes the gate raise (the
    env list still applies).
    """
    global _network_origins_cache
    try:
        from hal0.config import paths
        from hal0.config.loader import config_file_key

        target = str(paths.hal0_toml())
        key = config_file_key(target)
        if key is None:
            return ()

        cached = _network_origins_cache
        if cached is not None and cached[0] == target and cached[1] == key:
            return cached[2]

        from hal0.config.loader import load_hal0_config
        from hal0.install.network import _origin_of

        try:
            network = load_hal0_config().network
        except Exception:
            origins: tuple[str, ...] = ()
        else:
            public = _origin_of(network.public_url)
            origins = tuple(dict.fromkeys([*([public] if public else []), *network.extra_origins]))
        _network_origins_cache = (target, key, origins)
        return origins
    except Exception:  # pragma: no cover - defensive; never fail the gate
        return ()


def mint_session_cookie(
    now: float | None = None, *, ttl_seconds: int = SESSION_COOKIE_TTL_SECONDS
) -> str:
    """Generate a fresh signed session cookie value.

    The payload is JSON-serialised ``{"session_id", "expires_at"}``; the
    output is ``<b64url(payload)>.<b64url(hmac)>``. Caller is responsible
    for setting it on a response via :func:`set_session_cookie`.

    ``ttl_seconds`` sets how far ahead ``expires_at`` lands. The expiry is
    inside the signed payload, so a holder cannot extend their own session.
    """
    secret = _load_or_create_secret()
    ts = int(now if now is not None else time.time())
    payload = {
        "session_id": uuid.uuid4().hex,
        "expires_at": ts + ttl_seconds,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(secret, payload_bytes, hashlib.sha256).digest()
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(sig)}"


def session_cookie_expiry(value: str, now: float | None = None) -> int | None:
    """Return the cookie's ``expires_at`` iff its signature is valid AND unexpired.

    ``None`` for anything else -- absent, unparseable, forged or expired --
    so a caller can never act on an expiry the server did not sign.
    Constant-time compare on the signature keeps a timing oracle off the
    table; no exception leaks.
    """
    if not value or "." not in value:
        return None
    try:
        payload_b64, sig_b64 = value.split(".", 1)
        payload_bytes = _b64url_decode(payload_b64)
        sig = _b64url_decode(sig_b64)
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return None

    secret = _load_or_create_secret()
    expected = hmac.new(secret, payload_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig):
        return None

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):  # JSONDecodeError is a ValueError
        return None
    if not isinstance(payload, dict):
        return None

    expires_at = payload.get("expires_at")
    # bool is an int subclass; a signed payload never carries one, but be exact.
    if not isinstance(expires_at, int) or isinstance(expires_at, bool):
        return None
    ts = int(now if now is not None else time.time())
    return expires_at if ts < expires_at else None


def verify_session_cookie(value: str, now: float | None = None) -> bool:
    """Return ``True`` iff the cookie's signature is valid AND unexpired."""
    return session_cookie_expiry(value, now) is not None


def request_uses_tls(request: Request) -> bool:
    """Did the browser reach us over TLS?

    True when the request itself arrived over ``https``, or a reverse proxy
    that terminated TLS says so with ``X-Forwarded-Proto`` (hal0-api itself
    listens on plain HTTP; the documented deployment puts a proxy in front).
    The header is honoured without a trust setting on purpose: it only ever
    ADDS the ``Secure`` attribute to the sender's own cookie, so a forged
    value can restrict nobody but the forger.
    """
    if request.url.scheme == "https":
        return True
    forwarded = request.headers.get("x-forwarded-proto", "")
    return forwarded.split(",", 1)[0].strip().lower() == "https"


def set_session_cookie(
    response: Response,
    *,
    secure: bool | None = None,
    ttl_seconds: int = SESSION_COOKIE_TTL_SECONDS,
) -> str:
    """Mint + attach a session cookie to ``response``. Returns its value.

    ``secure`` marks the cookie ``Secure`` so the browser never sends it in
    cleartext; callers pass :func:`request_uses_tls` for the request being
    answered, so a plain-HTTP install keeps a cookie it can actually use
    while a TLS-proxied one gets the attribute. ``ttl_seconds`` sets both
    halves of the lifetime together -- the signed ``expires_at`` the server
    honours and the ``Max-Age`` the browser keeps the cookie for.
    """
    value = mint_session_cookie(ttl_seconds=ttl_seconds)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        value,
        max_age=ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=bool(secure),
        path="/",
    )
    return value


def _is_same_origin(origin: str, host: str, request_scheme: str) -> bool:
    """True iff ``origin`` is this request's own origin on an IP-literal host.

    The same-origin fallback of the REST/WS enforcement middleware's Origin
    gate (``hal0.api.auth._origin_allowed``, #2327).

    This is the "box's LAN IP changed since install" case (#2277): the
    browser typed ``http://<ip>:<port>``, so it sends that as ``Origin``
    and ``<ip>:<port>`` as ``Host``. Strict on everything else:

    * ``Host`` must be an IP literal. A DNS name can be rebound to the box
      by an attacker page, which then sends a matching ``Origin``/``Host``
      pair; an IP
      literal cannot be rebound. Named origins (``hal0.local``, the
      installer hostname, a reverse proxy's ``public_url``) are seeded into
      :func:`allowed_origins` by ``hal0.install.network`` and are stable
      across an IP change.
    * the ``Origin`` scheme must be ``http``/``https`` (opaque ``null`` and
      extension origins never match), and its authority must equal
      ``Host`` exactly, port included; a userinfo- or path-bearing value is
      not a browser Origin.
    * a ``wss`` upgrade that hal0 terminated itself only matches an
      ``https`` page. A ``ws`` upgrade may come from an ``https`` page,
      because a TLS-terminating upstream proxy (ADR-0012) forwards plain
      ``ws`` with ``Host`` preserved. The same leniency applies when
      uvicorn trusts ``X-Forwarded-Proto`` from more peers than loopback
      (``FORWARDED_ALLOW_IPS`` widened): ``request_scheme`` is then whatever
      a trusted peer claims; only the ``wss`` rule reads it, never the
      Origin/Host match itself.
    """
    # IPv6 zone ids (``%eth0``) are link-local plumbing no browser Origin
    # carries; reject them outright rather than reason about their encoding.
    if not origin or not host or "%" in host:
        return False
    try:
        parts = urlsplit(origin)
        hostname = urlsplit(f"//{host}").hostname or ""
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    if request_scheme == "wss" and parts.scheme != "https":
        return False
    if "@" in parts.netloc or parts.path or parts.query or parts.fragment:
        return False
    return parts.netloc.lower() == host.lower()


__all__ = [
    "DEFAULT_ALLOWED_ORIGINS",
    "SESSION_COOKIE_NAME",
    "SESSION_COOKIE_REMEMBER_TTL_SECONDS",
    "SESSION_COOKIE_TTL_SECONDS",
    "allowed_origins",
    "mint_session_cookie",
    "request_uses_tls",
    "session_cookie_expiry",
    "set_session_cookie",
    "verify_session_cookie",
]
