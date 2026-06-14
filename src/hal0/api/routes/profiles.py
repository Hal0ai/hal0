"""Profile catalog endpoints.

Mounted under /api/profiles:

    GET    ""          — list all profiles
    POST   ""          — create a custom profile (201)
    PUT    "/{name}"   — update a custom profile (200)
    DELETE "/{name}"   — delete a custom profile (204)

Seed profiles (defined in SEED_PROFILES) are immutable via the API.

Write flow delegates to :class:`hal0.profiles.ProfileCatalog`, which owns
seed immutability, duplicate checks, in-use scans, and full-catalog
atomic writes.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator

from hal0.api._audit import record_action
from hal0.config.schema import ProfileConfig
from hal0.profiles import ProfileCatalog, ProfilePatch

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
    image: str = Field(..., description="Container image ref (non-empty).")
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

    @field_validator("image")
    @classmethod
    def image_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("profile image must not be empty")
        return v


class ProfileUpdateBody(BaseModel):
    """Body for PUT /api/profiles/{name} — name is taken from the URL."""

    image: str | None = Field(default=None, description="Container image ref (non-empty).")
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

    @field_validator("image")
    @classmethod
    def image_nonempty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("profile image must not be empty")
        return v


# ── routes ────────────────────────────────────────────────────────────────────


@router.get("")
def list_profiles() -> list[dict[str, Any]]:
    """Return every profile in the catalog as a JSON array.

    Each item shape::

        {
            "name":           "rocm",
            "image":          "ghcr.io/hal0ai/...:rocm-7.2.4-rocmfp4-server",
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


@router.post("", status_code=201)
async def create_profile(body: ProfileBody, request: Request) -> dict[str, Any]:
    """Create a custom profile.

    Returns the created profile item (same shape as list).

    Raises:
        409 profiles.exists: name already exists (seed or custom).
        422: pydantic validation failure (empty image, bad name, …).
    """
    async with record_action(
        request, category="profile", action="profile.create", target=body.name
    ) as rec:
        profile = ProfileCatalog().create(
            body.name,
            ProfileConfig(
                image=body.image,
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
            "image": body.image,
            "device_class": body.device_class,
            "backend": body.backend,
        }
    return profile.to_dict()


@router.put("/{name}")
async def update_profile(name: str, body: ProfileUpdateBody, request: Request) -> dict[str, Any]:
    """Update an existing custom profile (shallow merge).

    Returns the updated profile item.

    Raises:
        409 profiles.seed_immutable: name is a seed profile.
        404 profiles.not_found: custom profile not found.
        422: pydantic validation failure.
    """
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
                image=body.image,
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
