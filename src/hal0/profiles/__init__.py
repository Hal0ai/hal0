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
import shlex
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
from hal0.config.schema import (
    PROFILE_BENCH,
    SEED_PROFILES,
    ProfileConfig,
    resolve_profile_flags,
)
from hal0.errors import Conflict, NotFound

log = logging.getLogger(__name__)

RuntimeFamily = Literal["llama-server", "flm", "kokoro", "qwen3tts", "comfyui"]
SlotType = Literal["llm", "embedding", "reranking", "transcription", "tts", "image"]

_PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    """Profile facts after seed/custom lookup and runtime classification."""

    name: str
    flags: str
    mtp: bool
    device_class: str
    resolved_flags: str
    seed: bool
    runtime_family: RuntimeFamily
    supported_slot_types: tuple[SlotType, ...]
    backend: str | None = None
    cloned_from: str | None = None
    #: Card display facts (profiles overhaul). The bench metrics are static
    #: for seeds and ``None`` for custom; ``used_by`` is the set of slots
    #: that reference this profile.
    intent: str = ""
    quant: str = ""
    tps: float | None = None
    rtf: float | None = None
    used_by: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "flags": self.flags,
            "mtp": self.mtp,
            "device_class": self.device_class,
            "backend": self.backend,
            "resolved_flags": self.resolved_flags,
            "seed": self.seed,
            "runtime_family": self.runtime_family,
            "supported_slot_types": list(self.supported_slot_types),
            "cloned_from": self.cloned_from,
            "intent": self.intent,
            "quant": self.quant,
            "tps": self.tps,
            "rtf": self.rtf,
            "used_by": list(self.used_by),
        }


@dataclass(frozen=True, slots=True)
class ProfilePatch:
    """Partial profile update input."""

    flags: str | None = None
    mtp: bool | None = None
    device_class: Literal["gpu", "cpu", "npu", "img"] | None = None
    backend: Literal["rocm", "vulkan"] | None = None
    intent: str | None = None
    quant: str | None = None


def _runtime_family(name: str, profile: ProfileConfig) -> RuntimeFamily:
    """Classify a profile's runtime family from its TYPED fields — the profile
    ``name`` + ``device_class`` — never an image string.

    spec-hw-slot-ownership §3: profiles carry no ``image`` anymore, so the old
    exact-image→``RUNNER_IMAGES`` lookup and the image-substring sniffs are gone.
    The runtime family is a structural fact: ``device_class`` pins the
    single-purpose runtimes (``img`` → comfyui, ``npu`` → flm), and the two TTS
    engines (kokoro / qwen3tts) — which share a ``cpu`` device_class with a plain
    llama-server CPU profile and so can only be told apart by name — key off
    their seed slug. Mirrors the model-side backends-driven classification in
    :func:`hal0.model_meta.modality.derive_modalities` (a structural signal, not
    a substring guess). Custom (cloned) profiles have no special-runtime signal
    and resolve to ``llama-server`` — the single-purpose runtimes are seed-only.
    """
    if name == "flm" or profile.device_class == "npu":
        return "flm"
    if name == "qwen3-tts":
        return "qwen3tts"
    if name == "kokoro":
        return "kokoro"
    if name == "comfyui" or profile.device_class == "img":
        return "comfyui"
    return "llama-server"


def _supported_slot_types(runtime_family: RuntimeFamily) -> tuple[SlotType, ...]:
    if runtime_family == "flm":
        return ("llm", "embedding", "transcription")
    if runtime_family in ("kokoro", "qwen3tts"):
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
        used_by = self._used_by_index()
        return [
            self._resolve_item(name, profile, used_by=tuple(used_by.get(name, ())))
            for name, profile in cfg.profile.items()
        ]

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
        # A create has no stored baseline, so §5/§21.7 stay a hard reject here —
        # only `update` grandfathers (see screen_profile_flags).
        screen_profile_flags(profile.flags)
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
            # #1411: screened INSIDE the lock, after `existing` is loaded, so the
            # profile's own stored hardware flags can be grandfathered. Screening
            # before the read (as this did) had no baseline to compare against,
            # which is what made every pre-guard profile un-editable.
            if patch.flags is not None:
                screen_profile_flags(patch.flags, grandfathered=existing.flags)
            updated = ProfileConfig(
                flags=patch.flags if patch.flags is not None else existing.flags,
                mtp=patch.mtp if patch.mtp is not None else existing.mtp,
                device_class=(
                    patch.device_class if patch.device_class is not None else existing.device_class
                ),
                backend=patch.backend if patch.backend is not None else existing.backend,
                cloned_from=existing.cloned_from,
                intent=patch.intent if patch.intent is not None else existing.intent,
                quant=patch.quant if patch.quant is not None else existing.quant,
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
        return [slot for slot, profile in self._slot_profiles() if profile == name]

    def _slot_profiles(self) -> list[tuple[str, str | None]]:
        """Return ``(slot_name, profile_name)`` for every slot, in one pass.

        Malformed slot TOMLs are logged and skipped so a single bad slot
        never breaks the whole profile listing.

        id-aware (P3-runtime-db inc4): ``list_slots()`` enumerates on-disk
        *stems*, which on an id-keyed box are digit ids, not display names.
        ``load_slot_config`` still needs the raw stem to find the file, but
        the reported slot identity is always ``cfg.name`` — the real display
        name a bilingual TOML embeds regardless of which stem it lives under
        (a name-keyed TOML's stem already IS its name, so this is a no-op
        there).
        """
        out: list[tuple[str, str | None]] = []
        for slot_name in list_slots():
            try:
                cfg = load_slot_config(slot_name)
            except Exception as exc:
                log.warning("profiles.in_use_scan_error slot=%s error=%s", slot_name, exc)
                continue
            out.append((cfg.name, cfg.profile))
        return out

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
        runtime = _runtime_family(name, profile)
        bench = PROFILE_BENCH.get(name, {})
        return ResolvedProfile(
            name=name,
            flags=profile.flags,
            mtp=profile.mtp,
            device_class=profile.device_class,
            backend=profile.backend,
            resolved_flags=resolve_profile_flags(profile),
            seed=name in SEED_PROFILES,
            runtime_family=runtime,
            supported_slot_types=_supported_slot_types(runtime),
            cloned_from=profile.cloned_from,
            intent=profile.intent,
            quant=profile.quant,
            tps=bench.get("tps"),
            rtf=bench.get("rtf"),
            used_by=used_by,
        )

    def _guard_custom(self, name: str) -> None:
        if name in SEED_PROFILES:
            # Seed profiles are VIRTUAL — overlaid from SEED_PROFILES in code on
            # every load (see loader.load_profiles_config), so there is nowhere on
            # disk to record a deletion: a force-delete would reappear on the next
            # load. There is deliberately no --force here; clone to customise, or
            # disable the slot that uses it via capabilities.toml.
            raise Conflict(
                f"profile {name!r} is a seed profile — seeds are virtual (re-applied "
                "from code on every load) and cannot be deleted or edited; clone it "
                "under a new name to customise, or disable the slot in capabilities.toml.",
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
    "screen_profile_flags",
]


def screen_profile_flags(flags: str | None, *, grandfathered: str | None = None) -> None:
    """Reject physical slot flags and hal0-managed flags before a profile is persisted.

    The catalog is the actual write seam — routes, import and CLI paths all
    funnel through :meth:`ProfileCatalog.create` / :meth:`ProfileCatalog.update`
    — so the guard lives here and the HTTP layer delegates to it rather than
    keeping a second copy. Enforces the model/profile ownership partition
    (§5 hardware flags + §21.7 managed args). Empty/unset ``flags`` is a no-op;
    malformed quoting is left to the schema layer's own diagnostics.

    ``grandfathered`` is the profile's CURRENTLY STORED flag text on an update
    (#1411). The §5 screen shipped with no data migration, so every custom
    profile authored before it carried ``-dev``/``--threads`` and failed its own
    round-trip: loading it in the drawer and pressing Save — which re-sends the
    stored text verbatim — 400'd on the profile's own data. On lxc105 that was
    10 of 10 pre-existing custom profiles, five of them bound to live slots, so
    the Profiles page saved nothing at all.

    The screen therefore judges what an update INTRODUCES, not what it inherits:
    a slot-hardware flag already present in ``grandfathered`` passes (logged, not
    silently ignored), while adding a new one still hard-rejects. That keeps the
    policy intact going forward, keeps the migration path open (dropping the
    inherited flag is a normal save, after which re-adding it is a new reach), and —
    unlike stripping the flags on read — never silently rewrites an operator's
    working device selection out from under a live slot. Grandfathering covers
    the §5 hardware set ONLY: #1404's load-path sanitizer already strips the
    §21.7 managed args from stored profiles, so a managed flag can never be
    inherited in the first place.
    """
    if not flags or not flags.strip():
        return
    try:
        tokens = shlex.split(flags)
    except ValueError:
        return  # schema/parser owns malformed quoting diagnostics
    from hal0.slots.argv import (
        SLOT_HARDWARE_FLAGS,
        _canon,
        _deny_managed_flags,
        _deny_slot_hardware_flags,
        strip_managed_flags,
    )

    inherited: list[str] = []
    if grandfathered and grandfathered.strip():
        try:
            prior_tokens = shlex.split(grandfathered)
        except ValueError:
            prior_tokens = []
        # `strip_managed_flags` doubles as the finder here: its `removed` list is
        # exactly "which flags from this denylist are present", canonicalised and
        # in original spelling. Reusing it keeps ONE token-matching implementation.
        _, inherited = strip_managed_flags(prior_tokens, denylist=SLOT_HARDWARE_FLAGS)
    if inherited:
        # Drop the inherited flags (and their values) from what gets screened, so
        # only the genuinely NEW reaches reach the deny helpers — which keeps
        # their message and `details.flags` naming exactly the offending subset.
        tokens, _ = strip_managed_flags(tokens, denylist=frozenset(_canon(f) for f in inherited))
        log.info(
            "profile flags carry pre-existing slot-hardware flag(s); grandfathered on update",
            extra={"event": "profile.hardware_flags_grandfathered", "flags": inherited},
        )

    # Hardware first so -ngl (in both sets) gets the "belongs on the slot"
    # message — mirrors the route/screen_model_write ordering.
    _deny_slot_hardware_flags(tokens, segment="profile flags")
    _deny_managed_flags(tokens, segment="profile flags")
