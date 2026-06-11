"""Profile catalog endpoints.

Mounted under /api/profiles:

    GET    ""          — list all profiles
    POST   ""          — create a custom profile (201)
    PUT    "/{name}"   — update a custom profile (200)
    DELETE "/{name}"   — delete a custom profile (204)

Seed profiles (defined in SEED_PROFILES) are immutable via the API.

Write flow: load_profiles_config() → guard → mutate catalog.profile →
save_profiles_config(catalog).  save_profiles_config writes the full
catalog atomically, so seeds MUST be included on every write (the caller
starts from load_profiles_config() which returns seeds when no file
exists, then adds/changes the custom entry).
"""

from __future__ import annotations

import re
import threading
from typing import Any, Literal

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from hal0.api.middleware.error_codes import Conflict, NotFound
from hal0.config.loader import (
    list_slots,
    load_profiles_config,
    load_slot_config,
    save_profiles_config,
)
from hal0.config.schema import SEED_PROFILES, ProfileConfig, resolve_profile_flags

log = structlog.get_logger(__name__)

router = APIRouter()

#: Mirror of manager._SLOT_NAME_RE — kebab-case, leading alphanumeric, ≤32 chars.
_PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

#: Serialises the load→mutate→save sections below. save_profiles_config is a
#: full-catalog REPLACE write, so two concurrent writers racing past the same
#: load_profiles_config() would silently drop one writer's change — a lost
#: update here means data loss (the entire entry vanishes from disk). The
#: operator surface is low-QPS, but the fix is five lines. Routes are sync
#: (FastAPI runs them in the threadpool), so a plain threading.Lock suffices.
_CATALOG_LOCK = threading.Lock()


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

    @field_validator("name")
    @classmethod
    def name_kebab(cls, v: str) -> str:
        if not _PROFILE_NAME_RE.match(v):
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

    @field_validator("image")
    @classmethod
    def image_nonempty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("profile image must not be empty")
        return v


# ── serializer ────────────────────────────────────────────────────────────────


def _serialize(name: str, p: ProfileConfig) -> dict[str, Any]:
    return {
        "name": name,
        "image": p.image,
        "flags": p.flags,
        "mtp": p.mtp,
        "device_class": p.device_class,
        "resolved_flags": resolve_profile_flags(p),
        "seed": name in SEED_PROFILES,
    }


# ── in-use scan ───────────────────────────────────────────────────────────────


def _slots_using_profile(profile_name: str) -> list[str]:
    """Return slot names whose TOML has profile=<profile_name>.

    Uses the synchronous list_slots() + load_slot_config() from
    hal0.config.loader — the same source the slots list route delegates to
    via iter_configs().  Errors loading individual slot TOMLs are logged and
    skipped so a malformed slot doesn't permanently block profile deletion —
    the warning keeps the orphaned-reference case diagnosable.
    """
    using: list[str] = []
    for slot_name in list_slots():
        try:
            cfg = load_slot_config(slot_name)
        except Exception as exc:
            log.warning("profiles.in_use_scan_error", slot=slot_name, error=str(exc))
            continue
        if cfg.profile == profile_name:
            using.append(slot_name)
    return using


# ── routes ────────────────────────────────────────────────────────────────────


@router.get("")
def list_profiles() -> list[dict[str, Any]]:
    """Return every profile in the catalog as a JSON array.

    Each item shape::

        {
            "name":           "moe-rocmfp4",
            "image":          "ghcr.io/hal0ai/...:rocm-7.2.4-rocmfp4-server",
            "flags":          "-fa on ...",
            "mtp":            false,
            "device_class":   "gpu",         # gpu | cpu | npu | img
            "resolved_flags": "-fa on ..."   # flags + MTP bundle when mtp=true
        }

    Raises:
        500 (ConfigParseError): if profiles.toml is present but malformed.
    """
    cfg = load_profiles_config()
    return [_serialize(name, p) for name, p in cfg.profile.items()]


@router.post("", status_code=201)
def create_profile(body: ProfileBody) -> dict[str, Any]:
    """Create a custom profile.

    Returns the created profile item (same shape as list).

    Raises:
        409 profiles.exists: name already exists (seed or custom).
        422: pydantic validation failure (empty image, bad name, …).
    """
    with _CATALOG_LOCK:
        catalog = load_profiles_config()
        if body.name in catalog.profile:
            raise Conflict(
                f"profile {body.name!r} already exists",
                code="profiles.exists",
            )
        new_profile = ProfileConfig(
            image=body.image,
            flags=body.flags,
            mtp=body.mtp,
            device_class=body.device_class,
        )
        catalog.profile[body.name] = new_profile
        save_profiles_config(catalog)
    return _serialize(body.name, new_profile)


@router.put("/{name}")
def update_profile(name: str, body: ProfileUpdateBody) -> dict[str, Any]:
    """Update an existing custom profile (shallow merge).

    Returns the updated profile item.

    Raises:
        409 profiles.seed_immutable: name is a seed profile.
        404 profiles.not_found: custom profile not found.
        422: pydantic validation failure.
    """
    if name in SEED_PROFILES:
        raise Conflict(
            f"profile {name!r} is a seed profile — seed profiles are immutable; "
            "clone under a new name",
            code="profiles.seed_immutable",
        )
    with _CATALOG_LOCK:
        catalog = load_profiles_config()
        if name not in catalog.profile:
            raise NotFound(
                f"profile {name!r} not found",
                code="profiles.not_found",
            )
        existing = catalog.profile[name]
        updated = ProfileConfig(
            image=body.image if body.image is not None else existing.image,
            flags=body.flags if body.flags is not None else existing.flags,
            mtp=body.mtp if body.mtp is not None else existing.mtp,
            device_class=(
                body.device_class if body.device_class is not None else existing.device_class
            ),
        )
        catalog.profile[name] = updated
        save_profiles_config(catalog)
    return _serialize(name, updated)


@router.delete("/{name}", status_code=204)
def delete_profile(name: str) -> None:
    """Delete a custom profile.

    Raises:
        409 profiles.seed_immutable: name is a seed profile.
        404 profiles.not_found: custom profile not found.
        409 profiles.in_use: one or more slots reference this profile.
    """
    if name in SEED_PROFILES:
        raise Conflict(
            f"profile {name!r} is a seed profile — seed profiles are immutable; "
            "clone under a new name",
            code="profiles.seed_immutable",
        )
    with _CATALOG_LOCK:
        catalog = load_profiles_config()
        if name not in catalog.profile:
            raise NotFound(
                f"profile {name!r} not found",
                code="profiles.not_found",
            )
        in_use = _slots_using_profile(name)
        if in_use:
            raise Conflict(
                f"profile {name!r} is in use by slot(s): {', '.join(in_use)}",
                code="profiles.in_use",
                details={"slots": in_use},
            )
        del catalog.profile[name]
        save_profiles_config(catalog)


__all__ = ["router"]
