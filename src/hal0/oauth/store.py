"""OAuth token + client-secret storage — through the secrets store, never
in TOML, never logged.

Every value this module writes goes through
:mod:`hal0.api._env_store` — the same atomic, mode-0600 ``api.env`` writer
the operator secrets router (``/api/secrets``) and the provider-credential
writer (``/api/providers/{name}/credentials``) already use. Nothing here
ever logs a token or client-secret value; every log line downstream
carries the provider id only.

Env-var naming convention (both land in ``/etc/hal0/api.env``, both start
with ``HAL0_`` and are therefore refused by ``/api/secrets``'s own mutation
guard — they are managed exclusively through the oauth routes/CLI, not the
generic Secrets tab):

  ``HAL0_OAUTH_<PROVIDER>_TOKEN``           — JSON blob, see :class:`OAuthToken`
  ``HAL0_OAUTH_<PROVIDER>_CLIENT_SECRET``   — raw client-secret string

:func:`load_token` and :func:`load_client_secret` read the value back by
parsing ``api.env`` directly (mirrors
:func:`hal0.service_identity.keys_from_api_env`'s pattern) rather than
through :mod:`hal0.api._env_store`, which is deliberately write/list-only
so the *generic* secrets surface never echoes a value. This module's
read-back is internal-only: the token exchange/refresh flow and
:func:`hal0.agents.hermes_provision.refresh_driver_env` are its only
callers, never a route response body.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from hal0.api._env_store import delete_env_value, list_env_keys, upsert_env_value
from hal0.config import paths

_TOKEN_KEY_RE = re.compile(r"^HAL0_OAUTH_([A-Z0-9_]+)_TOKEN$")


def _normalize(provider_id: str) -> str:
    return provider_id.upper().replace("-", "_")


def _token_env_key(provider_id: str) -> str:
    return f"HAL0_OAUTH_{_normalize(provider_id)}_TOKEN"


def _client_secret_env_key(provider_id: str) -> str:
    return f"HAL0_OAUTH_{_normalize(provider_id)}_CLIENT_SECRET"


@dataclass(frozen=True)
class OAuthToken:
    """The token payload persisted for one connected provider."""

    access_token: str
    refresh_token: str | None
    expires_at: float | None  # unix epoch seconds; None = provider gave no expiry
    scope: str
    token_type: str = "Bearer"

    def to_json(self) -> str:
        return json.dumps(
            {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at,
                "scope": self.scope,
                "token_type": self.token_type,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> OAuthToken:
        data = json.loads(raw)
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=data.get("expires_at"),
            scope=data.get("scope") or "",
            token_type=data.get("token_type") or "Bearer",
        )

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= time.time()


def _api_env() -> Path:
    return paths.etc() / "api.env"


def _read_quoted_value(api_env: Path, key: str) -> str | None:
    """Parse one `KEY="value"` line out of an EnvironmentFile-style file.

    Mirrors `hal0.service_identity.keys_from_api_env`'s direct-parse
    pattern. Returns None on any read failure or if the key isn't set.
    """
    try:
        text = api_env.read_text(encoding="utf-8")
    except OSError:
        return None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() != key:
            continue
        value = v.strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        return value
    return None


# ── tokens ────────────────────────────────────────────────────────────────


def save_token(provider_id: str, token: OAuthToken, *, api_env: Path | None = None) -> None:
    """Persist ``token`` for ``provider_id``. Never logs the value."""
    upsert_env_value(api_env or _api_env(), _token_env_key(provider_id), token.to_json())


def delete_token(provider_id: str, *, api_env: Path | None = None) -> bool:
    """Remove the stored token for ``provider_id``. Returns whether one existed."""
    return delete_env_value(api_env or _api_env(), _token_env_key(provider_id))


def is_connected(provider_id: str, *, api_env: Path | None = None) -> bool:
    """True if a token is stored for ``provider_id`` — the value is never read."""
    return _token_env_key(provider_id) in list_env_keys(api_env or _api_env())


def load_token(provider_id: str, *, api_env: Path | None = None) -> OAuthToken | None:
    """Read the stored token back. Internal use only — never a route response."""
    target = api_env or _api_env()
    raw = _read_quoted_value(target, _token_env_key(provider_id))
    if raw is None:
        return None
    try:
        return OAuthToken.from_json(raw)
    except (json.JSONDecodeError, KeyError):
        return None


def connected_provider_ids(*, api_env: Path | None = None) -> list[str]:
    """Provider ids with a token currently stored, derived from key names."""
    out = []
    for key in list_env_keys(api_env or _api_env()):
        m = _TOKEN_KEY_RE.match(key)
        if m:
            out.append(m.group(1).lower().replace("_", "-"))
    return sorted(out)


def driver_env_lines(*, api_env: Path | None = None) -> list[str]:
    """``KEY=value`` lines for every connected provider's token.

    Consumed verbatim by :func:`hal0.agents.hermes_provision.refresh_driver_env`
    so the Hermes agent's own env carries the current OAuth tokens — the
    ADR-0002-sanctioned env-from-secrets-store delivery path. Re-emits the
    exact JSON blob this module already stores; no re-encoding, one owner
    of the token shape.
    """
    target = api_env or _api_env()
    lines = []
    for provider_id in connected_provider_ids(api_env=target):
        token = load_token(provider_id, api_env=target)
        if token is not None:
            lines.append(f"{_token_env_key(provider_id)}={token.to_json()}")
    return lines


# ── client secrets ───────────────────────────────────────────────────────


def save_client_secret(provider_id: str, value: str, *, api_env: Path | None = None) -> None:
    upsert_env_value(api_env or _api_env(), _client_secret_env_key(provider_id), value)


def has_client_secret(provider_id: str, *, api_env: Path | None = None) -> bool:
    return _client_secret_env_key(provider_id) in list_env_keys(api_env or _api_env())


def load_client_secret(provider_id: str, *, api_env: Path | None = None) -> str | None:
    """Read the stored client secret back. Internal use only (token exchange)."""
    return _read_quoted_value(api_env or _api_env(), _client_secret_env_key(provider_id))
