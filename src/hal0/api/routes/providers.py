"""External upstream LLM providers (mounted under /api).

Endpoints:
  GET    /api/upstreams                  — list registered routing targets
  POST   /api/upstreams                  — create a remote upstream (reactive)
  GET    /api/upstreams/{name}           — single upstream
  PATCH  /api/upstreams/{name}           — mutate settings (visibility + structural)
  DELETE /api/upstreams/{name}           — remove a remote upstream
  POST   /api/upstreams/{name}/test      — probe reachability + auth
  GET    /api/upstreams/{name}/models    — stored model rows merged with discovery
  PATCH  /api/upstreams/{name}/models/{id} — enable, retype or price one model
  POST   /api/upstreams/{name}/refresh   — refetch discovery (converts an uncurated row)
  GET    /api/providers/catalog          — static integration catalog
  POST   /api/providers/probe            — check a URL + key and list models, saving nothing
  GET    /api/providers                  — configured providers (alias of upstreams)
  POST   /api/providers/{name}/credentials — write one API key to api.env

``/etc/hal0/upstreams.toml`` remains the source of truth (PLAN §6): every
write path funnels through :class:`~hal0.upstreams.registry.UpstreamRegistry`'s
persistent mutators, which rewrite the TOML atomically before touching the
in-memory registry — hand-editing the file plus ``hal0 config reload`` keeps
working alongside the reactive editor.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator

from hal0.api._env_store import TlsTrustError, is_process_control_env, upsert_env_value
from hal0.api._redact import redact_config
from hal0.api.middleware.error_codes import Hal0Error
from hal0.config import paths
from hal0.config.schema import (
    VALID_PRICE_UNITS,
    UpstreamEntry,
    UpstreamModelEntry,
    UpstreamModelFilters,
)
from hal0.upstreams.catalog import (
    MODEL_TYPES,
    UpstreamModel,
    canonical_id,
    enabled_ids,
    reseed_curated_cache,
)
from hal0.upstreams.discovery import (
    DiscoveredModel,
    DiscoveryCache,
    guess_type,
    parse_models_payload,
)
from hal0.upstreams.presets import get_preset, load_presets, preset_to_api
from hal0.upstreams.registry import (
    KNOWN_AUTH_STYLES,
    RESERVED_UPSTREAM_NAME,
    ProbeUrlError,
    UpstreamAlreadyExists,
    UpstreamNotFound,
    UpstreamProtected,
    normalise_key,
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


class UpstreamProtectedWriteHTTP(Hal0Error):
    """409 - a per-model write on an agent-managed or slot-backed upstream."""

    code = "upstream.protected"
    status = 409


class UpstreamNotCuratedHTTP(Hal0Error):
    code = "upstream.not_curated"
    status = 409


class UpstreamConflictHTTP(Hal0Error):
    code = "upstream.already_exists"
    status = 409


class UnsupportedMediaTypeHTTP(Hal0Error):
    code = "upstream.unsupported_media_type"
    status = 415


class UpstreamInvalidHTTP(Hal0Error):
    code = "upstream.invalid"
    status = 400


def _reserved_env_name_reason(key: str) -> str | None:
    """Why ``key`` may not hold an upstream credential, or None when it may (#2936).

    ``api.env`` is hal0-api's own environment file: a loader or interpreter
    variable there runs code in every process hal0-api starts, and a
    ``HAL0_*`` name is hal0's own service configuration and auth keys
    (#1450), which ``/api/secrets`` already refuses.
    """
    from hal0.api.routes.secrets import is_protected_secret

    if is_process_control_env(key):
        return "process_control"
    if is_protected_secret(key):
        return "hal0_reserved"
    return None


def _check_auth_value_env(value: str | None, *, name: str) -> None:
    """Refuse an upstream ``auth_value_env`` the credentials route would refuse."""
    if not value:
        return
    reason = _reserved_env_name_reason(value.strip())
    if reason is not None:
        raise UpstreamInvalidHTTP(
            f"auth_value_env {value!r} is reserved ({reason}) and cannot name an "
            "upstream credential",
            details={"name": name, "auth_value_env": value, "reason": reason},
        )


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
# Fields only editable on TOML-authored remote upstreams — the reserved-name
# and slot-backed entries get their structure from the slot lifecycle.
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
        "managed_by": getattr(u, "managed_by", None),
        "pinned": bool(getattr(u, "pinned", False)),
        "owned_models": list(getattr(u, "owned_models", ())),
        "catalog_id": getattr(u, "catalog_id", None),
        "curated": bool(getattr(u, "curated", False)),
        "budget_monthly_usd": getattr(u, "budget_monthly_usd", None),
        "enabled_count": len(enabled_ids(u)) if hasattr(u, "models") else 0,
        "models_total": len(getattr(u, "models", ())),
    }
    return redact_config(out)


@router.get("/upstreams")
async def list_upstreams(request: Request) -> list[dict[str, Any]]:
    """Return all configured upstreams (slots + remote providers).

    Pulled from ``app.state.upstreams`` — the same registry the dispatcher
    routes through. Each entry's ``models`` is the cached id list in
    ``app.state.upstream_models`` for that upstream (a curated upstream's is its
    enabled ids), empty when nothing is cached for it.
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
    provider presets (explicit body fields win over the prefill); without
    it ``url`` is required. Credentials are NOT accepted here — write the key
    afterwards via ``POST /api/providers/{name}/credentials`` so secrets never
    transit the CRUD surface. With ``models`` present (even empty) the row is
    curated and routes exactly its enabled entries; with ``models`` omitted it is
    created uncurated, as before per-model rows, and converts later.
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
    budget_monthly_usd: float | None = Field(default=None, ge=0)
    models: list[UpstreamModelEntry] | None = Field(default=None)


@router.post("/upstreams", status_code=201)
async def create_upstream(body: UpstreamCreateBody, request: Request) -> dict[str, Any]:
    """Create a remote upstream reactively.

    Appends a row to ``upstreams.toml`` (atomic write, TOML stays canonical)
    and registers the upstream live — no API restart needed. Guards: the
    reserved name can't be used, names must be unique, and slot upstreams
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
        preset = get_preset(body.catalog_id)
        if preset is None:
            raise UpstreamInvalidHTTP(
                f"unknown catalog_id {body.catalog_id!r}",
                details={"catalog_id": body.catalog_id, "known": sorted(load_presets())},
            )
        prefill = {
            "url": preset.base_url or None,
            "auth_style": preset.auth or None,
            "auth_header": (preset.auth_header_name if preset.auth == "header" else None),
            "auth_value_env": f"{body.catalog_id.upper()}_API_KEY",
        }

    def pick(field: str, default: Any = None) -> Any:
        explicit = getattr(body, field)
        if explicit is not None:
            return explicit
        prefilled = prefill.get(field)
        return prefilled if prefilled is not None else default

    _check_auth_value_env(pick("auth_value_env", ""), name=name)
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
            catalog_id=body.catalog_id,
            curated=body.models is not None,
            budget_monthly_usd=body.budget_monthly_usd,
            models=body.models or [],
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

    # A curated upstream's cache entry is its enabled rows; an uncurated new row
    # is left to the dispatcher, as before per-model rows.
    created_cache = getattr(request.app.state, "upstream_models", None)
    if created_cache is not None:
        reseed_curated_cache(upstreams, created_cache, created.name)

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


# Fields that decide which listing a provider answers with: a change to any of
# them makes a stored discovery entry stale.
_DISCOVERY_IDENTITY_FIELDS = ("url", "auth_style", "auth_header", "auth_value_env", "catalog_id")


class UpstreamPatchBody(BaseModel):
    """Body for ``PATCH /api/upstreams/{name}``.

    Visibility fields (``advertise_models``, ``enabled``, ``model_filters``)
    are editable on every upstream kind. Structural fields (``url``,
    ``auth_*``, ``timeout_seconds``, ``warmup_strategy``) only on
    TOML-authored remote upstreams — the reserved-name and slot-backed
    entries get their structure from the slot lifecycle. ``name``/``kind``/
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

    # Agent-managed entries are owned wholesale by the agent: any edit
    # (enabled and model_filters included) goes through the agent.
    if fields and getattr(u, "managed_by", None):
        raise UpstreamProtectedHTTP(
            f"upstream {name!r} is managed by {u.managed_by}; "
            "change it through the agent, not here",
            details={"name": name, "managed_by": u.managed_by},
        )

    # The reserved name, slot-kind, and container-backed remotes (slot_name
    # set) get their structure from the slot lifecycle.
    protected = name == RESERVED_UPSTREAM_NAME or u.kind == "slot" or bool(u.slot_name)
    structural_requested = sorted(set(fields) & _STRUCTURAL_FIELDS)
    if protected and structural_requested:
        raise UpstreamProtectedHTTP(
            f"upstream {name!r} is "
            f"{'reserved' if name == RESERVED_UPSTREAM_NAME else 'slot-backed'}; "
            f"structural fields are managed by the slot lifecycle",
            details={"name": name, "fields": structural_requested},
        )

    if "auth_value_env" in fields:
        _check_auth_value_env(fields["auth_value_env"], name=name)

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
    # The direct-read composite catalogue's module-level TTL cache lives in
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

    if model_cache is not None:
        # Curated rows win over the visibility handling above: the cache entry
        # is always the enabled list, whatever flipped.
        reseed_curated_cache(upstreams, model_cache, name)
    discovery = getattr(request.app.state, "upstream_discovery", None)
    if discovery is not None and any(
        getattr(u, f, None) != getattr(updated, f, None) for f in _DISCOVERY_IDENTITY_FIELDS
    ):
        discovery.drop(name)  # a listing from another endpoint or account is stale

    _audit_log.info(
        "upstream.patch",
        name=name,
        fields=sorted(fields),
        source=request.client.host if request.client else None,
    )
    return _serialize_upstream(updated, last_models=(model_cache or {}).get(name))


# ── per-model routes ──────────────────────────────────────────────────────────


class UpstreamModelPatchBody(BaseModel):
    """Body for ``PATCH /api/upstreams/{name}/models/{id}``; only sent fields change.

    ``context``, ``price_in`` and ``price_out`` accept ``null`` to clear the value.
    """

    model_config = {"extra": "forbid"}

    enabled: bool = Field(default=True)
    type: str = Field(default="chat")
    context: int | None = Field(default=None, gt=0)
    price_in: float | None = Field(default=None, ge=0)
    price_out: float | None = Field(default=None, ge=0)
    price_unit: str = Field(default="Mtok")

    @field_validator("type")
    @classmethod
    def _type_valid(cls, v: str) -> str:
        if v not in MODEL_TYPES:
            raise ValueError(f"type must be one of {sorted(MODEL_TYPES)}")
        return v

    @field_validator("price_unit")
    @classmethod
    def _unit_valid(cls, v: str) -> str:
        if v not in VALID_PRICE_UNITS:
            raise ValueError(f"price_unit must be one of {sorted(VALID_PRICE_UNITS)}")
        return v


def _lookup_upstream(request: Request, name: str) -> Any:
    u = request.app.state.upstreams.get(name)
    if u is None:
        raise UpstreamNotFoundHTTP(f"upstream {name!r} not found", {"name": name})
    return u


def _is_protected(u: Any) -> bool:
    """Agent-managed, slot-backed, slot-kind or the reserved name: not editable here."""
    return bool(
        getattr(u, "managed_by", None)
        or u.slot_name
        or u.kind == "slot"
        or u.name == RESERVED_UPSTREAM_NAME
    )


def _refuse_protected(u: Any) -> None:
    if _is_protected(u):
        owner = getattr(u, "managed_by", None)
        raise UpstreamProtectedWriteHTTP(
            f"upstream {u.name!r} is "
            f"{'managed by ' + owner if owner else 'slot-backed or reserved'}; "
            "its models are not editable here",
            details={"name": u.name, "managed_by": owner},
        )


def _discovery_cache(request: Request) -> DiscoveryCache:
    cache = getattr(request.app.state, "upstream_discovery", None)
    if cache is None:
        cache = DiscoveryCache()
        request.app.state.upstream_discovery = cache
    return cache


async def _discovered(
    request: Request, u: Any, *, force: bool
) -> tuple[list[DiscoveredModel], float | None, bool]:
    """Cached discovery for ``u`` as ``(models, fetched_at, stale)``.

    Refetches when forced or when the cache is past its TTL. A failed or empty
    refetch keeps whatever the cache holds and reports ``stale``; nothing here
    raises, so a provider that is down never fails a listing.
    """
    cache = _discovery_cache(request)
    fresh = not force and not cache.is_stale(u.name)
    if not fresh:
        try:
            payload = await request.app.state.upstreams.fetch_model_payload(u.name)
        except Exception:
            payload = None
        models = (
            parse_models_payload(payload, get_preset(u.catalog_id or ""))
            if payload is not None
            else []
        )
        if models:  # an empty answer is not worth remembering
            cache.put(u.name, models)
            fresh = True
    entry = cache.get(u.name)
    return (entry[0], entry[1], not fresh) if entry else ([], None, True)


def _row(
    upstream: str,
    model_id: str,
    *,
    enabled: bool,
    type_: str,
    context: int | None,
    price_in: float | None,
    price_out: float | None,
    price_unit: str,
    discovered: bool,
    recommended: bool,
) -> dict[str, Any]:
    return {
        "id": model_id,
        "canonical_id": canonical_id(upstream, model_id),
        "enabled": enabled,
        "type": type_,
        "context": context,
        "price_in": price_in,
        "price_out": price_out,
        "price_unit": price_unit,
        "discovered": discovered,
        "recommended": recommended,
        "in_use_by": [],  # filled by a later lane
    }


def _recommended_ids(u: Any) -> frozenset[str]:
    preset = get_preset(u.catalog_id or "")
    return frozenset(m.id for m in preset.models if m.recommended) if preset else frozenset()


def _stored_row(u: Any, m: UpstreamModel, d: DiscoveredModel | None, rec: frozenset[str]):
    """A stored row, its own values first; discovery only fills what is blank."""
    context = m.context if m.context is not None else (d.context if d else None)
    price_in, price_out, unit = m.price_in, m.price_out, m.price_unit
    if d is not None and price_in is None and price_out is None:
        price_in, price_out, unit = d.price_in, d.price_out, d.price_unit
    return _row(
        u.name,
        m.id,
        enabled=m.enabled,
        type_=m.type,
        context=context,
        price_in=price_in,
        price_out=price_out,
        price_unit=unit,
        discovered=d is not None,
        recommended=m.id in rec,
    )


def _merged_rows(u: Any, discovered: list[DiscoveredModel]) -> list[dict[str, Any]]:
    """Stored rows first, then models only discovery lists (switched off)."""
    found = {d.id: d for d in discovered}
    rec = _recommended_ids(u)
    rows = [_stored_row(u, m, found.get(m.id), rec) for m in u.models]
    stored = {m.id for m in u.models}
    for d in discovered:
        if d.id not in stored:
            rows.append(
                _row(
                    u.name,
                    d.id,
                    enabled=False,
                    type_=d.type,
                    context=d.context,
                    price_in=d.price_in,
                    price_out=d.price_out,
                    price_unit=d.price_unit,
                    discovered=True,
                    recommended=d.id in rec,
                )
            )
    return rows


def _live_rows(request: Request, u: Any) -> list[dict[str, Any]]:
    """Read-only rows for an upstream with no model rows: the ids it serves now."""
    cache: dict[str, list[str]] = getattr(request.app.state, "upstream_models", {})
    return [
        _row(
            u.name,
            mid,
            enabled=True,
            type_=guess_type(mid),
            context=None,
            price_in=None,
            price_out=None,
            price_unit="Mtok",
            discovered=True,
            recommended=False,
        )
        for mid in cache.get(u.name, [])
    ]


async def _listing(
    request: Request,
    u: Any,
    *,
    force: bool = False,
    warning: str | None = None,
    store_new: bool = False,
) -> dict[str, Any]:
    """The model listing of ``u``; read-only unless ``store_new``.

    With ``store_new`` (the refresh route) a fresh discovery answer is also
    written back: each offered model with no stored row becomes a switched-off
    row (:func:`hal0.upstreams.convert.sync_new_models`), so the listing shows
    it from the stored rows.
    """
    if _is_protected(u):
        return {
            "name": u.name,
            "curated": False,
            "protected": True,
            "fetched_at": None,
            "stale": False,
            "warning": warning,
            "models": _live_rows(request, u),
        }
    if not u.curated:
        # Awaiting conversion: nothing is stored, so show what is served today.
        return {
            "name": u.name,
            "curated": False,
            "protected": False,
            "fetched_at": None,
            "stale": True,
            "warning": warning,
            "models": _live_rows(request, u),
        }
    found, fetched_at, stale = await _discovered(request, u, force=force)
    if store_new and not stale:
        from hal0.upstreams import convert

        await convert.sync_new_models(
            request.app.state.upstreams,
            u.name,
            model_cache=getattr(request.app.state, "upstream_models", None),
            discovered=found,
        )
    # The fetch awaited: read the rows again so a concurrent write is not lost.
    u = _lookup_upstream(request, u.name)
    return {
        "name": u.name,
        "curated": True,
        "protected": False,
        "fetched_at": fetched_at,
        "stale": stale,
        "warning": warning,
        "models": _merged_rows(u, found),
    }


@router.get("/upstreams/{name}/models")
async def list_upstream_models(name: str, request: Request) -> dict[str, Any]:
    """Stored model rows merged with discovery; never fails because the provider is down."""
    return await _listing(request, _lookup_upstream(request, name))


@router.patch("/upstreams/{name}/models/{model_id:path}")
async def patch_upstream_model(
    name: str, model_id: str, body: UpstreamModelPatchBody, request: Request
) -> dict[str, Any]:
    """Upsert one model row on a curated upstream and apply it to routing at once.

    A model only discovery lists gets a row seeded from its discovered values,
    switched off unless the body enables it.
    """
    u = _lookup_upstream(request, name)
    _refuse_protected(u)
    if not u.curated:
        raise UpstreamNotCuratedHTTP(
            f"upstream {name!r} is not converted yet; refresh when the provider is reachable",
            details={"name": name},
        )
    sent = body.model_fields_set

    try:
        UpstreamModelEntry(id=model_id)  # refuse a bad id before any network call
    except ValidationError as exc:
        raise UpstreamInvalidHTTP(
            "invalid model row", details={"name": name, "errors": _validation_summaries(exc)}
        ) from exc
    seed: DiscoveredModel | None = None
    if not any(m.id == model_id for m in u.models):
        found, _at, _stale = await _discovered(request, u, force=False)
        seed = next((d for d in found if d.id == model_id), None)
        # The await let other writes land: everything below reads the row again.
        u = _lookup_upstream(request, name)
    stored = next((m for m in u.models if m.id == model_id), None)
    if stored is not None:
        data: dict[str, Any] = asdict(stored)
    else:
        data = {"id": model_id, "enabled": False, "type": guess_type(model_id)}
        if seed is not None:
            data.update(
                type=seed.type,
                context=seed.context,
                price_in=seed.price_in,
                price_out=seed.price_out,
                price_unit=seed.price_unit,
            )
    data.update({k: getattr(body, k) for k in sent})

    try:
        entry = UpstreamModelEntry.model_validate(data)
    except ValidationError as exc:
        raise UpstreamInvalidHTTP(
            "invalid model row", details={"name": name, "errors": _validation_summaries(exc)}
        ) from exc
    rows = [m for m in u.models if m.id != model_id]
    position = next((i for i, m in enumerate(u.models) if m.id == model_id), len(rows))
    rows.insert(position, UpstreamModel(**entry.model_dump()))

    upstreams = request.app.state.upstreams
    try:
        updated = upstreams.apply_persistent_patch(name, {"models": rows})
    except ValidationError as exc:
        raise UpstreamInvalidHTTP(
            "invalid upstream configuration",
            details={"name": name, "errors": _validation_summaries(exc)},
        ) from exc
    model_cache = getattr(request.app.state, "upstream_models", None)
    if model_cache is not None:
        reseed_curated_cache(upstreams, model_cache, name)
    _audit_log.info(
        "upstream.model_patch",
        name=name,
        model=model_id,
        fields=sorted(sent),
        source=request.client.host if request.client else None,
    )
    cached = _discovery_cache(request).get(name)
    discovered = cached[0] if cached else []
    return next(r for r in _merged_rows(updated, discovered) if r["id"] == model_id)


def _deferred_warning(u: Any, reason: str | None) -> str:
    """Operator-facing text for a row the converter left alone; no key, URL or error text."""
    if reason in ("backup_failed", "write_failed"):
        return (
            "the config file could not be written, so the row was not converted; check the hal0 log"
        )
    if reason == "no_valid_models":
        return "the provider's model list had no usable ids, so the row was not converted"
    if reason in ("unreachable", "no_models"):
        return f"the provider returned no models; {_why_unreachable(u)}"
    return "the row changed while it was being converted; refresh again"


def _why_unreachable(u: Any) -> str:
    """Class-wise hint for an empty provider answer; never carries a key or a URL."""
    if not u.url:
        return "no url is configured"
    if u.auth_style != "none" and u.auth_value_env and not os.environ.get(u.auth_value_env):
        return "no key is configured"
    return "check the url and key, then refresh again"


@router.post("/upstreams/{name}/refresh")
async def refresh_upstream_models(name: str, request: Request) -> dict[str, Any]:
    """Refetch a provider's model list and return the listing.

    On a curated row discovery is refetched and each model the provider newly
    offers is stored as a switched-off row (``GET …/models`` never writes). On
    an uncurated, plain remote
    row the one-time conversion runs for that row first (same rules, backup and
    warnings as the boot converter). A provider that cannot be reached leaves
    the row untouched and answers ``stale: true``, not an error.
    """
    u = _lookup_upstream(request, name)
    _refuse_protected(u)
    if not u.curated:
        from hal0.upstreams import convert

        warning: str | None = None
        reason: str | None = None
        try:
            result = await convert.convert_legacy_upstreams(
                request.app.state.upstreams,
                providers_toml=paths.etc() / "providers.toml",
                model_cache=getattr(request.app.state, "upstream_models", None),
                discovery=_discovery_cache(request),
                only=name,
            )
            reason = result.deferred_reasons.get(name)
        except Exception as exc:
            _log.warning("upstream.refresh_convert_failed", name=name, error=type(exc).__name__)
            warning = f"conversion failed ({type(exc).__name__}); the row was not changed"
        u = _lookup_upstream(request, name)
        if warning is None and not u.curated:
            warning = _deferred_warning(u, reason)
        return await _listing(request, u, warning=warning)
    return await _listing(request, u, force=True, store_new=True)


@router.delete("/upstreams/{name}")
async def delete_upstream(name: str, request: Request) -> dict[str, Any]:
    """Delete a remote upstream from the registry and ``upstreams.toml``.

    The reserved-name and slot-backed upstreams are protected. Credentials in
    ``api.env`` are deliberately retained — deleting an upstream must never
    destroy a secret (remove it via ``/api/secrets`` if truly unwanted).
    """
    upstreams = request.app.state.upstreams
    existing = upstreams.get(name)
    if existing is not None and getattr(existing, "managed_by", None):
        raise UpstreamProtectedHTTP(
            f"upstream {name!r} is managed by {existing.managed_by}; "
            "change it through the agent, not here",
            details={"name": name, "managed_by": existing.managed_by},
        )
    try:
        removed_from_toml = upstreams.remove_persistent(name)
    except UpstreamNotFound as exc:
        raise UpstreamNotFoundHTTP(str(exc), {"name": name}) from exc
    except UpstreamProtected as exc:
        raise UpstreamProtectedHTTP(str(exc), {"name": name}) from exc

    model_cache = getattr(request.app.state, "upstream_models", None)
    if model_cache is not None:
        model_cache.pop(name, None)
    discovery = getattr(request.app.state, "upstream_discovery", None)
    if discovery is not None:
        discovery.drop(name)

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
    """Return the provider presets (built-in upstream templates).

    Used by the "Add upstream" form to populate the dropdown of known
    providers (Anthropic, OpenAI, OpenRouter, hal0 self, custom, …).
    """
    return {pid: preset_to_api(p) for pid, p in load_presets().items()}


_RATE_LIMIT_FIELDS = ("limit_requests", "remaining_requests", "limit_tokens", "remaining_tokens")


class ProviderProbeBody(BaseModel):
    """Body for ``POST /api/providers/probe``.

    ``key`` is a transient secret: it is held as :class:`SecretStr`, used for
    one outbound request and never stored, logged or echoed.
    """

    model_config = {"extra": "forbid"}

    catalog_id: str | None = Field(default=None)
    url: str | None = Field(default=None)
    auth_style: str | None = Field(default=None)
    auth_header: str | None = Field(default=None)
    key: SecretStr | None = Field(default=None, repr=False)


_KEY_REFUSAL = "key must be printable ASCII without whitespace"


def _rate_limit_values(preset: Any, headers: dict[str, str]) -> dict[str, int | None]:
    """Parse the preset-declared rate-limit headers; null when absent or not an integer."""
    declared = preset.rate_limit_headers if preset is not None else {}
    out: dict[str, int | None] = dict.fromkeys(_RATE_LIMIT_FIELDS)
    for field_name in _RATE_LIMIT_FIELDS:
        header = declared.get(field_name)
        raw = headers.get(header.lower()) if header else None
        try:
            out[field_name] = int(raw) if raw is not None else None
        except ValueError:
            out[field_name] = None
    return out


@router.post("/providers/probe")
async def probe_provider(request: Request) -> dict[str, Any]:
    """Check a base URL and key and list the provider's models; persist nothing.

    Gating matches the other upstream write routes (none per route; the app
    middleware decides). The body is parsed by hand so that no validation
    error, which would echo the offending ``input``, can carry the key.
    ``models[].canonical_id`` is always null: no provider name exists yet.
    """
    # A cross-site form can send text/plain without a CORS preflight; refusing
    # anything but JSON stops it driving a blind server-side GET.
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise UnsupportedMediaTypeHTTP("content-type must be application/json")
    try:
        raw = await request.json()
        body = ProviderProbeBody.model_validate(raw)
    except ValidationError as exc:
        # loc + msg only: never ``input``.
        raise UpstreamInvalidHTTP(
            "invalid probe request",
            details={"errors": _validation_summaries(exc)},
        ) from None
    except ValueError:
        raise UpstreamInvalidHTTP("request body must be a JSON object") from None

    preset = None
    if body.catalog_id is not None:
        preset = get_preset(body.catalog_id)
        if preset is None:
            raise UpstreamInvalidHTTP(
                f"unknown catalog_id {body.catalog_id!r}",
                details={"catalog_id": body.catalog_id, "known": sorted(load_presets())},
            )
    url = body.url or (preset.base_url if preset is not None else "")
    if not url:
        raise UpstreamInvalidHTTP("url is required (or supply a catalog_id with a known base_url)")
    auth_style = body.auth_style or (preset.auth if preset is not None else "") or "bearer"
    if auth_style.lower() not in KNOWN_AUTH_STYLES:
        raise UpstreamInvalidHTTP(
            f"unknown auth_style {auth_style!r}", details={"known": sorted(KNOWN_AUTH_STYLES)}
        )
    auth_header = body.auth_header or (
        preset.auth_header_name if preset is not None and preset.auth == "header" else ""
    )
    if auth_style.lower() == "header" and not auth_header.strip():
        raise UpstreamInvalidHTTP("auth_style 'header' needs an auth_header name")
    key = body.key.get_secret_value() if body.key is not None else ""
    if normalise_key(auth_style, auth_header, key) is None:
        raise UpstreamInvalidHTTP(_KEY_REFUSAL)

    try:
        result = await request.app.state.upstreams.probe(
            url=url, auth_style=auth_style, auth_header=auth_header, key=key
        )
    except ProbeUrlError as exc:
        raise UpstreamInvalidHTTP(str(exc)) from None

    models: list[dict[str, Any]] = []
    if result["ok"]:
        for m in parse_models_payload(result["payload"], preset):
            models.append(
                {
                    "id": m.id,
                    "canonical_id": None,
                    "type": m.type,
                    "type_source": m.type_source,
                    "context": m.context,
                    "price_in": m.price_in,
                    "price_out": m.price_out,
                    "price_unit": m.price_unit,
                    "recommended": m.recommended,
                }
            )
    _log.info(
        "provider.probed",
        catalog_id=body.catalog_id,
        ok=result["ok"],
        status=result["status"],
        models=len(models),
    )
    return {
        "ok": result["ok"],
        "status": result["status"],
        "latency_ms": result["latency_ms"],
        "models": models,
        "rate_limit": _rate_limit_values(preset, result["headers"]),
        "error": result["error"],
    }


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
        upsert_env_value(api_env, key, value, environ=os.environ)
    except TlsTrustError as exc:
        raise ProviderCredentialError(
            f"key {key!r} selects the CA bundle outbound TLS clients trust, so it is only "
            f"accepted as a root-owned path hal0 cannot write: {exc.reason}",
            details={"key": key, "reason": "tls_trust"},
        ) from exc
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

    # An agent-managed entry's key belongs to the agent (Hermes's own .env,
    # ADR-0035 decision 3); refuse before anything is written to api.env.
    if getattr(upstream, "managed_by", None):
        raise UpstreamProtectedHTTP(
            f"upstream {name!r} is managed by {upstream.managed_by}; "
            "change it through the agent, not here",
            details={"name": name, "managed_by": upstream.managed_by},
        )

    key = body.key.strip()
    if not _ENV_KEY_RE.match(key):
        raise ProviderCredentialError(
            "key must be an ALL_CAPS env-var name "
            "(letters, digits, underscores; no leading digit, no shell metacharacters)",
            details={"key": key},
        )
    # #2936: same refusals as /api/secrets — no loader or interpreter
    # variable and no HAL0_* service key lands in hal0-api's environment
    # through a credential, whatever auth_value_env the upstream declares.
    reason = _reserved_env_name_reason(key)
    if reason is not None:
        raise ProviderCredentialError(
            f"key {key!r} is reserved ({reason}) and cannot hold a credential",
            details={"key": key, "reason": reason},
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

    # Normalise the value exactly as the probe does (same helper, this
    # upstream's style and header). Anything that is not printable ASCII is
    # refused, which includes the control characters (a newline would break
    # out of the quoted api.env line and inject an arbitrary env-var; the file
    # is a LAN-writable systemd EnvironmentFile).
    normal = normalise_key(upstream.auth_style, upstream.auth_header, body.value)
    if normal is None or not normal[0]:
        raise ProviderCredentialError(_KEY_REFUSAL, details={"name": name, "key": key})
    token = normal[0]

    api_env = paths.etc() / "api.env"
    try:
        await asyncio.to_thread(_write_credential_to_api_env, api_env, key, token)
    except ProviderCredentialError:
        raise
    except OSError as exc:
        raise ProviderCredentialError(
            f"failed to write {api_env}: {exc}",
            details={"path": str(api_env), "error": str(exc)},
        ) from exc

    # The writer above already updated the in-process environment, under the
    # api.env lock, so the running registry observes the new value without a
    # restart — it reads ``os.environ[upstream.auth_value_env]`` per call.
    # The persisted api.env line is the source of truth across restarts.

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
