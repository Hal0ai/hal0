"""External upstream LLM providers (mounted under /api).

Endpoints:
  GET    /api/upstreams                  — list registered routing targets
  POST   /api/upstreams                  — create a remote upstream (reactive)
  GET    /api/upstreams/{name}           — single upstream
  PATCH  /api/upstreams/{name}           — mutate settings (visibility + structural)
  DELETE /api/upstreams/{name}           — remove a remote upstream
  POST   /api/upstreams/{name}/test      — probe reachability + auth
  GET    /api/providers/catalog          — static integration catalog
  GET    /api/providers                  — configured providers (alias of upstreams)
  POST   /api/providers/{name}/credentials — write one API key to api.env

``/etc/hal0/upstreams.toml`` remains the source of truth (PLAN §6): every
write path funnels through :class:`~hal0.upstreams.registry.UpstreamRegistry`'s
persistent mutators, which rewrite the TOML atomically before touching the
in-memory registry — hand-editing the file plus ``hal0 config reload`` keeps
working alongside the reactive editor.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, ValidationError

from hal0.api._env_store import upsert_env_value
from hal0.api._redact import redact_config
from hal0.api.middleware.error_codes import Hal0Error
from hal0.config import paths
from hal0.config.schema import UpstreamEntry, UpstreamModelFilters
from hal0.upstreams.integrations import get_catalog
from hal0.upstreams.registry import (
    COMPOSITE_UPSTREAM_NAME,
    UpstreamAlreadyExists,
    UpstreamNotFound,
    UpstreamProtected,
)

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


class UpstreamProtectedHTTP(Hal0Error):
    code = "upstream.protected"
    status = 400


class UpstreamConflictHTTP(Hal0Error):
    code = "upstream.already_exists"
    status = 409


class UpstreamInvalidHTTP(Hal0Error):
    code = "upstream.invalid"
    status = 400


class ProviderCredentialError(Hal0Error):
    code = "provider.credential_write_failed"
    status = 400


# Upstream names double as TOML keys, URL path segments, and env-var stems —
# keep them boring: lowercase alnum plus - and _, max 64 chars.
_UPSTREAM_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _validation_summaries(exc: ValidationError) -> list[str]:
    """Flatten pydantic errors to JSON-safe strings (ctx can hold raw exceptions)."""
    return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]


# Fields editable on any upstream (catalog visibility / routing kill-switch).
_VISIBILITY_FIELDS = frozenset({"advertise_models", "enabled", "model_filters"})
# Fields only editable on TOML-authored remote upstreams — the composite and
# slot-backed entries get their structure from the slot lifecycle.
_STRUCTURAL_FIELDS = frozenset(
    {"url", "auth_style", "auth_header", "auth_value_env", "timeout_seconds", "warmup_strategy"}
)


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
    filters = getattr(u, "model_filters", None)
    out = {
        "name": u.name,
        "kind": u.kind,
        "url": u.url,
        "auth_style": u.auth_style,
        "auth_header": getattr(u, "auth_header", ""),
        "auth_value_env": u.auth_value_env,  # env-var *name*, not value
        "auth_configured": bool(u.auth_value_env),
        # Unlike auth_configured (env-var *declared*), this reports whether
        # the key has actually been written — drives the UI's auth badge.
        "auth_key_present": bool(u.auth_value_env and os.environ.get(u.auth_value_env)),
        "timeout_seconds": u.timeout_seconds,
        "slot_name": u.slot_name,
        "warmup_strategy": u.warmup_strategy,
        "advertise_models": u.advertise_models,
        "enabled": getattr(u, "enabled", True),
        "model_filters": (
            None
            if filters is None
            else {
                "models": list(filters.models),
                "include": list(filters.include),
                "exclude": list(filters.exclude),
            }
        ),
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


class UpstreamCreateBody(BaseModel):
    """Body for ``POST /api/upstreams`` — always creates a ``kind="remote"``.

    ``catalog_id`` prefills ``url``/``auth_style``/``auth_header`` from the
    integrations catalog (explicit body fields win over the prefill); without
    it ``url`` is required. Credentials are NOT accepted here — write the key
    afterwards via ``POST /api/providers/{name}/credentials`` so secrets never
    transit the CRUD surface.
    """

    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1, max_length=64)
    catalog_id: str | None = Field(default=None)
    url: str | None = Field(default=None)
    auth_style: str | None = Field(default=None)
    auth_header: str | None = Field(default=None)
    auth_value_env: str | None = Field(default=None)
    timeout_seconds: float | None = Field(default=None, gt=0.0)
    warmup_strategy: str | None = Field(default=None)
    advertise_models: bool = Field(default=True)
    enabled: bool = Field(default=True)
    model_filters: UpstreamModelFilters | None = Field(default=None)


@router.post("/upstreams", status_code=201)
async def create_upstream(body: UpstreamCreateBody, request: Request) -> dict[str, Any]:
    """Create a remote upstream reactively.

    Appends a row to ``upstreams.toml`` (atomic write, TOML stays canonical)
    and registers the upstream live — no API restart needed. Guards: the
    composite name is reserved, names must be unique, and slot upstreams
    can't be created here (they're owned by the slot lifecycle).
    """
    name = body.name.strip()
    if not _UPSTREAM_NAME_RE.match(name):
        raise UpstreamInvalidHTTP(
            "upstream name must be lowercase alphanumeric plus '-'/'_' (max 64 chars)",
            details={"name": body.name},
        )

    prefill: dict[str, Any] = {}
    if body.catalog_id is not None:
        entry = get_catalog().get(body.catalog_id)
        if entry is None:
            raise UpstreamInvalidHTTP(
                f"unknown catalog_id {body.catalog_id!r}",
                details={"catalog_id": body.catalog_id, "known": sorted(get_catalog())},
            )
        prefill = {
            "url": entry.get("base_url") or None,
            "auth_style": entry.get("auth") or None,
            "auth_header": (
                entry.get("auth_header_name") if entry.get("auth") == "header" else None
            ),
            "auth_value_env": f"{body.catalog_id.upper()}_API_KEY",
        }

    def pick(field: str, default: Any = None) -> Any:
        explicit = getattr(body, field)
        if explicit is not None:
            return explicit
        prefilled = prefill.get(field)
        return prefilled if prefilled is not None else default

    url = pick("url")
    if not url:
        raise UpstreamInvalidHTTP(
            "url is required (or supply a catalog_id with a known base_url)",
            details={"name": name},
        )

    try:
        entry_model = UpstreamEntry(
            name=name,
            kind="remote",
            url=url,
            auth_style=pick("auth_style", "bearer"),
            auth_header=pick("auth_header", ""),
            auth_value_env=pick("auth_value_env", ""),
            timeout_seconds=body.timeout_seconds if body.timeout_seconds is not None else 300.0,
            warmup_strategy=body.warmup_strategy or "none",
            advertise_models=body.advertise_models,
            enabled=body.enabled,
            model_filters=(
                None
                if body.model_filters is None or body.model_filters.is_empty()
                else body.model_filters
            ),
        )
    except ValidationError as exc:
        raise UpstreamInvalidHTTP(
            "invalid upstream configuration",
            details={"name": name, "errors": _validation_summaries(exc)},
        ) from exc

    upstreams = request.app.state.upstreams
    try:
        created = upstreams.create_persistent(entry_model)
    except UpstreamProtected as exc:
        raise UpstreamProtectedHTTP(str(exc), {"name": name}) from exc
    except UpstreamAlreadyExists as exc:
        raise UpstreamConflictHTTP(str(exc), {"name": name}) from exc

    _audit_log.info(
        "upstream.created",
        name=name,
        url=url,
        catalog_id=body.catalog_id,
        source=request.client.host if request.client else None,
    )
    out = _serialize_upstream(created)
    if created.auth_value_env and not os.environ.get(created.auth_value_env):
        out["hint"] = (
            f"write the API key next: POST /api/providers/{name}/credentials "
            f'{{"key": "{created.auth_value_env}", "value": "<secret>"}}'
        )
    return out


class UpstreamPatchBody(BaseModel):
    """Body for ``PATCH /api/upstreams/{name}``.

    Visibility fields (``advertise_models``, ``enabled``, ``model_filters``)
    are editable on every upstream kind. Structural fields (``url``,
    ``auth_*``, ``timeout_seconds``, ``warmup_strategy``) only on
    TOML-authored remote upstreams — the composite and slot-backed entries
    get their structure from the slot lifecycle. ``name``/``kind``/
    ``slot_name`` stay immutable (delete + recreate to rename).

    ``model_filters`` set to ``null`` or an all-empty object clears the
    filters; omitting the field leaves them unchanged.
    """

    model_config = {"extra": "forbid"}

    advertise_models: bool | None = Field(
        default=None,
        description="Whether this upstream's /v1/models appears in the aggregate catalog.",
    )
    enabled: bool | None = Field(
        default=None,
        description="Routing kill-switch — false removes the upstream from dispatch entirely.",
    )
    model_filters: UpstreamModelFilters | None = Field(default=None)
    url: str | None = Field(default=None)
    auth_style: str | None = Field(default=None)
    auth_header: str | None = Field(default=None)
    auth_value_env: str | None = Field(default=None)
    timeout_seconds: float | None = Field(default=None, gt=0.0)
    warmup_strategy: str | None = Field(default=None)


@router.patch("/upstreams/{name}")
async def patch_upstream(name: str, body: UpstreamPatchBody, request: Request) -> dict[str, Any]:
    """Mutate settings on a registered upstream.

    On success:

    - the in-memory :class:`Upstream` is updated,
    - the matching ``upstreams.toml`` row is rewritten atomically via
      :func:`hal0.config.loader.save_upstreams_config` (auto-registered
      upstreams with no TOML row get an in-memory-only update that resets
      on restart — same contract ``set_advertise`` always had),
    - the per-upstream model cache (``app.state.upstream_models[name]``)
      is punched on visibility flips so the next ``/api/upstreams`` and
      ``/v1/models`` request sees the change without an API restart,
    - the change is audit-logged.

    ``advertise_models``/``model_filters`` are catalog-visibility knobs;
    ``enabled`` is the routing kill-switch; the rest are structural.
    """
    upstreams = request.app.state.upstreams
    try:
        u = upstreams.get(name)
    except Exception:  # UpstreamRegistry.get never raises; defensive.
        u = None
    if u is None:
        raise UpstreamNotFoundHTTP(f"upstream {name!r} not found", {"name": name})

    # Only fields the caller actually sent — model_filters=null means
    # "clear", so presence must be distinguished from an omitted field.
    fields: dict[str, Any] = {
        k: getattr(body, k) for k in body.model_fields_set if k in body.model_fields
    }

    # Composite, slot-kind, and container-backed remotes (slot_name set)
    # get their structure from the slot lifecycle.
    protected = name == COMPOSITE_UPSTREAM_NAME or u.kind == "slot" or bool(u.slot_name)
    structural_requested = sorted(set(fields) & _STRUCTURAL_FIELDS)
    if protected and structural_requested:
        raise UpstreamProtectedHTTP(
            f"upstream {name!r} is {'the composite' if name == COMPOSITE_UPSTREAM_NAME else 'slot-backed'}; "
            f"structural fields are managed by the slot lifecycle",
            details={"name": name, "fields": structural_requested},
        )

    if not fields:
        # Nothing to do — but echo the current state so a no-op PATCH
        # remains idempotent and useful as a probe.
        model_cache: dict[str, list[str]] = getattr(request.app.state, "upstream_models", {})
        return _serialize_upstream(u, last_models=model_cache.get(name))

    if "model_filters" in fields and fields["model_filters"] is not None:
        mf = fields["model_filters"]
        if mf.is_empty():
            fields["model_filters"] = None

    try:
        updated = upstreams.apply_persistent_patch(name, fields)
    except ValidationError as exc:
        raise UpstreamInvalidHTTP(
            "invalid upstream configuration",
            details={"name": name, "errors": _validation_summaries(exc)},
        ) from exc

    # Punch the per-upstream model cache so the next /api/upstreams and
    # /v1/models request reflects visibility flips without an API restart.
    # The composite ``hal0`` upstream's module-level cache lives in
    # hal0.api and is unaffected by per-upstream flips.
    model_cache = getattr(request.app.state, "upstream_models", None)
    visibility_flip = {"advertise_models", "enabled"} & set(fields)
    if model_cache is not None and name in model_cache and visibility_flip:
        now_visible = updated.advertise_models and getattr(updated, "enabled", True)
        if now_visible:
            # Re-enabling: drop the stale snapshot so the next call refetches.
            model_cache.pop(name, None)
        else:
            # Disabling: drop the catalog rows immediately so /v1/models
            # never serves stale entries from the in-memory cache.
            model_cache[name] = []

    _audit_log.info(
        "upstream.patch",
        name=name,
        fields=sorted(fields),
        source=request.client.host if request.client else None,
    )
    return _serialize_upstream(updated, last_models=(model_cache or {}).get(name))


@router.delete("/upstreams/{name}")
async def delete_upstream(name: str, request: Request) -> dict[str, Any]:
    """Delete a remote upstream from the registry and ``upstreams.toml``.

    The composite and slot-backed upstreams are protected. Credentials in
    ``api.env`` are deliberately retained — deleting an upstream must never
    destroy a secret (remove it via ``/api/secrets`` if truly unwanted).
    """
    upstreams = request.app.state.upstreams
    try:
        removed_from_toml = upstreams.remove_persistent(name)
    except UpstreamNotFound as exc:
        raise UpstreamNotFoundHTTP(str(exc), {"name": name}) from exc
    except UpstreamProtected as exc:
        raise UpstreamProtectedHTTP(str(exc), {"name": name}) from exc

    model_cache = getattr(request.app.state, "upstream_models", None)
    if model_cache is not None:
        model_cache.pop(name, None)

    _audit_log.info(
        "upstream.deleted",
        name=name,
        removed_from_toml=removed_from_toml,
        source=request.client.host if request.client else None,
    )
    return {
        "ok": True,
        "name": name,
        "removed_from_toml": removed_from_toml,
        "hint": "credentials in api.env are retained; remove via /api/secrets if unwanted",
    }


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
    except ValueError as exc:
        # Writer-level guard tripped (e.g. newline in value) — surface as
        # a 400 rather than a 500.
        raise ProviderCredentialError(
            str(exc),
            details={"path": str(api_env), "error": str(exc)},
        ) from exc
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
    ``hal0.mcp.admin.GATED_TOOLS``). Auth was removed by design;
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

    # Reject control chars in the value — a newline would break out of the
    # quoted api.env line and inject an arbitrary env-var (the file is an
    # unauthenticated, LAN-writable systemd EnvironmentFile). The shared
    # writer guards \n/\r too; this catches the wider control-char set with
    # a clear 400 before we touch the file.
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in body.value):
        raise ProviderCredentialError(
            "control characters not allowed in credential value",
            details={"name": name, "key": key},
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
