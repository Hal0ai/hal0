"""Operator-managed secrets store (mounted under ``/api/secrets``).

Backs the dashboard's Settings → Secrets panel (``useSecrets`` /
``useSecretSet`` / ``useSecretDelete`` hooks). Secrets are persisted to
``/etc/hal0/api.env`` — the same systemd ``EnvironmentFile=`` the
provider-credential writer targets — via the shared atomic, mode-0600
writer in :mod:`hal0.api._env_store`. The running process's
``os.environ`` is updated in lockstep so a freshly-set secret is
observable without a restart; the persisted line is the source of truth
across restarts.

Endpoints::

    GET    /api/secrets            — list secret NAMES (never values)
    POST   /api/secrets/{name}     — set/overwrite a secret  → 204
    PUT    /api/secrets/{name}     — set/overwrite a secret  → 204
    DELETE /api/secrets/{name}     — remove a secret         → 204

Both POST and PUT are accepted for the set path: the v3 dashboard's
``useSecretSet`` hook POSTs ``{value}`` while the documented contract is
PUT — accepting both keeps the route honest with the running UI and the
spec at once.

Security posture (auth removed, open on the trusted LAN):

  - Secret VALUES are NEVER returned by any endpoint, never logged, and
    never echoed in an error body.
  - Names are validated against ``^[A-Z][A-Z0-9_]{0,63}$`` so a caller
    can't smuggle a newline / shell metacharacter / lowercase env-var
    into ``api.env``.
  - Writes + deletes emit a structured audit row (name only) and a
    footer journal event so an operator can see *that* a secret changed
    without the value ever touching a log line.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from hal0.api._env_store import (
    delete_env_value,
    list_env_keys,
    upsert_env_value,
)
from hal0.config import paths
from hal0.errors import Hal0Error

_audit_log = structlog.get_logger("hal0.audit")
_log = structlog.get_logger(__name__)

router = APIRouter()

# Secret names: ALL_CAPS env-var grammar, must start with a letter, ≤64
# chars total. Tighter than the provider-credential rule (which allows a
# leading underscore) — operator-set secrets are plain env-vars.
_SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

# Fixed mask returned for every set secret. We do NOT keep the value, so
# this is a constant "it is set" indicator, never a partial reveal.
_MASK = "••••••••"

#: hal0's own configuration namespace inside ``api.env`` (#1450).
#:
#: ``api.env`` is two stores in one file: the operator's third-party
#: credentials (``HF_TOKEN``, ``OPENROUTER_API_KEY``, …) and hal0's service
#: configuration + auth keys, written there by ``installer/install.sh``,
#: ``hal0.toml`` derivation and ``service_identity.rotate_api_env_key``. This
#: route owns only the first. Everything under ``HAL0_`` is owned by those
#: writers and is refused here — deleting ``HAL0_ADMIN_KEY`` disarms every new
#: login (``routes/auth.py`` validates against it from the env), and deleting
#: ``HAL0_PORT`` / ``HAL0_UI_DIST`` breaks the service on next restart. Both
#: were one unconfirmed click, and one anonymous ``curl``, before this.
#:
#: A prefix rule rather than a name list on purpose: an enumerated list goes
#: stale the moment a new ``HAL0_*`` var is added to install.sh, and it is the
#: *unlisted* one that then becomes deletable. Nothing in ``SECRET_PRESETS``
#: has ever used this prefix, so no supported workflow is narrowed.
PROTECTED_SECRET_PREFIX = "HAL0_"


def is_protected_secret(name: str) -> bool:
    """True when ``name`` is hal0's own config/auth surface, not an operator
    secret — see :data:`PROTECTED_SECRET_PREFIX`."""
    return name.upper().startswith(PROTECTED_SECRET_PREFIX)


class SecretNameInvalid(Hal0Error):
    code = "secret.name_invalid"
    status = 400


class SecretProtected(Hal0Error):
    """A reserved ``HAL0_*`` key — visible, never mutable through this route."""

    code = "secret.protected"
    status = 403


class SecretValueInvalid(Hal0Error):
    code = "secret.value_invalid"
    status = 400


class SecretNotFound(Hal0Error):
    code = "secret.not_found"
    status = 404


class SecretWriteFailed(Hal0Error):
    code = "secret.write_failed"
    status = 400


class SecretBody(BaseModel):
    """Set-secret request body — a single opaque value."""

    value: str = Field(..., min_length=1)


def _api_env() -> Path:
    """Resolve the api.env path (HAL0_HOME-relative under tests)."""
    return paths.etc() / "api.env"


def _validate_name(name: str) -> str:
    """Return the validated secret name or raise :class:`SecretNameInvalid`."""
    candidate = name.strip()
    if not _SECRET_NAME_RE.match(candidate):
        raise SecretNameInvalid(
            "secret name must match ^[A-Z][A-Z0-9_]{0,63}$ "
            "(ALL_CAPS env-var, leading letter, no shell metacharacters)",
            details={"name": name},
        )
    return candidate


def _validate_mutable(name: str) -> str:
    """Validated name for a *write* — additionally refusing reserved keys.

    The gate is here rather than in the UI because the UI is not the only
    caller: this router is reachable by anyone who can reach the API, so a
    dialog is a courtesy and this is the control (#1450).
    """
    key = _validate_name(name)
    if is_protected_secret(key):
        raise SecretProtected(
            f"{key!r} is hal0 service configuration, not an operator secret — "
            f"names starting with {PROTECTED_SECRET_PREFIX!r} are managed by the "
            "installer, hal0.toml and `hal0 auth rotate`, and cannot be set or "
            "removed here",
            details={"name": key, "protected": True},
        )
    return key


def _updated_at(api_env: Path) -> str | None:
    """File-mtime (ISO 8601 UTC) of api.env, or None if it doesn't exist.

    An ``EnvironmentFile`` carries no per-key timestamps, so the file
    mtime is the honest best-effort "last changed" signal for every key.
    """
    try:
        mtime = api_env.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(mtime, tz=UTC).isoformat()


async def _emit(request: Request, type_: str, message: str, name: str) -> None:
    """Best-effort footer journal event — name only, never the value."""
    event_bus = getattr(request.app.state, "events", None)
    if event_bus is None:
        return
    try:
        await event_bus.emit(
            type_,
            "info",
            f"secret:{name}",
            message,
            data={"name": name},
        )
    except Exception:  # pragma: no cover — event bus is best-effort
        _log.debug("secret.event_emit_failed", type=type_, name=name)


@router.get("")
async def list_secrets() -> dict[str, Any]:
    """List secret NAMES (never values).

    Shape matches the dashboard's ``useSecrets`` hook —
    ``{"secrets": [{name, set, masked, updated_at}]}``. Every entry is
    ``set: true`` (a name only appears here because it's present in
    api.env); ``masked`` is a constant indicator, never a partial reveal.
    """
    api_env = _api_env()
    updated_at = _updated_at(api_env)
    entries = [
        {
            "name": name,
            "set": True,
            "masked": _MASK,
            "updated_at": updated_at,
            # #1450: reserved HAL0_* rows stay VISIBLE — an operator should be
            # able to see what the service is configured with — but carry the
            # flag the dashboard renders as a lock instead of a Remove button.
            # Hiding them would trade one lie for another; the mutation refusal
            # below is the actual control.
            "protected": is_protected_secret(name),
        }
        for name in list_env_keys(api_env)
    ]
    return {"secrets": entries}


def _validate_value(value: str, key: str) -> None:
    """Reject empty / non-printable values.

    A control character — most dangerously ``\\n`` / ``\\r`` — would let a
    value break out of its quoted line in api.env and inject a new
    ``KEY=value`` env-var (the file is an unauthenticated, LAN-writable
    systemd ``EnvironmentFile``). We reject any non-printable character via
    ``str.isprintable()``: this covers the C0 controls + ``0x7f`` *and* the
    higher-codepoint line separators (NEL ``U+0085``, LS ``U+2028``, PS
    ``U+2029``) that an ``ord(ch) < 0x20`` test misses but Python's
    ``str.splitlines()`` — which ``_env_store`` uses to re-read api.env —
    splits on, so they could still inject a phantom line on round-trip.
    Regular space is printable and allowed; every secret value we expect
    (tokens, keys, URLs) is printable.
    """
    if not value:
        raise SecretValueInvalid("secret value must be non-empty", details={"name": key})
    if not value.isprintable():
        raise SecretValueInvalid(
            "control characters not allowed in secret value",
            details={"name": key},
        )


async def _set_secret(name: str, body: SecretBody, request: Request) -> Response:
    """Shared set/overwrite implementation for POST + PUT."""
    key = _validate_mutable(name)
    _validate_value(body.value, key)

    api_env = _api_env()
    try:
        upsert_env_value(api_env, key, body.value)
    except OSError as exc:
        raise SecretWriteFailed(
            f"could not write secret to {api_env}: {exc}",
            details={"name": key, "error": str(exc)},
        ) from exc

    # Mirror the provider-credential path: update the live process env so
    # the new secret is observable without a restart; persisted api.env is
    # the source of truth across restarts.
    os.environ[key] = body.value

    _audit_log.info("secret.set", name=key, api_env_path=str(api_env))
    await _emit(request, "system.secret_updated", f"secret {key!r} set", key)
    return Response(status_code=204)


@router.post("/{name}", status_code=204)
async def set_secret_post(name: str, body: SecretBody, request: Request) -> Response:
    """Set/overwrite a secret (POST form — matches the v3 ``useSecretSet`` hook)."""
    return await _set_secret(name, body, request)


@router.put("/{name}", status_code=204)
async def set_secret_put(name: str, body: SecretBody, request: Request) -> Response:
    """Set/overwrite a secret (PUT form — documented contract)."""
    return await _set_secret(name, body, request)


@router.delete("/{name}", status_code=204)
async def delete_secret(name: str, request: Request) -> Response:
    """Remove a secret. Idempotent — returns 204 even if it wasn't set.

    Reserved ``HAL0_*`` names are refused with a 403 (#1450) — including
    when they are not present, so the answer does not leak which service
    keys the box happens to carry.
    """
    key = _validate_mutable(name)
    api_env = _api_env()
    try:
        removed = delete_env_value(api_env, key)
    except OSError as exc:
        raise SecretWriteFailed(
            f"could not delete secret from {api_env}: {exc}",
            details={"name": key, "error": str(exc)},
        ) from exc

    # Drop from the live process env regardless so a restart isn't needed
    # to stop honouring the secret.
    os.environ.pop(key, None)

    if removed:
        _audit_log.info("secret.deleted", name=key, api_env_path=str(api_env))
        await _emit(request, "system.secret_deleted", f"secret {key!r} deleted", key)
    return Response(status_code=204)
