"""External upstream LLM providers (mounted under /api).

Endpoints:
  GET    /api/upstreams                  — list registered routing targets
  GET    /api/upstreams/{name}           — single upstream
  POST   /api/upstreams/{name}/test      — probe reachability + auth
  GET    /api/providers/catalog          — static integration catalog
  GET    /api/providers                  — configured providers (alias of upstreams)

Write paths (create/update/delete) are intentionally deferred — providers are
authored by editing /etc/hal0/upstreams.toml and reloading, which the
``hal0 config reload`` CLI surfaces. The Phase 1 design (PLAN §6) treats the
TOML files as the source of truth; a fully reactive editor lands later.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from hal0.api._env_store import upsert_env_value
from hal0.api._redact import redact_config
from hal0.api.middleware.error_codes import Hal0Error
from hal0.config import paths
from hal0.upstreams.integrations import get_catalog
from hal0.upstreams.registry import UpstreamNotFound

_audit_log = structlog.get_logger("hal0.audit")
_log = structlog.get_logger(__name__)

# Allowed env-var names — uppercase ASCII letters, digits, underscores; must
# start with a letter or underscore. Matches POSIX shell + systemd
# EnvironmentFile= rules and prevents callers from injecting newlines /
# shell metacharacters via the ``key`` body field.
_ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")

# See slots.py for the writer-gate rationale.

router = APIRouter()


class UpstreamNotFoundHTTP(Hal0Error):
    code = "upstream.not_found"
    status = 404


class ProviderCredentialError(Hal0Error):
    code = "provider.credential_write_failed"
    status = 400


class ProviderCredentialBody(BaseModel):
    """POST /api/providers/{name}/credentials body schema.

    Single secret pair: ``key`` is the env-var name the upstream's
    ``auth_value_env`` points at; ``value`` is the secret itself.
    Validated against :data:`_ENV_KEY_RE` so a caller can't sneak a
    newline or shell metacharacter into the api.env line.
    """

    key: str = Field(..., min_length=1, max_length=128)
    value: str = Field(..., min_length=1)


def _serialize_upstream(u: Any, *, last_models: list[str] | None = None) -> dict[str, Any]:
    """Project an Upstream dataclass into the dashboard-friendly dict.

    Mirrors /api/slots' shape — `name`, `kind`, `url`, plus a sanitized
    auth descriptor that never leaks credential values. The output is
    run through :func:`redact_config` as a defense-in-depth pass (#553):
    the schema only stores the env-var NAME (``auth_value_env``), never
    a secret, so a redaction trigger on the well-known shape is a no-op
    today — but if a future field lands here whose name matches a
    sensitive pattern, the walk catches it without a round of edits.
    """
    out = {
        "name": u.name,
        "kind": u.kind,
        "url": u.url,
        "auth_style": u.auth_style,
        "auth_value_env": u.auth_value_env,  # env-var *name*, not value
        "auth_configured": bool(u.auth_value_env),
        "timeout_seconds": u.timeout_seconds,
        "slot_name": u.slot_name,
        "warmup_strategy": u.warmup_strategy,
        "advertise_models": u.advertise_models,
        "models": last_models or [],
    }
    return redact_config(out)


@router.get("/upstreams")
async def list_upstreams(request: Request) -> list[dict[str, Any]]:
    """Return all configured upstreams (slots + remote providers).

    Pulled from ``app.state.upstreams`` — the same registry the dispatcher
    routes through. Each entry includes the cached model list when one has
    been fetched (typically primed on first /api/health hit).
    """
    upstreams = request.app.state.upstreams
    model_cache: dict[str, list[str]] = getattr(request.app.state, "upstream_models", {})
    return [_serialize_upstream(u, last_models=model_cache.get(u.name)) for u in upstreams.list()]


@router.get("/upstreams/{name}")
async def get_upstream(name: str, request: Request) -> dict[str, Any]:
    """Return a single upstream by name (404 if not registered)."""
    upstreams = request.app.state.upstreams
    u = upstreams.get(name)
    if u is None:
        raise UpstreamNotFoundHTTP(f"upstream {name!r} not found", {"name": name})
    model_cache: dict[str, list[str]] = getattr(request.app.state, "upstream_models", {})
    return _serialize_upstream(u, last_models=model_cache.get(name))


@router.post("/upstreams/{name}/test")
async def test_upstream(name: str, request: Request) -> dict[str, Any]:
    """Probe ``/v1/models`` on ``name`` and return a reachability report.

    Shape: ``{ok, status?, latency_ms, models_count?, error?}``. Used by the
    Slots/Upstreams settings view to give a "test connection" button.
    """
    upstreams = request.app.state.upstreams
    try:
        result = await upstreams.test(name)
    except UpstreamNotFound as exc:
        raise UpstreamNotFoundHTTP(str(exc), {"name": name}) from exc
    # Footer event when the operator-driven test discovers an unreachable
    # upstream. The slot state machine emits its own slot.state events on
    # warmup failures; this covers the remote-provider half of the world
    # where there is no slot but the dashboard still wants to surface
    # outages.
    event_bus = getattr(request.app.state, "events", None)
    if event_bus is not None and not result.get("ok"):
        await event_bus.emit(
            "system.upstream_unhealthy",
            "warn",
            f"upstream:{name}",
            f"upstream {name!r} unreachable",
            data={
                "name": name,
                "status": result.get("status"),
                "error": result.get("error"),
                "latency_ms": result.get("latency_ms"),
            },
        )
    return result


@router.get("/providers/catalog")
async def providers_catalog() -> dict[str, dict[str, Any]]:
    """Return the static integration catalog (built-in upstream templates).

    Used by the "Add upstream" form to populate the dropdown of known
    providers (Anthropic, OpenAI, OpenRouter, hal0 self, custom, …).
    """
    return get_catalog()


@router.get("/providers")
async def list_providers(request: Request) -> list[dict[str, Any]]:
    """Return remote (kind=='remote') upstreams only.

    The dashboard's Providers tab is essentially "show me the third-party
    catalogs I've wired up" — slot upstreams are managed under /api/slots.
    """
    upstreams = request.app.state.upstreams
    model_cache: dict[str, list[str]] = getattr(request.app.state, "upstream_models", {})
    return [
        _serialize_upstream(u, last_models=model_cache.get(u.name))
        for u in upstreams.list()
        if u.kind != "slot"
    ]


def _write_credential_to_api_env(api_env: Path, key: str, value: str) -> None:
    """Upsert ``key=<quoted-value>`` in ``api_env`` atomically.

    Thin wrapper over :func:`hal0.api._env_store.upsert_env_value` — the
    atomic tmp-file + ``os.replace`` + mode-0600 writer now lives in the
    shared env store so the ``/api/secrets`` router writes to the same
    file with identical posture. Read/write failures surface as
    :class:`ProviderCredentialError` so the route's envelope is unchanged.
    """
    try:
        upsert_env_value(api_env, key, value)
    except OSError as exc:
        raise ProviderCredentialError(
            f"could not write {api_env}: {exc}",
            details={"path": str(api_env), "error": str(exc)},
        ) from exc


@router.post("/providers/{name}/credentials")
async def write_provider_credential(
    name: str,
    body: ProviderCredentialBody,
    request: Request,
) -> dict[str, Any]:
    """Persist one provider credential to ``/etc/hal0/api.env`` (gated).

    Body: ``{key: <ENV_VAR_NAME>, value: <secret>}``. The upstream named
    ``{name}`` must already exist in the registry; we use it to validate
    that ``key`` matches its declared ``auth_value_env`` so a caller
    can't write arbitrary env-vars through this route. Returns
    ``{ok: true, key, name}`` — the secret value is NEVER echoed back.

    The Phase 8 MCP admin server's ``provider_credential_write`` tool
    routes here; that path is gated on owner approval (see
    ``hal0.mcp.admin.GATED_TOOLS``). Auth was removed in ADR-0012;
    direct REST writes are open on the local network.

    Process restart is the caller's responsibility — the registry
    re-reads env on next load (see ``UpstreamRegistry`` __init__).
    Surfacing that as a hint in the response so the dashboard can render
    "restart hal0-api to pick up the change" without an extra round trip.
    """
    upstreams = request.app.state.upstreams
    upstream = upstreams.get(name)
    if upstream is None:
        raise UpstreamNotFoundHTTP(f"upstream {name!r} not found", {"name": name})

    key = body.key.strip()
    if not _ENV_KEY_RE.match(key):
        raise ProviderCredentialError(
            "key must be an ALL_CAPS env-var name "
            "(letters, digits, underscores; no leading digit, no shell metacharacters)",
            details={"key": key},
        )

    # Bind the credential to the upstream's declared env-var. Catches
    # the "typo'd PROVIDER_KEY but the upstream actually reads
    # PROVIDER_API_KEY" footgun without forcing the caller to look up
    # auth_value_env separately.
    expected_env = upstream.auth_value_env or ""
    if expected_env and key != expected_env:
        raise ProviderCredentialError(
            f"key {key!r} does not match upstream {name!r}'s declared "
            f"auth_value_env={expected_env!r}; refusing to write a "
            "credential the upstream won't read",
            details={"name": name, "key": key, "expected": expected_env},
        )

    api_env = paths.etc() / "api.env"
    try:
        _write_credential_to_api_env(api_env, key, body.value)
    except ProviderCredentialError:
        raise
    except OSError as exc:
        raise ProviderCredentialError(
            f"failed to write {api_env}: {exc}",
            details={"path": str(api_env), "error": str(exc)},
        ) from exc

    # Update the in-process environment so the running registry can
    # observe the new value without a restart — the registry reads
    # ``os.environ[upstream.auth_value_env]`` per call (registry.py:293).
    # The persisted api.env line is the source of truth across restarts.
    os.environ[key] = body.value

    identity = getattr(request.state, "identity", None)
    audit_identity = getattr(identity, "identity", None) if identity is not None else None
    # Structured audit row — never log the value, only the env-var name +
    # the upstream it landed against + who wrote it.
    _audit_log.info(
        "provider.credential_written",
        upstream=name,
        key=key,
        api_env_path=str(api_env),
        identity=audit_identity,
    )

    return {
        "ok": True,
        "name": name,
        "key": key,
        "value": "***REDACTED***",
        "hint": "restart hal0-api or reload upstreams to pick up the change",
    }
