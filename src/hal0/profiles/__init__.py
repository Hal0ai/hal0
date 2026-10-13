"""ProfileCatalog — deep module for runtime profile lookup and mutation.

ADR-0029: a profile is the whole tune a slot launches with — free flags, the
context window, the reasoning default, vision, MTP and an optional runner.
This module concentrates the profile interface:

* seed/custom catalog reads and full-catalog atomic writes;
* seed immutability and duplicate-name checks;
* in-use scans before delete;
* the flag screen every write goes through;
* runtime family and supported slot types.

Routes are adapters over this module; providers and fit checks consume
:class:`ResolvedProfile` instead of re-parsing profiles.toml.
"""

from __future__ import annotations

import logging
import re
import shlex
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from hal0.config import paths
from hal0.config.loader import (
    ConfigNotFound,
    list_slots,
    load_profiles_config,
    load_slot_config,
    read_slot_profile_ref,
    save_profiles_config,
)
from hal0.config.schema import (
    PROFILE_BENCH,
    SEED_PROFILES,
    ProfileConfig,
)
from hal0.errors import Conflict, NotFound, UnprocessableEntity

log = logging.getLogger(__name__)

RuntimeFamily = Literal["llama-server", "flm", "kokoro", "qwen3tts", "moonshine", "comfyui"]
SlotType = Literal["llm", "embedding", "reranking", "transcription", "tts", "image"]

_PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

#: Marks a :class:`ProfilePatch` field the caller left out (``None`` clears).
UNSET: Any = object()


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    """Profile facts after seed/custom lookup and runtime classification."""

    name: str
    flags: str
    seed: bool
    runtime_family: RuntimeFamily
    supported_slot_types: tuple[SlotType, ...]
    ctx_size: int | None = None
    enable_thinking: bool | None = None
    vision: str = "auto"
    mtp: str = "auto"
    runner: str | None = None
    cloned_from: str | None = None
    #: Card display facts. The bench metrics are static for seeds and ``None``
    #: for custom; ``used_by`` is the set of slots that reference this profile.
    intent: str = ""
    tps: float | None = None
    rtf: float | None = None
    used_by: tuple[str, ...] = ()

    @property
    def resolved_flags(self) -> str:
        """The flag text the launch path uses (the profile's own flags)."""
        return self.flags

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "flags": self.flags,
            "resolved_flags": self.flags,
            "ctx_size": self.ctx_size,
            "enable_thinking": self.enable_thinking,
            "vision": self.vision,
            "mtp": self.mtp,
            "runner": self.runner,
            "seed": self.seed,
            "runtime_family": self.runtime_family,
            "supported_slot_types": list(self.supported_slot_types),
            "cloned_from": self.cloned_from,
            "intent": self.intent,
            "tps": self.tps,
            "rtf": self.rtf,
            "used_by": list(self.used_by),
        }


@dataclass(frozen=True, slots=True)
class ProfilePatch:
    """Partial profile update input.

    A field left at :data:`UNSET` keeps the stored value. ``ctx_size=None`` /
    ``enable_thinking=None`` clear the stored value; ``runner=""`` clears the
    runtime back to the slot's own runner (#2186).
    """

    flags: str | None = None
    ctx_size: Any = field(default=UNSET)
    enable_thinking: Any = field(default=UNSET)
    vision: Literal["auto", "off"] | None = None
    mtp: Literal["auto", "off"] | None = None
    runner: str | None = None
    intent: str | None = None


def _runtime_family(name: str, profile: ProfileConfig) -> RuntimeFamily:
    """A profile's runtime family: its ``runner``'s, else the seed slug's.

    ``profile.runner`` is the structural signal (a registry key names exactly
    one runtime). A runner-less profile named after a single-purpose engine's
    seed (flm, kokoro, moonshine, qwen3-tts, comfyui) is that engine; any
    other runner-less profile is llama-server. Converted clones of those
    seeds carry an explicit runner.
    """
    if profile.runner:
        from hal0.runners import RUNNER_IMAGES  # lazy: runners must not import profiles

        runner = RUNNER_IMAGES.get(profile.runner)
        if runner is not None:
            return runner.runtime_family
    if name == "flm":
        return "flm"
    if name == "qwen3-tts":
        return "qwen3tts"
    if name == "kokoro":
        return "kokoro"
    if name == "moonshine":
        return "moonshine"
    if name == "comfyui":
        return "comfyui"
    return "llama-server"


def _supported_slot_types(runtime_family: RuntimeFamily) -> tuple[SlotType, ...]:
    if runtime_family == "flm":
        return ("llm", "embedding", "transcription")
    if runtime_family in ("kokoro", "qwen3tts"):
        return ("tts",)
    if runtime_family == "moonshine":
        return ("transcription",)
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
        used_by = self._used_by_index()
        return [
            self._resolve_item(name, profile, used_by=tuple(used_by.get(name, ())))
            for name, profile in cfg.profile.items()
        ]

    def resolve(self, name: str) -> ResolvedProfile:
        cfg = load_profiles_config(self._path)
        profile = cfg.profile.get(name)
        if profile is None and name in cfg.quarantined:
            raise UnprocessableEntity(
                f"profile {name!r} is invalid as stored and was set aside: "
                f"{cfg.quarantined[name]['reason']}; fix or recreate it "
                "(hal0 profile edit / the Profiles page)",
                code="profiles.invalid",
                details={"profile": name, "reason": cfg.quarantined[name]["reason"]},
            )
        if profile is None:
            raise NotFound(
                f"profile {name!r} not found",
                code="profiles.not_found",
                details={"profile": name, "available": sorted(cfg.profile)},
            )
        return self._resolve_item(name, profile)

    def create(self, name: str, profile: ProfileConfig) -> ResolvedProfile:
        self._validate_name(name)
        screen_profile_flags(profile.flags)
        profile = profile.model_copy(update={"runner": screen_profile_runner(profile.runner)})
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
        if patch.flags is not None:
            screen_profile_flags(patch.flags)
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
                flags=patch.flags if patch.flags is not None else existing.flags,
                ctx_size=existing.ctx_size if patch.ctx_size is UNSET else patch.ctx_size,
                enable_thinking=(
                    existing.enable_thinking
                    if patch.enable_thinking is UNSET
                    else patch.enable_thinking
                ),
                vision=patch.vision if patch.vision is not None else existing.vision,
                mtp=patch.mtp if patch.mtp is not None else existing.mtp,
                runner=_merge_runner(patch.runner, existing.runner),
                cloned_from=existing.cloned_from,
                intent=patch.intent if patch.intent is not None else existing.intent,
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
            in_use_slots, unreadable = self._scan_slots_for(name)
            if in_use_slots:
                raise Conflict(
                    f"profile {name!r} is in use by slot(s): {', '.join(in_use_slots)}",
                    code="profiles.in_use",
                    details={"slots": in_use_slots},
                )
            if unreadable:
                # Fail closed: a slot file nobody can parse may well name this
                # profile, and deleting it would break that slot once it is fixed.
                raise Conflict(
                    f"cannot tell whether profile {name!r} is in use: slot file(s) "
                    f"{', '.join(unreadable)} could not be read; fix or remove them first",
                    code="profiles.slot_unreadable",
                    details={"profile": name, "slots": unreadable},
                )
            del catalog.profile[name]
            save_profiles_config(catalog, self._path)

    def slots_using(self, name: str) -> list[str]:
        """Return slot names whose TOML references ``name``."""
        return self._scan_slots_for(name)[0]

    def _scan_slots_for(self, name: str) -> tuple[list[str], list[str]]:
        """Return ``(slots naming name, unreadable slot stems)``."""
        entries, unreadable = self._scan_slots()
        return [slot for slot, profile in entries if profile == name], unreadable

    def _slot_profiles(self) -> list[tuple[str, str | None]]:
        """Return ``(slot_name, profile_name)`` for every readable slot."""
        return self._scan_slots()[0]

    def _scan_slots(self) -> tuple[list[tuple[str, str | None]], list[str]]:
        """Scan every slot once: ``(entries, unreadable stems)``.

        The reported identity is the display name the TOML embeds, whichever
        stem (name or id) it lives under. A slot that fails the typed load
        (``slot.unconverted``, a stale key) still names its profile in the raw
        TOML, so it is read untyped and counted. Only a file that cannot be
        parsed as TOML at all lands in ``unreadable`` (by stem); it is logged
        and left out of the entries so one bad slot never breaks the profile
        listing, while ``delete`` refuses on it.
        """
        entries: list[tuple[str, str | None]] = []
        unreadable: list[str] = []
        for stem in list_slots():
            try:
                cfg = load_slot_config(stem)
            except Exception as exc:
                log.warning("profiles.in_use_scan_typed_load_failed slot=%s error=%s", stem, exc)
            else:
                entries.append((cfg.name, cfg.profile))
                continue
            try:
                entries.append(read_slot_profile_ref(stem))
            except ConfigNotFound:
                continue  # removed between the directory scan and the read
            except Exception as exc:
                log.warning("profiles.in_use_scan_error slot=%s error=%s", stem, exc)
                unreadable.append(stem)
        return entries, unreadable

    def _used_by_index(self) -> dict[str, list[str]]:
        """Map ``profile_name -> [slot names]`` from a single slot scan."""
        index: dict[str, list[str]] = {}
        for slot_name, profile_name in self._slot_profiles():
            if profile_name:
                index.setdefault(profile_name, []).append(slot_name)
        return index

    def _resolve_item(
        self,
        name: str,
        profile: ProfileConfig,
        *,
        used_by: tuple[str, ...] = (),
    ) -> ResolvedProfile:
        return resolve_loaded_profile(name, profile, used_by=used_by)

    def _guard_custom(self, name: str) -> None:
        if name in SEED_PROFILES:
            # Seed profiles are VIRTUAL — overlaid from SEED_PROFILES in code on
            # every load, so there is nowhere on disk to record an edit or a
            # deletion. Clone to customise.
            raise Conflict(
                f"profile {name!r} is a seed profile — seeds are virtual (re-applied "
                "from code on every load) and cannot be deleted or edited; clone it "
                "under a new name to customise.",
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
    "UNSET",
    "ProfileCatalog",
    "ProfilePatch",
    "ResolvedProfile",
    "RuntimeFamily",
    "SlotType",
    "resolve_loaded_profile",
    "runtime_family_of",
    "screen_profile_flags",
    "screen_profile_runner",
]


def resolve_loaded_profile(
    name: str, profile: ProfileConfig, *, used_by: tuple[str, ...] = ()
) -> ResolvedProfile:
    """Resolve a profile definition the caller already holds, with no I/O."""
    runtime = _runtime_family(name, profile)
    bench = PROFILE_BENCH.get(name, {})
    return ResolvedProfile(
        name=name,
        flags=profile.flags,
        ctx_size=profile.ctx_size,
        enable_thinking=profile.enable_thinking,
        vision=profile.vision,
        mtp=profile.mtp,
        runner=profile.runner,
        seed=name in SEED_PROFILES,
        runtime_family=runtime,
        supported_slot_types=_supported_slot_types(runtime),
        cloned_from=profile.cloned_from,
        intent=profile.intent,
        tps=bench.get("tps"),
        rtf=bench.get("rtf"),
        used_by=used_by,
    )


def runtime_family_of(name: str, profile: ProfileConfig) -> RuntimeFamily:
    """Public seam for :func:`_runtime_family` (a profile not yet in the catalog)."""
    return _runtime_family(name, profile)


def _merge_runner(patched: str | None, stored: str | None) -> str | None:
    """Resolve an update's ``runner`` against the profile's stored value.

    ``None`` keeps the stored runtime; ``""`` clears it (#2186); the stored
    value passes unscreened (a key may leave the registry under the profile
    that stores it, #2183); anything else is screened as on create.
    """
    if patched is None:
        return stored
    if not patched.strip():
        return None
    if patched == stored:
        return stored
    return screen_profile_runner(patched)


def screen_profile_runner(runner: str | None) -> str | None:
    """Validate + canonicalize a profile's runner key. None/blank passes.

    ``runner`` is a RUNNER_IMAGES registry key, never an image ref. Shared by
    the catalog write seam and the portable import path.
    """
    if runner is None or not runner.strip():
        return None
    from hal0.runners import RUNNER_IMAGES

    key = runner.strip()
    if key not in RUNNER_IMAGES:
        raise UnprocessableEntity(
            f"unknown runtime {runner!r}",
            code="profiles.unknown_runner",
            details={"runner": runner, "available": sorted(RUNNER_IMAGES)},
        )
    return key


def screen_profile_flags(flags: str | None) -> None:
    """Reject every flag a profile may not carry, before it is persisted.

    The catalog is the write seam (routes, import, CLI). A profile may not set
    hardware flags (the slot's typed fields), hal0-managed flags (including
    ``--ctx-size``: use ``ctx_size``), model-fact flags (``--jinja``,
    ``--chat-template*``, ``--mmproj*``, ``--no-mmproj*``),
    ``--spec-draft-device`` (the slot's device decides it), a host-owned flag
    (remote model fetch, host file, listener, auth/TLS), a bare positional or
    ``--reasoning-format none`` (#2545), in any spelling. The text must be
    valid shell text.
    """
    if not flags or not flags.strip():
        return
    try:
        tokens = shlex.split(flags)
    except ValueError as exc:
        raise UnprocessableEntity(
            f"profile flags are not valid shell text: {exc}",
            code="profiles.flags_malformed",
            details={"flags": flags},
        ) from exc
    from hal0.slots.argv import deny_reasoning_format_none, screen_profile_segment

    # The launch-time screen (hardware, managed, model-fact, host-owned flags
    # and bare positionals, in every spelling), plus #2545 on the write side.
    deny_reasoning_format_none(tokens, segment="profile flags")
    screen_profile_segment(tokens, segment="profile flags")
