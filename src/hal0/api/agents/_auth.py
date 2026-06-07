"""Origin allowlist + HMAC session-cookie helpers for the agent chat proxy.

ADR-0012 removed Bearer auth and now ``X-hal0-Agent`` is the only
identity claim on hal0-api. DA-sec-ops review (MUST-FIX #2) raised that
exposing a long-running JSON-RPC bridge to the hermes runtime over an
unauthenticated ``0.0.0.0:8080`` is LAN-RCE: any host on the LAN sets
``X-hal0-Agent: hal0-dashboard`` and gets an interactive shell through
hermes's tool surface.

This module fixes that for the chat-proxy WebSocket routes by:

1. Origin allowlist on every WS upgrade. Configured via
   ``HAL0_ALLOWED_ORIGINS`` (comma-separated). Default covers the
   hal0.local hostname and dev origins (``localhost:5173`` for Vite,
   ``127.0.0.1:8080`` for the bundled SPA). The check is FREE; missing
   it leaves the rest of this scheme
   moot because any drive-by site could WebSocket into hal0-api from
   the user's own browser session.

2. An HMAC session cookie minted on first GET ``/agents/`` (or on the
   first ``/api/agents/{id}/session/create`` REST call) and verified on
   every WS upgrade after that. The cookie payload is JSON
   ``{"session_id": "<uuid>", "expires_at": <unix-ts>}``. The signature
   is ``HMAC-SHA256(<secret>, <base64url(payload)>)``. Secret comes from
   ``/var/lib/hal0/agents/secret.bin`` (chmod 0600, generated on first
   use). The cookie is set ``HttpOnly``, ``SameSite=Lax``.

The cookie is the only authorisation seam — there is no Bearer header
to spoof, and the secret never leaves the hal0 service user.

Embed-token-to-hermes auth (the ``Authorization: Bearer <runtime.json
embed_token>`` header on the upstream hop) is handled inside
``chat_proxy.py``; this module only owns the browser-facing seam.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Final

from fastapi import HTTPException, Request, Response
from starlette.websockets import WebSocket

# Default-deny set of browser origins permitted to upgrade to chat-proxy
# WebSockets. Operators can override at boot via ``HAL0_ALLOWED_ORIGINS``
# (comma-separated). The default covers:
#   - http://hal0.local         — mDNS / hosts-file alias
#   - http://localhost:5173     — Vite dev server (UI hot-reload)
#   - http://127.0.0.1:8080     — bundled SPA served from hal0-api
DEFAULT_ALLOWED_ORIGINS: Final[tuple[str, ...]] = (
    "http://hal0.local",
    "http://localhost:5173",
    "http://127.0.0.1:8080",
)

# Cookie name + lifetime. 8h chosen so a workday session never expires
# mid-conversation; renewal happens on the next dashboard load.
SESSION_COOKIE_NAME: Final[str] = "hal0_session"
SESSION_COOKIE_TTL_SECONDS: Final[int] = 8 * 60 * 60

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


def _b64url_encode(data: bytes) -> str:
    """URL-safe base64 without padding (matches JWT conventions)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """Reverse of :func:`_b64url_encode`. Restores padding before decode."""
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def allowed_origins() -> tuple[str, ...]:
    """Effective allowlist, including the env override when set.

    A misconfigured env (empty string) falls back to the default rather
    than denying everything — first-run dev convenience.
    """
    raw = os.environ.get("HAL0_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return DEFAULT_ALLOWED_ORIGINS
    parsed = tuple(o.strip() for o in raw.split(",") if o.strip())
    return parsed or DEFAULT_ALLOWED_ORIGINS


def mint_session_cookie(now: float | None = None) -> str:
    """Generate a fresh signed session cookie value.

    The payload is JSON-serialised ``{"session_id", "expires_at"}``; the
    output is ``<b64url(payload)>.<b64url(hmac)>``. Caller is responsible
    for setting it on a response via :func:`set_session_cookie`.
    """
    secret = _load_or_create_secret()
    ts = int(now if now is not None else time.time())
    payload = {
        "session_id": uuid.uuid4().hex,
        "expires_at": ts + SESSION_COOKIE_TTL_SECONDS,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(secret, payload_bytes, hashlib.sha256).digest()
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(sig)}"


def verify_session_cookie(value: str, now: float | None = None) -> bool:
    """Return ``True`` iff the cookie's signature is valid AND unexpired.

    Constant-time compare on the signature keeps a timing oracle off the
    table. An unparseable cookie returns ``False`` (no exception leaks).
    """
    if not value or "." not in value:
        return False
    try:
        payload_b64, sig_b64 = value.split(".", 1)
        payload_bytes = _b64url_decode(payload_b64)
        sig = _b64url_decode(sig_b64)
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return False

    secret = _load_or_create_secret()
    expected = hmac.new(secret, payload_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig):
        return False

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False

    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, int):
        return False
    ts = int(now if now is not None else time.time())
    return ts < expires_at


def set_session_cookie(response: Response, *, secure: bool | None = None) -> str:
    """Mint + attach a session cookie to ``response``. Returns its value.

    ``secure`` defaults to ``True`` on production-style origins and can
    be forced for tests via the kwarg.
    """
    value = mint_session_cookie()
    response.set_cookie(
        SESSION_COOKIE_NAME,
        value,
        max_age=SESSION_COOKIE_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=bool(secure) if secure is not None else False,
        path="/",
    )
    return value


def require_browser_auth(request: Request) -> None:
    """Verify a fresh session cookie on REST endpoints.

    Raises 403 if absent / signature-invalid / expired. Used by the
    session-management REST shim so the same cookie that gates the WS
    upgrade also gates ``session.create``.
    """
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie or not verify_session_cookie(cookie):
        raise HTTPException(status_code=403, detail="session_cookie_invalid")


def check_ws_origin_and_cookie(ws: WebSocket) -> bool:
    """Return ``True`` iff Origin is allowlisted AND cookie is valid.

    Used as the gate on every WS upgrade in :mod:`chat_proxy`. Returning
    ``False`` lets the caller send the policy-violation close code
    (4403) before any frame is ever exchanged.
    """
    origin = ws.headers.get("origin", "")
    if origin not in allowed_origins():
        return False
    cookie = ws.cookies.get(SESSION_COOKIE_NAME)
    return bool(cookie and verify_session_cookie(cookie))


__all__ = [
    "DEFAULT_ALLOWED_ORIGINS",
    "SESSION_COOKIE_NAME",
    "SESSION_COOKIE_TTL_SECONDS",
    "allowed_origins",
    "check_ws_origin_and_cookie",
    "mint_session_cookie",
    "require_browser_auth",
    "set_session_cookie",
    "verify_session_cookie",
]
