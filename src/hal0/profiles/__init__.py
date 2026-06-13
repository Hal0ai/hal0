"""ProfileCatalog — deep module for runtime profile lookup and mutation.

A profile is no longer just an image string plus flags. It describes a
runtime template that affects whether a slot/model/device combination is
runnable. This module concentrates the profile interface:

* seed/custom catalog reads and full-catalog atomic writes;
* seed immutability and duplicate-name checks;
* in-use scans before delete;
* resolved flags, runtime family, and supported slot types.

Routes are adapters over this module; providers and fit checks should
consume :class:`ResolvedProfile` instead of re-parsing profiles.toml.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hal0.config import paths
from hal0.config.loader import (
    list_slots,
    load_profiles_config,
    load_slot_config,
    save_profiles_config,
)
from hal0.config.schema import SEED_PROFILES, ProfileConfig, resolve_profile_flags
from hal0.errors import Conflict, NotFound

log = logging.getLogger(__name__)

RuntimeFamily = Literal["llama-server", "flm", "kokoro", "comfyui"]
SlotType = Literal["llm", "embedding", "reranking", "transcription", "tts", "image"]

_PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    """Profile facts after seed/custom lookup and runtime classification."""

    name: str
    image: str
    flags: str
    mtp: bool
    device_class: str
    resolved_flags: str
    seed: bool
    runtime_family: RuntimeFamily
    supported_slot_types: tuple[SlotType, ...]
    backend: str | None = None
    cloned_from: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "image": self.image,
            "flags": self.flags,
            "mtp": self.mtp,
            "device_class": self.device_class,
            "backend": self.backend,
            "resolved_flags": self.resolved_flags,
            "seed": self.seed,
            "runtime_family": self.runtime_family,
            "supported_slot_types": list(self.supported_slot_types),
            "cloned_from": self.cloned_from,
        }


@dataclass(frozen=True, slots=True)
class ProfilePatch:
    """Partial profile update input."""

    image: str | None = None
    flags: str | None = None
    mtp: bool | None = None
    device_class: Literal["gpu", "cpu", "npu", "img"] | None = None
    backend: Literal["rocm", "vulkan"] | None = None


def _runtime_family(name: str, profile: ProfileConfig) -> RuntimeFamily:
    # Classify by device_class/image (robust to slug renames); the legacy
    # name literals are kept as a belt-and-suspenders hint.
    image = profile.image.lower()
    if name == "flm" or profile.device_class == "npu" or "flm" in image:
        return "flm"
    if name == "tts" or "kokoro" in image:
        return "kokoro"
    if name == "comfyui" or profile.device_class == "img" or "comfyui" in image:
        return "comfyui"
    return "llama-server"


def _supported_slot_types(runtime_family: RuntimeFamily) -> tuple[SlotType, ...]:
    if runtime_family == "flm":
        return ("llm", "embedding", "transcription")
    if runtime_family == "kokoro":
        return ("tts",)
    if runtime_family == "comfyui":
        return ("image",)
    return ("llm", "embedding", "reranking")


class ProfileCatalog:
    """Read and mutate the profile catalog through one interface."""

    def __init__(self, *, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()

    def _path_or_default(self) -> Path:
        return self._path or paths.profiles_toml()

    def list(self) -> list[ResolvedProfile]:
        cfg = load_profiles_config(self._path)
        return [self._resolve_item(name, profile) for name, profile in cfg.profile.items()]

    def resolve(self, name: str) -> ResolvedProfile:
        cfg = load_profiles_config(self._path)
        profile = cfg.profile.get(name)
        if profile is None:
            raise NotFound(
                f"profile {name!r} not found",
                code="profiles.not_found",
                details={"profile": name, "available": sorted(cfg.profile)},
            )
        return self._resolve_item(name, profile)

    def create(self, name: str, profile: ProfileConfig) -> ResolvedProfile:
        self._validate_name(name)
        with self._lock:
            catalog = load_profiles_config(self._path)
            if name in catalog.profile:
                raise Conflict(
                    f"profile {name!r} already exists",
                    code="profiles.exists",
                    details={"profile": name},
                )
            catalog.profile[name] = profile
            save_profiles_config(catalog, self._path)
        return self._resolve_item(name, profile)

    def update(self, name: str, patch: ProfilePatch) -> ResolvedProfile:
        self._guard_custom(name)
        with self._lock:
            catalog = load_profiles_config(self._path)
            existing = catalog.profile.get(name)
            if existing is None:
                raise NotFound(
                    f"profile {name!r} not found",
                    code="profiles.not_found",
                    details={"profile": name},
                )
            updated = ProfileConfig(
                image=patch.image if patch.image is not None else existing.image,
                flags=patch.flags if patch.flags is not None else existing.flags,
                mtp=patch.mtp if patch.mtp is not None else existing.mtp,
                device_class=(
                    patch.device_class if patch.device_class is not None else existing.device_class
                ),
                backend=patch.backend if patch.backend is not None else existing.backend,
                cloned_from=existing.cloned_from,
            )
            catalog.profile[name] = updated
            save_profiles_config(catalog, self._path)
        return self._resolve_item(name, updated)

    def delete(self, name: str) -> None:
        self._guard_custom(name)
        with self._lock:
            catalog = load_profiles_config(self._path)
            if name not in catalog.profile:
                raise NotFound(
                    f"profile {name!r} not found",
                    code="profiles.not_found",
                    details={"profile": name},
                )
            in_use = self.slots_using(name)
            if in_use:
                raise Conflict(
                    f"profile {name!r} is in use by slot(s): {', '.join(in_use)}",
                    code="profiles.in_use",
                    details={"slots": in_use},
                )
            del catalog.profile[name]
            save_profiles_config(catalog, self._path)

    def slots_using(self, name: str) -> list[str]:
        """Return slot names whose TOML references ``name``."""
        using: list[str] = []
        for slot_name in list_slots():
            try:
                cfg = load_slot_config(slot_name)
            except Exception as exc:
                log.warning("profiles.in_use_scan_error slot=%s error=%s", slot_name, exc)
                continue
            if cfg.profile == name:
                using.append(slot_name)
        return using

    def _resolve_item(self, name: str, profile: ProfileConfig) -> ResolvedProfile:
        runtime = _runtime_family(name, profile)
        return ResolvedProfile(
            name=name,
            image=profile.image,
            flags=profile.flags,
            mtp=profile.mtp,
            device_class=profile.device_class,
            backend=profile.backend,
            resolved_flags=resolve_profile_flags(profile),
            seed=name in SEED_PROFILES,
            runtime_family=runtime,
            supported_slot_types=_supported_slot_types(runtime),
            cloned_from=profile.cloned_from,
        )

    def _guard_custom(self, name: str) -> None:
        if name in SEED_PROFILES:
            raise Conflict(
                f"profile {name!r} is a seed profile — seed profiles are immutable; "
                "clone under a new name",
                code="profiles.seed_immutable",
                details={"profile": name},
            )

    def _validate_name(self, name: str) -> None:
        if not _PROFILE_NAME_RE.match(name):
            raise Conflict(
                "profile name must be kebab-case (a-z0-9_-), ≤32 chars, start with alphanumeric",
                code="profiles.invalid_name",
                details={"profile": name},
            )


__all__ = [
    "ProfileCatalog",
    "ProfilePatch",
    "ResolvedProfile",
    "RuntimeFamily",
    "SlotType",
]
