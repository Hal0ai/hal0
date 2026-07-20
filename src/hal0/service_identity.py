"""Box service identity — the API keys hal0 processes present on internal calls.

When a hal0 process makes an authenticated call to its OWN API (the CLI probing
``/api/*``; the in-process brain steward self-calling ``/v1/chat/completions``
or the platform routes) and no caller bearer is available to forward, it must
still authenticate on an auth-enabled box. This module resolves the box's
service key the SAME way in both places: process env first, then
``/etc/hal0/api.env`` on disk — mirroring what the CLI has always done for its
own probes (halo150 O2) so the two surfaces can never drift.

Precedence within a tier is ``env → api.env``; ``prefer`` picks which tier is
tried first, with the other tier as fallback so a box provisioned with only one
of the two keys still authenticates. Returns nothing when no key is
discoverable — loopback dev-open boxes stay keyless and the API's development
posture handles them.

NEVER log or echo the resolved key values.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import tempfile

# tier -> the env var / api.env key name that carries it.
_KEY_ENV: dict[str, str] = {"admin": "HAL0_ADMIN_KEY", "client": "HAL0_CLIENT_KEY"}

# The mode a *rotated* api.env is written at. The installer seeds api.env at
# 0644 (world-readable — see install/perms.py's phase-4 FIXME), but the moment
# a rotation lands a live secret in it we tighten to 0640 (owner rw, group r,
# NO world). Never world-readable once it carries a rotated key.
_API_ENV_MODE = 0o640


def _tier_order(prefer: str) -> tuple[str, str]:
    """The (first, fallback) tier order for a ``prefer`` selector."""
    return ("admin", "client") if prefer == "admin" else ("client", "admin")


def keys_from_api_env() -> dict[str, str]:
    """Best-effort ``{HAL0_ADMIN_KEY, HAL0_CLIENT_KEY}`` read from api.env.

    The box's ``/etc/hal0/api.env`` is readable by hal0 processes (and the CLI
    running as root on the box) even when the keys aren't exported into the
    caller's environment. Any failure (missing file, unreadable) yields an
    empty mapping — auth then simply isn't attached, same as before this seam.
    """
    try:
        from hal0.config import paths as cfg_paths

        text = (cfg_paths.etc() / "api.env").read_text(encoding="utf-8")
    except Exception:
        return {}
    found: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k in _KEY_ENV.values() and v:
            found[k] = v.strip().strip('"').strip("'")
    return found


def service_key(prefer: str = "admin") -> str | None:
    """Resolve the box service key, preferring the ``prefer`` tier.

    Order: env[prefer] → env[other] → api.env[prefer] → api.env[other]. The
    fallback tier keeps a single-key box working; ``None`` when nothing is
    discoverable.
    """
    first, other = _tier_order(prefer)
    for tier in (first, other):
        value = os.environ.get(_KEY_ENV[tier], "").strip()
        if value:
            return value
    file_keys = keys_from_api_env()
    for tier in (first, other):
        value = file_keys.get(_KEY_ENV[tier])
        if value:
            return value
    return None


def service_auth_headers(prefer: str = "admin") -> dict[str, str]:
    """``{"Authorization": "Bearer <key>"}`` for the box identity, or ``{}``."""
    key = service_key(prefer=prefer)
    return {"Authorization": f"Bearer {key}"} if key else {}


# ---------------------------------------------------------------------------
# Key rotation (POST /api/auth/rotate) — mint + persist a fresh box key.


def generate_service_key() -> str:
    """Mint a fresh, strong box service key.

    Mirrors the gateway's ``_generate_api_server_key`` (hermes_provision):
    ``secrets.token_urlsafe(32)`` = 256 bits of CSPRNG entropy rendered as
    ~43 URL-safe chars. The output character set (``[A-Za-z0-9_-]``) carries
    no shell/systemd-EnvironmentFile-special characters, so it needs no
    quoting when written into api.env.
    """
    return secrets.token_urlsafe(32)


def key_fingerprint(key: str) -> str:
    """Short, non-reversible fingerprint of ``key`` (sha256 hex, first 8 chars).

    Lets a status surface prove *which* key is live without ever echoing the
    value — a one-way hash prefix, not the key. NEVER pass the key itself to a
    response or log; pass this instead.
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def _upsert_env_line(text: str, name: str, value: str) -> str:
    """Return ``text`` with ``name=value`` set.

    Replaces the FIRST existing ``name=...`` assignment in place, else appends
    a new line. Every other line (network vars, comments, HF_TOKEN notes) is
    preserved verbatim so a rotation only touches the one key line. Always ends
    with exactly one trailing newline.
    """
    new_line = f"{name}={value}"
    out: list[str] = []
    replaced = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if not replaced and stripped and not stripped.startswith("#"):
            key_part, sep, _ = stripped.partition("=")
            if sep and key_part.strip() == name:
                out.append(new_line)
                replaced = True
                continue
        out.append(raw)
    if not replaced:
        out.append(new_line)
    return "\n".join(out) + "\n"


def rotate_api_env_key(tier: str) -> dict[str, object]:
    """Rotate the ``tier`` (``admin``|``client``) box key in ``/etc/hal0/api.env``.

    Generates a fresh key, upserts it into api.env via a same-directory
    tmpfile + ``os.replace`` (atomic on POSIX; a mid-write crash leaves the
    prior file intact), preserving the file's existing owner/group and forcing
    a never-world-readable ``0640`` mode (the file now holds a live secret).

    It ALSO updates the current process' ``os.environ`` so the running auth
    layer — which reads ``os.environ`` per request (see ``hal0.api.auth``'s
    ``_admin_key``/``_client_key``) — honours the new key IMMEDIATELY, with no
    restart. The on-disk write makes the rotation durable across the next
    restart too. (A multi-worker deployment would only update the worker that
    served the request; hal0 ships single-process, so the rotation is live.)

    Returns status ONLY — ``{tier, key_len, fingerprint}``. The key value is
    NEVER returned, logged, or otherwise echoed by this module; the operator
    retrieves it out-of-band from ``/etc/hal0/api.env`` on the box.
    """
    env_name = _KEY_ENV.get(tier)
    if env_name is None:
        raise ValueError(f"unknown key tier: {tier!r}")

    from hal0.config import paths as cfg_paths

    target = cfg_paths.etc() / "api.env"
    target.parent.mkdir(parents=True, exist_ok=True)

    new_key = generate_service_key()

    try:
        existing = target.read_text(encoding="utf-8")
    except OSError:
        existing = ""

    content = _upsert_env_line(existing, env_name, new_key)

    # Preserve the existing owner/group (never silently change who owns the
    # config file); force 0640 because it now carries a live secret.
    prev_uid = prev_gid = -1
    with contextlib.suppress(OSError):
        st = target.stat()
        prev_uid, prev_gid = st.st_uid, st.st_gid

    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".api.env.", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        os.chmod(tmp_path, _API_ENV_MODE)
        if prev_uid != -1:
            with contextlib.suppress(OSError):
                os.chown(tmp_path, prev_uid, prev_gid)
        os.replace(tmp_path, str(target))
        tmp_path = None  # rename succeeded; don't clean up in finally
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)

    # Live effect: the auth hot path reads os.environ per request, so the new
    # key is honoured on the very next request without a restart.
    os.environ[env_name] = new_key

    return {
        "tier": tier,
        "key_len": len(new_key),
        "fingerprint": key_fingerprint(new_key),
    }


__all__ = [
    "generate_service_key",
    "key_fingerprint",
    "keys_from_api_env",
    "rotate_api_env_key",
    "service_auth_headers",
    "service_key",
]
