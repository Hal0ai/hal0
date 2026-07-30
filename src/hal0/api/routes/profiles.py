"""Profile catalog endpoints.

Mounted under /api/profiles:

    GET    ""              — list all profiles
    POST   ""              — create a custom profile (201)
    POST   "/import"       — import a profile from a portable envelope
    GET    "/{name}"       — resolve a single profile
    POST   "/{name}/export"— export a profile to a portable envelope
    PUT    "/{name}"       — update a custom profile (200)
    DELETE "/{name}"       — delete a custom profile (204)

Seed profiles (defined in SEED_PROFILES) are immutable via the API.

Write flow delegates to :class:`hal0.profiles.ProfileCatalog`, which owns
seed immutability, duplicate checks, in-use scans, and full-catalog
atomic writes.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator

from hal0.api._audit import record_action
from hal0.config.schema import ProfileConfig
from hal0.errors import BadRequest
from hal0.profiles import ProfileCatalog, ProfilePatch
from hal0.profiles.portable import (
    export_envelope,
    import_profile,
    parse_envelope,
    verify_checksum,
)

router = APIRouter()

#: Mirror of manager._SLOT_NAME_RE — kebab-case, leading alphanumeric, ≤32 chars.
_PROFILE_NAME_RE = r"^[a-z0-9][a-z0-9_-]{0,31}$"


# ── request models ────────────────────────────────────────────────────────────


class ProfileBody(BaseModel):
    """Body for POST /api/profiles and PUT /api/profiles/{name}."""

    name: str = Field(
        ...,
        description="Profile key (kebab-case, ≤32 chars, leading alphanumeric).",
    )
    flags: str = Field(default="", description="Bench-tuned llama-server CLI flags.")
    mtp: bool = Field(default=False, description="Append MTP bundle to flags when True.")
    device_class: Literal["gpu", "cpu", "npu", "img"] = Field(
        default="gpu",
        description="Device class this profile targets.",
    )
    backend: Literal["rocm", "vulkan"] | None = Field(
        default=None,
        description="GPU runtime (rocm|vulkan); None for non-GPU profiles.",
    )
    cloned_from: str | None = Field(
        default=None,
        description="Provenance: profile this one was cloned from (informational).",
    )
    intent: str = Field(default="", description="Human label for the card headline.")
    quant: str = Field(default="", description="Weight quant shown as a card chip.")

    @field_validator("name")
    @classmethod
    def name_kebab(cls, v: str) -> str:
        if not re.match(_PROFILE_NAME_RE, v):
            raise ValueError(
                "profile name must be kebab-case (a-z0-9_-), ≤32 chars, start with alphanumeric"
            )
        return v


class ProfileUpdateBody(BaseModel):
    """Body for PUT /api/profiles/{name} — name is taken from the URL."""

    flags: str | None = Field(default=None, description="Bench-tuned llama-server CLI flags.")
    mtp: bool | None = Field(default=None, description="MTP toggle.")
    device_class: Literal["gpu", "cpu", "npu", "img"] | None = Field(
        default=None,
        description="Device class this profile targets.",
    )
    backend: Literal["rocm", "vulkan"] | None = Field(
        default=None,
        description="GPU runtime (rocm|vulkan); None for non-GPU profiles.",
    )
    intent: str | None = Field(default=None, description="Human label for the card headline.")
    quant: str | None = Field(default=None, description="Weight quant shown as a card chip.")


# ── routes ────────────────────────────────────────────────────────────────────


@router.get("")
def list_profiles() -> list[dict[str, Any]]:
    """Return every profile in the catalog as a JSON array.

    Each item shape::

        {
            "name":           "rocm",
            "flags":          "-fa on ...",
            "mtp":            false,
            "device_class":   "gpu",          # gpu | cpu | npu | img
            "backend":        "rocm",         # rocm | vulkan | null (non-GPU)
            "resolved_flags": "-fa on ...",   # flags + MTP bundle when mtp=true
            "intent":         "MoE agents",   # card headline label
            "quant":          "FP4",          # weight quant chip
            "tps":            52.8,           # bench tok/s (null when un-benched)
            "rtf":            null,           # real-time factor for synth slots
            "used_by":        ["primary"]     # slots bound to this profile
        }

    Raises:
        500 (ConfigParseError): if profiles.toml is present but malformed.
    """
    return [profile.to_dict() for profile in ProfileCatalog().list()]


def _screen_profile_flags(flags: str | None) -> None:
    """Reject hal0-owned flags in a profile's freeform ``flags`` text.

    spec-hw-slot-ownership §5: a profile is a device-agnostic tune template, so
    its ``flags`` must not carry the slot-hardware flags (``-ngl``/``--device``/
    ``--threads``). §21.7: nor the managed args hal0 computes per-slot
    (``--model``/``--host``/``--port``/``--ctx-size``/``--alias``) — a profile
    that persists ``-c``/``--port`` only explodes later when stamped onto a
    model. Symmetric to the model ``defaults.extra_args`` guard in
    :func:`hal0.services.models_service.screen_model_write`; raises the same
    ``slot.hardware_flag_denied`` / ``slot.managed_arg_denied`` BadRequests so
    both surfaces render one path. Empty / unset flags are a no-op; an
    unparseable flag string is left to the ProfileConfig schema to reject.
    """
    if not flags or not flags.strip():
        return
    import shlex

    from hal0.slots.argv import _deny_managed_flags, _deny_slot_hardware_flags

    try:
        tokens = shlex.split(flags)
    except ValueError:
        # Malformed quoting — defer to the pydantic/config layer's own error
        # rather than masking it with a partition message.
        return
    # Hardware first so -ngl (in both sets) gets the "belongs on the slot"
    # message — mirrors screen_model_write's ordering.
    _deny_slot_hardware_flags(tokens, segment="profile flags")
    _deny_managed_flags(tokens, segment="profile flags")


@router.post("", status_code=201)
async def create_profile(body: ProfileBody, request: Request) -> dict[str, Any]:
    """Create a custom profile.

    Returns the created profile item (same shape as list).

    Raises:
        409 profiles.exists: name already exists (seed or custom).
        422: pydantic validation failure (bad name, …).
        400 slot.hardware_flag_denied: flags carry a slot-owned hardware flag.
        400 slot.managed_arg_denied: flags carry a hal0-managed flag.
    """
    # spec-hw-slot-ownership §5 + §21.7: reject slot-hardware and managed
    # flags before persisting.
    _screen_profile_flags(body.flags)
    async with record_action(
        request, category="profile", action="profile.create", target=body.name
    ) as rec:
        profile = ProfileCatalog().create(
            body.name,
            ProfileConfig(
                flags=body.flags,
                mtp=body.mtp,
                device_class=body.device_class,
                backend=body.backend,
                cloned_from=body.cloned_from,
                intent=body.intent,
                quant=body.quant,
            ),
        )
        rec.after = {
            "name": body.name,
            "device_class": body.device_class,
            "backend": body.backend,
        }
    return profile.to_dict()


@router.post("/import")
async def import_profile_route(request: Request) -> dict[str, Any]:
    """Import a profile from an uploaded ``.hal0profile.json`` envelope.

    Body::

        { "envelope": {...}, "name": "name", "dry_run": false }

    ``dry_run`` validates the envelope + checksum and reports whether the target
    name already exists, without creating anything. A commit creates the profile
    under ``name`` and returns the resolved profile item.

    Raises:
        400 profiles.bad_envelope: not a valid hal0.profile envelope.
        400 profiles.import_no_name: commit requested without a name.
        409 profiles.exists: name already exists.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest(
            "request body must be valid JSON",
            code="request.invalid_json",
            details={"error": str(exc)},
        ) from exc
    if not isinstance(body, dict):
        raise BadRequest("request body must be a JSON object", code="request.not_an_object")

    envelope = body.get("envelope", body)
    dry_run = bool(body.get("dry_run", False))
    name = body.get("name")

    if dry_run:
        env = parse_envelope(envelope)
        existing = {p.name for p in ProfileCatalog().list()}
        target = name or env.name
        collides = bool(target) and target in existing
        return {
            "dry_run": True,
            "valid": True,
            "checksum_ok": verify_checksum(envelope) if isinstance(envelope, dict) else False,
            "name": env.name or "",
            "schema_version": env.schema_version,
            "collides": collides,
        }

    if not name or not isinstance(name, str):
        raise BadRequest(
            "import commit requires a 'name'",
            code="profiles.import_no_name",
        )

    async with record_action(
        request, category="profile", action="profile.import", target=name
    ) as rec:
        resolved = import_profile(envelope, name, ProfileCatalog())
        rec.after = {"name": name}
    return {"dry_run": False, "profile": resolved.to_dict()}


@router.get("/{name}")
def get_profile(name: str) -> dict[str, Any]:
    """Resolve a single profile by name.

    Returns the profile item (same shape as list).

    Raises:
        404 profiles.not_found: no such profile.
    """
    return ProfileCatalog().resolve(name).to_dict()


@router.post("/{name}/export")
def export_profile(name: str) -> dict[str, Any]:
    """Serialize a profile into its portable ``.hal0profile.json`` envelope.

    Embeds the profile template only (no secrets, no host paths) and stamps
    ``exported_at`` + a content checksum.

    Raises:
        404 profiles.not_found: no such profile.
    """
    resolved = ProfileCatalog().resolve(name)
    cfg = ProfileConfig(
        flags=resolved.flags,
        mtp=resolved.mtp,
        device_class=resolved.device_class,
        backend=resolved.backend,
        cloned_from=resolved.cloned_from,
        intent=resolved.intent,
        quant=resolved.quant,
    )
    return export_envelope(name, cfg, exported_at=datetime.now(UTC).isoformat())


@router.put("/{name}")
async def update_profile(name: str, body: ProfileUpdateBody, request: Request) -> dict[str, Any]:
    """Update an existing custom profile (shallow merge).

    Returns the updated profile item.

    Raises:
        409 profiles.seed_immutable: name is a seed profile.
        404 profiles.not_found: custom profile not found.
        422: pydantic validation failure.
        400 slot.hardware_flag_denied: flags carry a slot-owned hardware flag.
        400 slot.managed_arg_denied: flags carry a hal0-managed flag.
    """
    # spec-hw-slot-ownership §5 + §21.7: reject slot-hardware and managed
    # flags before persisting.
    _screen_profile_flags(body.flags)
    catalog = ProfileCatalog()
    before = None
    existing = next((p for p in catalog.list() if p.name == name), None)
    if existing is not None:
        before = existing.to_dict()
    async with record_action(
        request,
        category="profile",
        action="profile.update",
        target=name,
        before=before,
    ) as rec:
        profile = catalog.update(
            name,
            ProfilePatch(
                flags=body.flags,
                mtp=body.mtp,
                device_class=body.device_class,
                backend=body.backend,
                intent=body.intent,
                quant=body.quant,
            ),
        )
        rec.after = profile.to_dict()
    return profile.to_dict()


@router.delete("/{name}", status_code=204)
async def delete_profile(name: str, request: Request) -> None:
    """Delete a custom profile.

    Raises:
        409 profiles.seed_immutable: name is a seed profile.
        404 profiles.not_found: custom profile not found.
        409 profiles.in_use: one or more slots reference this profile.
    """
    async with record_action(request, category="profile", action="profile.delete", target=name):
        ProfileCatalog().delete(name)


__all__ = ["router"]
