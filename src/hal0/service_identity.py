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

import os

# tier -> the env var / api.env key name that carries it.
_KEY_ENV: dict[str, str] = {"admin": "HAL0_ADMIN_KEY", "client": "HAL0_CLIENT_KEY"}


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


__all__ = ["keys_from_api_env", "service_auth_headers", "service_key"]
