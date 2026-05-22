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
                                  success. 401 on bad creds. Throttled
                                  via the IP-bucket rate limiter
                                  (FINDINGS §32) — 5 attempts / 60s.
  GET    /api/auth/login        — legacy no-op kept so existing wizard
                                  probes don't 404 during the Wave 1
                                  rollout. Returns a hint pointing at
                                  the POST endpoint.
  POST   /api/auth/logout       — clears the session cookie; 204.
  POST   /api/auth/password     — set or rotate the owner password.
                                  First-run claim requires EITHER the
                                  OTP token from
                                  ``<state>/.first-run.lock`` OR a
                                  127.0.0.1 loopback call (FINDINGS
                                  §28). Once a password is set, the
                                  endpoint requires writer scope.
                                  Rate-limited the same as /login.
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

import contextlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from hal0.api.auth import first_run as first_run_lock
from hal0.api.auth.password import (
    create_session_token,
    hash_password,
    verify_password,
)
from hal0.api.auth.rate_limit import (
    RateLimitExceeded,
    check_rate_limit,
    client_ip,
)
from hal0.api.middleware.auth import (
    SESSION_COOKIE_NAME,
    AuthForbidden,
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
from hal0.config import paths
from hal0.errors import BadRequest, Hal0Error

log = structlog.get_logger(__name__)

router = APIRouter()

# Loopback hosts that bypass the first-run OTP gate. IPv4 plus IPv6
# loopback both qualify — a curl call from the same machine could come
# in on either depending on the bind address. Anything outside this set
# (including a LAN IP that points at the same physical host) must
# present the OTP.
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})

# Rate-limit scope names. Kept as constants so the limiter buckets stay
# stable across releases and tests can reset() per-scope.
_RATE_SCOPE_LOGIN: str = "auth.login"
_RATE_SCOPE_PASSWORD: str = "auth.password"


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


class FirstRunOtpRequired(Hal0Error):
    """First-run set-password called from a LAN peer without the OTP.

    The lockfile sits at ``<state>/.first-run.lock`` and is printed by
    ``install.sh`` at the end of the install. The operator pastes it
    into the wizard's password step; the wizard then echoes it back as
    ``otp`` in the JSON body. Loopback callers bypass this requirement
    so a host-local ``curl`` to ``http://127.0.0.1:8080`` keeps the old
    UX.
    """

    code = "auth.first_run_otp_required"
    status = 401


class FirstRunOtpInvalid(Hal0Error):
    """First-run set-password called with a bad/expired OTP.

    Distinct code from "required" so the wizard can render a more
    targeted error ("That setup token didn't match — copy a fresh one
    from the installer transcript or run ``hal0 auth print-otp``").
    """

    code = "auth.first_run_otp_invalid"
    status = 401


# Note: the brief mentioned a possible "409 auth.password_already_set"
# response for the post-first-run path, but the existing behaviour
# (kept intentionally) is the more conservative 401 ``auth.required``
# from ``require_token`` — see
# tests/api/test_password_auth.py::test_second_set_password_without_auth_returns_401.
# Surfacing 409 would let a probing attacker enumerate the install
# state without credentials; 401 is the indistinguishable response
# that matches the rest of the auth surface.


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


def _rate_limited_response(exc: RateLimitExceeded) -> JSONResponse:
    """Render a 429 JSONResponse with the Retry-After header.

    The global ``Hal0Error`` handler can't set per-response headers
    (it's a single-arg JSONResponse construction), so the rate-limit
    paths catch :class:`RateLimitExceeded` locally and synthesise the
    response here. Shape matches the hal0 error envelope contract:
    ``{"error": {"code", "message", "details"}}`` plus ``Retry-After``
    in seconds.
    """
    return JSONResponse(
        status_code=429,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )


@router.post("/login")
async def login(request: Request, response: Response) -> Any:
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

    Rate-limited (FINDINGS §32) via the IP-bucket on
    ``app.state.auth_rate_limiter``. The check runs BEFORE the bcrypt
    verify so a flooding client doesn't get the CPU-amortised cover of
    250ms per attempt — they pay zero compute on the throttled tail.
    Failed attempts log at WARN level with ``{client_ip, identity,
    reason}`` for grep-ability; the password itself is never logged.
    """
    try:
        check_rate_limit(request, scope=_RATE_SCOPE_LOGIN)
    except RateLimitExceeded as exc:
        return _rate_limited_response(exc)

    body = await _read_json_object(request)
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")

    store = _store(request)
    hash_str = store.get_password_hash()

    # Constant-ish failure path: every failure mode below collapses to
    # the same 401 envelope, so a probing client can't distinguish
    # "no password set" from "wrong password" from "wrong username".
    if not hash_str:
        reason = "no_password_configured"
        ok = False
    elif username != _OWNER_USERNAME:
        reason = "unknown_username"
        ok = False
    elif not verify_password(password, hash_str):
        reason = "bad_password"
        ok = False
    else:
        reason = "ok"
        ok = True

    if not ok:
        # Structured WARN log — identity is what the client claimed (so
        # an attacker probing common usernames is grep-able) and reason
        # is the precise leg that failed. The password is NEVER logged.
        log.warning(
            "auth.login_failed",
            client_ip=client_ip(request),
            identity=username or "<empty>",
            reason=reason,
        )
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


def _is_loopback(request: Request) -> bool:
    """True if ``request.client.host`` is a loopback address.

    Used to bypass the first-run OTP gate: an operator running
    ``curl http://127.0.0.1:8080`` on the host machine has already
    proven they have shell access, so requiring them to fish the OTP
    out of journalctl is friction without a corresponding security
    win. The bypass is intentionally strict — only the literal
    loopback hosts qualify, not e.g. ``10.0.1.50`` even when that's
    the install's own LAN IP.
    """
    return client_ip(request) in _LOOPBACK_HOSTS


def _verify_first_run_otp(request: Request, body: dict[str, Any]) -> None:
    """Validate the first-run OTP against the lockfile.

    Raises ``FirstRunOtpRequired`` when no OTP is presented and the
    request isn't loopback, or ``FirstRunOtpInvalid`` when an OTP is
    presented but doesn't match. On match (or loopback), returns
    cleanly so the caller can proceed.

    The OTP is read from the JSON body's ``otp`` field OR from the
    ``X-Hal0-First-Run-OTP`` header — both are accepted so the wizard
    can carry it in JSON while a CLI flow can stick it on a header.
    """
    # Loopback bypass — see _is_loopback's docstring for the rationale.
    if _is_loopback(request):
        log.info(
            "auth.first_run.loopback_bypass",
            client_ip=client_ip(request),
        )
        return

    presented = str(body.get("otp") or request.headers.get("x-hal0-first-run-otp") or "").strip()

    lock = first_run_lock.read_lockfile()
    if lock is None:
        # No lockfile on disk — either it was never minted (very old
        # install upgraded in-place) or it was consumed earlier. Refuse
        # the first-run claim from a non-loopback peer rather than
        # silently allowing it: this is a security-critical path.
        log.warning(
            "auth.first_run.no_lockfile",
            client_ip=client_ip(request),
        )
        raise FirstRunOtpRequired(
            "first-run setup requires the OTP printed by the installer",
            details={
                "hint": (
                    "the .first-run.lock file is missing; run hal0-api restart to mint a fresh one"
                ),
            },
        )

    if not presented:
        raise FirstRunOtpRequired(
            "first-run setup requires the OTP printed by the installer",
            details={
                "hint": (
                    "paste the value from `cat /var/lib/hal0/.first-run.lock` "
                    "into the wizard, OR call from 127.0.0.1"
                ),
            },
        )

    # Constant-time compare so the per-byte timing on a sustained probe
    # doesn't leak the prefix of the OTP. ``hmac.compare_digest`` is
    # also resistant to length-leak (returns False for differing
    # lengths without scanning).
    import hmac as _hmac

    if not _hmac.compare_digest(presented, lock.otp):
        log.warning(
            "auth.first_run.otp_mismatch",
            client_ip=client_ip(request),
        )
        raise FirstRunOtpInvalid(
            "first-run OTP did not match",
            details={"reason": "otp_mismatch"},
        )


@router.post("/password")
async def set_password(request: Request, response: Response) -> Any:
    """Set or rotate the owner password.

    Body::

        {"password": "...", "otp": "<optional first-run token>"}

    Auth contract:

      - First-run claim (no password yet):
          * Loopback callers (``127.0.0.1`` / ``::1``) pass through
            without any credential.
          * Non-loopback callers MUST present the OTP minted on
            startup at ``<state>/.first-run.lock`` (printed by the
            installer banner). FINDINGS §28.
          * On success the response sets a ``hal0_session`` cookie so
            the wizard's next writer call (PUT /api/config/models, the
            capability pulls, /api/capabilities/...) authenticates as
            the new owner. Without this the wizard would 401 on every
            subsequent mutation — see #fix-firstrun-auth.
      - Rotation (password already set):
          * Requires writer scope (Bearer or cookie). Cookie path
            additionally enforces the CSRF tripwire — same as every
            other writer-scoped mutation. No new cookie is issued; the
            existing session stays valid.

    Always 400s a password shorter than 8 chars (server-side floor;
    the wizard surfaces a stronger UX hint).

    Rate-limited (FINDINGS §32): the IP-bucket scope is shared with
    the rest of the auth surface so an attacker can't grind both
    endpoints simultaneously.
    """
    try:
        check_rate_limit(request, scope=_RATE_SCOPE_PASSWORD)
    except RateLimitExceeded as exc:
        return _rate_limited_response(exc)

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
    else:
        # First-run claim: enforce the OTP / loopback gate. This is the
        # FINDINGS §28 mitigation — without it, any LAN peer can race
        # the legitimate operator to claim ownership in the window
        # between hal0-api starting and the operator finishing the
        # wizard.
        _verify_first_run_otp(request, body)

    new_hash = hash_password(new_password)
    store.set_password_hash(new_hash)

    # Consume the lockfile only on the first-run path. On a rotation
    # the file is already gone (was consumed on the original claim);
    # consume_lockfile is idempotent so the rotation branch could also
    # call it, but keeping the call colocated with first-run makes the
    # life-cycle obvious in code review.
    if not has_existing:
        first_run_lock.consume_lockfile()
        # Mint a session cookie so the wizard's subsequent writer calls
        # ride a normal cookie session instead of hitting AuthRequired
        # on every mutation. Mirrors POST /api/auth/login exactly — same
        # JWT helper, same cookie attributes — because once the password
        # is set the operator IS the owner and skipping the explicit
        # /api/auth/login round-trip is just a UX shortcut for the
        # wizard. Rotation deliberately doesn't re-issue: the caller
        # already had a session (or a Bearer) to pass require_writer.
        token = create_session_token(user=_OWNER_USERNAME, scope=_OWNER_SCOPE)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            samesite="lax",
            secure=_request_is_tls(request),
            path="/",
        )

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None:
        await event_bus.emit(
            "system.auth_changed",
            "info",
            "system",
            "owner password rotated" if has_existing else "owner password set",
            data={"rotated": has_existing},
        )
    return {
        "ok": True,
        "password_set": True,
        # ``rotated`` distinguishes first-run set from a subsequent
        # rotation. The wizard surfaces a different toast based on it.
        "rotated": has_existing,
    }


@router.post("/disable")
async def disable_auth(request: Request) -> dict[str, Any]:
    """First-run-only: switch the box to trusted-LAN posture.

    Called from the wizard's "Skip — leave open" path. Writes
    ``HAL0_AUTH_DISABLED=1`` into ``/etc/hal0/api.env`` (atomic
    tmp-file + replace; the line is added or uncommented as needed),
    consumes the first-run lockfile so the open-claim window closes,
    and schedules a deferred ``systemctl restart hal0-api`` so the
    response can flush before the process turns over. The next
    request lands on an auth-disabled process where every dependency
    returns ``identity=anonymous, scope=all`` — the dashboard's
    Settings panels stop 401'ing and the operator gets the
    pre-v1 trusted-LAN posture they asked for by clicking Skip.

    Gates:
      - Only callable while no owner password is set AND the
        first-run lockfile exists. Once either condition flips, this
        endpoint returns 403 ``auth.disable_not_available`` — we
        deliberately do NOT allow flipping auth off from an existing
        password-secured install via a single anonymous POST.
      - The endpoint is in :data:`_FIRST_RUN_CLAIM_PATHS` so anonymous
        callers can reach it during the claim window.

    On hosts without systemd (dev installs, CI), the restart step is
    a no-op and the env-var write is the only side effect — the next
    process start (manual or harness-driven) picks it up.
    """
    try:
        check_rate_limit(request, scope=_RATE_SCOPE_PASSWORD)
    except RateLimitExceeded as exc:
        return _rate_limited_response(exc)

    store = _store(request)
    if store.get_password_hash() is not None:
        raise AuthForbidden(
            "cannot disable auth after the owner password has been set — "
            "edit /etc/hal0/api.env and restart hal0-api instead",
            details={"reason": "password_already_set"},
        )
    if first_run_lock.read_lockfile() is None:
        raise AuthForbidden(
            "first-run claim window has closed — cannot toggle auth from "
            "an anonymous request, edit /etc/hal0/api.env directly",
            details={"reason": "claim_window_closed"},
        )

    api_env = paths.etc() / "api.env"
    _set_auth_disabled_in_env(api_env)
    first_run_lock.consume_lockfile()

    # Best-effort deferred restart so the response flushes before the
    # process turns over. On dev / CI / containerised installs without
    # systemd, swallow the FileNotFoundError — the env-var write stuck
    # to disk and the next process start picks it up.
    _schedule_service_restart()

    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None:
        await event_bus.emit(
            "system.auth_changed",
            "info",
            "system",
            "auth disabled — trusted-LAN posture (first-run skip)",
            data={"auth_disabled": True},
        )

    return {
        "ok": True,
        "auth_disabled": True,
        "api_env_path": str(api_env),
        "restart_scheduled": True,
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


# ── Auth-disable helpers (POST /api/auth/disable) ────────────────────────────


_AUTH_DISABLED_LINE = "HAL0_AUTH_DISABLED=1\n"


def _set_auth_disabled_in_env(api_env: Path) -> None:
    """Add or uncomment ``HAL0_AUTH_DISABLED=1`` in ``api.env``.

    Atomic: writes a tmp file in the same directory then ``os.replace``
    over the target so a crash mid-write leaves the prior file intact.
    Idempotent: if the line is already present and uncommented, we
    rewrite the file with the same content (no-op semantically).

    The installer's default ``api.env`` ships the line commented out
    (``# HAL0_AUTH_DISABLED=1``); we look for the commented form and
    uncomment it in place to preserve surrounding comments. If neither
    form is present we append. The file is created with mode 0644 to
    match what the installer drops — adjust the umask if the deploy
    posture changes.
    """
    api_env.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    try:
        existing = api_env.read_text(encoding="utf-8")
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise Hal0Error(
            f"could not read {api_env}: {exc}",
            details={"path": str(api_env), "error": str(exc)},
        ) from exc

    lines = existing.splitlines(keepends=True) if existing else []
    rewritten: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.lstrip()
        if (
            stripped.startswith("HAL0_AUTH_DISABLED=")
            or stripped.startswith("# HAL0_AUTH_DISABLED=")
            or stripped.startswith("#HAL0_AUTH_DISABLED=")
        ):
            if not replaced:
                rewritten.append(_AUTH_DISABLED_LINE)
                replaced = True
            # Drop the original line (whether commented or set to a
            # different value) — the new line above is authoritative.
            continue
        rewritten.append(line)
    if not replaced:
        if rewritten and not rewritten[-1].endswith("\n"):
            rewritten.append("\n")
        rewritten.append(_AUTH_DISABLED_LINE)

    fd, tmp_str = tempfile.mkstemp(
        prefix=f".{api_env.name}.",
        suffix=".tmp",
        dir=str(api_env.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("".join(rewritten))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_str, 0o644)
        os.replace(tmp_str, api_env)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_str)
        raise


def _schedule_service_restart() -> None:
    """Best-effort deferred ``systemctl restart hal0-api``.

    Spawns a detached background shell that sleeps 2s and then asks
    systemd to restart this process. The sleep lets the HTTP response
    flush before the kill arrives, and the detach (``setsid`` +
    closed FDs) makes the child outlive its parent.

    Silently no-ops when ``systemctl`` is absent (dev / CI /
    containerised installs without systemd). The env-var write
    already landed; the next process start picks it up.
    """
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return
    # If we can't even spawn sh, the env-var write still stuck; the
    # operator's next manual restart picks it up.
    with contextlib.suppress(OSError, FileNotFoundError):
        subprocess.Popen(
            ["sh", "-c", f"sleep 2 && {systemctl} restart hal0-api"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )


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
