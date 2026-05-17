"""Password hashing + signed session tokens for the FastAPI auth path.

Introduced by ADR-0001 Child A (refs #54, #55). This module is additive
to the existing bearer-token machinery in :mod:`hal0.auth.tokens`; the
two surfaces compose in :mod:`hal0.api.middleware.auth`, where the
existing ``require_token`` / ``require_writer`` dependencies grow a
cookie path alongside the Bearer path.

Two responsibilities live here:

  1. **Password hashing** — ``hash_password`` / ``verify_password`` use
     bcrypt with a cost factor of 12 (the value called out in the ADR).
     We deliberately use bcrypt here rather than the argon2id helpers in
     :mod:`hal0.auth.passwords` so we stay byte-compatible with the
     password hash Caddy would produce via ``caddy hash-password``
     (Caddy basic_auth uses bcrypt by default). That parity is what lets
     Child B drop Caddy's basicauth without a separate migration step:
     existing edge-auth installs already hold a bcrypt hash on disk.

  2. **Session tokens** — ``create_session_token`` mints a signed JWT
     (HS256) carrying ``sub`` (the username, always ``"owner"`` in v1),
     ``scope``, ``iat``, and ``exp``. ``verify_session_token`` returns
     the decoded claims dict on success and ``None`` on any validation
     failure (signature mismatch, expiry, malformed token). The signing
     key is derived from a per-install secret stored under
     ``HAL0_HOME/keyring`` (atomically generated on first use).

Why HS256 not RS/EC? The session cookie is consumed by the same
process that signs it — there's no third party verifying the token, so
the asymmetric machinery would buy us nothing and cost an extra file
on disk. The keyring file is the single source of truth and it never
leaves the host.

Why not roll session storage server-side? Stateless JWT cookies survive
process restarts without a session table, which matters for a home-LAN
install where the API restarts on every ``hal0 update``. The trade-off
is that we cannot forcibly invalidate a single outstanding cookie
short of rotating the keyring; for v1, ``POST /api/auth/logout``
clears the client-side cookie, and a keyring rotation is the documented
"sign everyone out" escape hatch. (Multi-user revocation lands in a
later milestone — see the ADR's "Out of scope" section.)
"""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path
from typing import Any, Final

import bcrypt
import jwt

from hal0.config import paths

# Bcrypt cost factor from the ADR. 12 is the OWASP 2024 baseline — a
# single hash on commodity x86 takes ~250ms, which is the right ceiling
# for an interactive login endpoint (slow enough to throttle brute
# force, fast enough that the user doesn't notice).
_BCRYPT_ROUNDS: Final[int] = 12

# Signing key file lives alongside the token store under HAL0_HOME's etc/
# root. 32 url-safe bytes → ~256 bits of entropy, which is what RFC 7518
# §3.2 wants for HS256.
_KEYRING_FILENAME: Final[str] = "keyring"
_KEYRING_BYTES: Final[int] = 32

# JWT algorithm choice; see module docstring for the rationale.
_JWT_ALG: Final[str] = "HS256"

# Default session lifetime, applied when the caller does not override it.
# 7 days is the standard "remember me" window for a home dashboard; shorter
# would force re-login through every server restart on weekends, longer
# starts to feel like a forgotten device.
DEFAULT_SESSION_TTL_SECONDS: Final[int] = 7 * 24 * 60 * 60


# ── Password hashing ─────────────────────────────────────────────────────────


def hash_password(plaintext: str) -> str:
    """Return a bcrypt hash of *plaintext* (cost 12).

    Raises:
        ValueError: if the password is empty. We do not enforce a
            minimum length here — that's the endpoint's job, because
            the policy reads differently depending on whether the call
            is a first-run claim or a rotation. Keeping the policy out
            of the primitive lets it be reused by future flows (e.g. a
            CLI ``hal0 auth password reset`` subcommand) without
            duplicating the check.
    """
    if not plaintext:
        raise ValueError("password must be non-empty")
    # bcrypt operates on bytes and silently truncates inputs at 72
    # bytes. We surface the cap as a hard error rather than silently
    # weaken long passphrases — a 73-character password that "works" but
    # only the first 72 chars matter is the exact footgun bcrypt's
    # reputation is built on.
    encoded = plaintext.encode("utf-8")
    if len(encoded) > 72:
        raise ValueError("password is too long (bcrypt accepts at most 72 bytes UTF-8)")
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(encoded, salt).decode("ascii")


def verify_password(plaintext: str, hash_str: str) -> bool:
    """Return True iff *plaintext* matches *hash_str*.

    Returns ``False`` (never raises) for any failure mode — empty
    inputs, malformed hash, wrong algorithm marker, library version
    skew. Callers turn the bool into a 401, never a 500.
    """
    if not plaintext or not hash_str:
        return False
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), hash_str.encode("ascii"))
    except (ValueError, TypeError):
        # Malformed hash string, wrong encoding, or unsupported scheme
        # marker. All look identical to the caller — invalid creds.
        return False


# ── Keyring (HS256 signing secret) ───────────────────────────────────────────


def _keyring_path() -> Path:
    """Return the on-disk path for the HS256 signing key.

    ``HAL0_HOME/etc/hal0/keyring`` when ``HAL0_HOME`` is set; the FHS
    equivalent ``/etc/hal0/keyring`` otherwise. Same root as
    ``tokens.toml`` so a single backup of ``etc/`` covers both the token
    store and the session-signing secret.
    """
    return paths.etc() / _KEYRING_FILENAME


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write *data* atomically to *path* (tmpfile + fsync + rename).

    Mirrors :func:`hal0.config.loader.write_toml_atomic` so a partial
    write during keyring bootstrap can never lock the user out — the
    rename is the moment the keyring becomes visible to subsequent
    processes, and POSIX makes that rename atomic when src/dst share a
    directory.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        # Tighten perms before the rename so the visible file never
        # exists with looser perms (a TOCTOU window otherwise). 0o600
        # because the keyring is per-host secret material — only the
        # service user should be able to read it.
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        # If anything above raised before os.replace, the tmp file is
        # an orphan; clean it up so we don't leak .keyring.<pid>.tmp
        # files across crashed installer runs.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _load_or_create_signing_key() -> str:
    """Return the HS256 signing key, generating it on first use.

    The generated key is 32 url-safe base64 chars (~256 bits of
    entropy). We return the string form rather than raw bytes because
    PyJWT accepts str directly and the file content is text anyway —
    keeping it as str lets a sysadmin ``cat`` the file to verify it
    exists without staring at a hexdump.
    """
    path = _keyring_path()
    try:
        existing = path.read_text(encoding="ascii").strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass
    except OSError:
        # If the file exists but we can't read it (permissions, FS
        # error), we'd rather mint a fresh key than crash the API on
        # startup. The downside is in-flight cookies stop validating;
        # the upside is the server keeps serving. Recovery is "fix the
        # file permissions and restart" — same as any other corrupt
        # on-disk state.
        pass

    fresh = secrets.token_urlsafe(_KEYRING_BYTES)
    _atomic_write_bytes(path, fresh.encode("ascii"))
    return fresh


# ── Session tokens ───────────────────────────────────────────────────────────


def create_session_token(
    user: str,
    scope: str,
    ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
) -> str:
    """Mint a signed session JWT for *user* with *scope* claims.

    The returned string is what we set as the ``hal0_session`` cookie
    value. ``ttl_seconds`` is configurable so the wizard can mint a
    short-lived bootstrap token during first-run setup if it ever needs
    to; today every caller takes the default.
    """
    if not user:
        raise ValueError("user must be non-empty")
    if not scope:
        raise ValueError("scope must be non-empty")
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": user,
        "scope": scope,
        "iat": now,
        "exp": now + int(ttl_seconds),
    }
    key = _load_or_create_signing_key()
    return jwt.encode(claims, key, algorithm=_JWT_ALG)


def verify_session_token(token: str) -> dict[str, Any] | None:
    """Validate a session token and return its claims, or None.

    None means *any* validation failure — bad signature, expired,
    malformed, wrong algorithm. The caller never needs to distinguish:
    they always respond with the same "your cookie is no good, log in
    again" path. Keeping the failure modes folded into a single None
    return avoids leaking timing/identity info to a probing client.
    """
    if not token:
        return None
    key = _load_or_create_signing_key()
    try:
        decoded = jwt.decode(token, key, algorithms=[_JWT_ALG])
    except jwt.PyJWTError:
        return None
    if not isinstance(decoded, dict):
        return None
    # Sanity-check the minimal claim set so a future schema change
    # doesn't accidentally grant access via a token that's missing the
    # fields the middleware reads.
    if "sub" not in decoded or "scope" not in decoded:
        return None
    return decoded


__all__ = [
    "DEFAULT_SESSION_TTL_SECONDS",
    "create_session_token",
    "hash_password",
    "verify_password",
    "verify_session_token",
]
