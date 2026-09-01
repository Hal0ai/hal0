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
from hal0.errors import Conflict, NotFound, UnprocessableEntity

log = logging.getLogger(__name__)

RuntimeFamily = Literal["llama-server", "flm", "kokoro", "qwen3tts", "moonshine", "comfyui"]
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
    runner: str | None = None
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
            "runner": self.runner,
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
    #: Mirrors ProfileConfig.backend, which accepts "cuda" too. Omitting it here
    #: made a CUDA profile un-patchable through this seam.
    backend: Literal["rocm", "vulkan", "cuda"] | None = None
    #: None = leave unchanged (as everywhere else on this class); ``""`` =
    #: CLEAR the stored runtime back to Auto (#2186). ``runner`` is the one
    #: field here with a natural empty value — "no runtime pinned" is a real,
    #: reachable state (every seed is in it), unlike ``backend``/``device_class``
    #: whose None already means "unset" — so a clear needs a wire value that is
    #: not None, and the empty string is the one the select control already
    #: produces. Without it the drawer's "— Auto —" option sent None and the
    #: stored runtime silently survived the save.
    runner: str | None = None
    intent: str | None = None
    quant: str | None = None


def _runtime_family(name: str, profile: ProfileConfig) -> RuntimeFamily:
    """Classify a profile's runtime family from its TYPED fields, in the
    precedence ``runner`` > ``device_class`` > profile ``name`` — never an
    image string.

    ``profile.runner`` (runtime-cascade D4) is the structural signal: a
    registry key names exactly one runtime, so its ``Runner.runtime_family``
    is the answer whenever one is stored. That makes the family a property of
    the runtime the profile selects rather than of the slug it happens to
    carry, so a custom profile can be any family — the single-purpose runtimes
    are no longer seed-only.

    Everything below the runner check is the PRE-runner fallback, kept for the
    (still-common) runner-less profile and for a stored key that is no longer
    in the registry. spec-hw-slot-ownership §3: profiles carry no ``image``
    anymore, so the old exact-image→``RUNNER_IMAGES`` lookup and the
    image-substring sniffs are gone. ``device_class`` pins the single-purpose
    runtimes (``img`` → comfyui, ``npu`` → flm), and the name-keyed CPU
    engines — the two TTS engines (kokoro / qwen3tts) plus the moonshine STT
    engine, which share a ``cpu`` device_class with a plain llama-server CPU
    profile and so can only be told apart by name — key off their seed slug.
    Mirrors the model-side backends-driven classification in
    :func:`hal0.model_meta.modality.derive_modalities` (a structural signal, not
    a substring guess). A runner-less custom (cloned) profile has no
    special-runtime signal and resolves to ``llama-server``.
    """
    if profile.runner:
        from hal0.runners import RUNNER_IMAGES  # lazy: runners must not import profiles

        runner = RUNNER_IMAGES.get(profile.runner)
        if runner is not None:
            return runner.runtime_family
    if name == "flm" or profile.device_class == "npu":
        return "flm"
    if name == "qwen3-tts":
        return "qwen3tts"
    if name == "kokoro":
        return "kokoro"
    if name == "moonshine":
        return "moonshine"
    if name == "comfyui" or profile.device_class == "img":
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
        if profile is None:
            profile = self._materialize_legacy(name)
        if profile is None:
            raise NotFound(
                f"profile {name!r} not found",
                code="profiles.not_found",
                details={"profile": name, "available": sorted(cfg.profile)},
            )
        return self._resolve_item(name, profile)

    def _materialize_legacy(self, name: str) -> ProfileConfig | None:
        """Adopt a demoted (ex-seed) definition on first reference, or None.

        ``loader._demote_legacy_seeds`` covers every install that HAS a
        profiles.toml. This is for the one state it structurally cannot: a
        FRESH install, where there is no file at all and the migration is a
        deliberate no-op, yet the shipped configuration still names demoted
        profiles — install.sh copies
        ``installer/etc-hal0/slots/agent.toml`` (``chadrock-moe``) and
        ``coder.toml`` (``coding``) verbatim, and the ``saber`` seed stack's
        agent slot asks for ``moe``. Without this the curated slots on a
        brand-new box would reference nothing.

        The gate is load-bearing, not an optimisation: a legacy name the
        install has already settled and that is now missing from the catalog
        was DELETED, and re-materializing it would resurrect it on the next
        read — precisely the virtual-seed behaviour the demotion exists to
        escape. But "settled" is a fact about a NAME, not about the install:
        the gate used to be ``profiles.toml exists``, which the first adoption
        itself makes true, so whichever of the shipped references resolved
        first won and every other one fell back to the device default. A fresh
        box ships three (agent.toml → chadrock-moe, coder.toml → coding, the
        saber stack → moe), so the outcome depended on launch order.

        ``ProfilesConfig.adopted_legacy_names`` is the real question: empty on
        a fresh install (adopt freely, once each), every legacy name after the
        bulk demotion has run (adopt nothing — absence is a deletion), and
        growing by one on each adoption here.

        Materializing (rather than answering read-only) is what leaves the
        entry editable and deletable afterwards, exactly like a demoted one. A
        write failure is not fatal: the caller still gets the definition and
        the next reference retries.
        """
        from hal0.config.schema import LEGACY_SEED_PROFILES

        entry = LEGACY_SEED_PROFILES.get(name)
        if entry is None:
            return None
        profile = ProfileConfig.model_validate(entry)
        with self._lock:
            catalog = load_profiles_config(self._path)
            adopted = catalog.adopted_legacy_names()
            if name in adopted:
                return None
            catalog.profile[name] = profile
            catalog.legacy_seeds_adopted = sorted(adopted | {name})
            try:
                save_profiles_config(catalog, self._path)
            except OSError as exc:
                log.warning("profiles.legacy_materialize_write_failed name=%s error=%s", name, exc)
        return profile

    def create(self, name: str, profile: ProfileConfig) -> ResolvedProfile:
        self._validate_name(name)
        # A create has no stored baseline, so §5/§21.7 stay a hard reject here —
        # only `update` grandfathers (see screen_profile_flags).
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
                runner=_merge_runner(patch.runner, existing.runner),
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
            in_use_slots = self.slots_using(name)
            in_use_models = self.models_using(name)
            if in_use_slots or in_use_models:
                clauses = []
                if in_use_slots:
                    clauses.append(f"slot(s): {', '.join(in_use_slots)}")
                if in_use_models:
                    clauses.append(f"model(s): {', '.join(in_use_models)}")
                raise Conflict(
                    f"profile {name!r} is in use by " + " and ".join(clauses),
                    code="profiles.in_use",
                    details={"slots": in_use_slots, "models": in_use_models},
                )
            del catalog.profile[name]
            # A deleted legacy name is settled whether or not it was ever
            # adopted here — otherwise deleting one the operator had created
            # themselves (under a legacy name, on a fresh install) would leave
            # it eligible for adoption and the next resolve would hand back the
            # shipped definition in its place.
            from hal0.config.schema import LEGACY_SEED_PROFILES

            if name in LEGACY_SEED_PROFILES:
                catalog.legacy_seeds_adopted = sorted(catalog.adopted_legacy_names() | {name})
            save_profiles_config(catalog, self._path)

    def slots_using(self, name: str) -> list[str]:
        """Return slot names whose TOML references ``name``."""
        return [slot for slot, profile in self._slot_profiles() if profile == name]

    def models_using(self, name: str) -> list[str]:
        """Return model ids whose ``defaults.profile`` references ``name``.

        HAL0-41 / GH #1437: ``slots_using`` alone missed a model that
        prefers a profile via ``defaults.profile`` but isn't (yet) bound to
        any slot — deleting the profile out from under it left a dangling
        reference. Errors reading the registry are logged and treated as
        "no models found" so a registry hiccup never blocks a slot-only
        delete, mirroring ``_slot_profiles``'s per-slot error tolerance.
        """
        from hal0.registry.store import ModelRegistry

        try:
            models = ModelRegistry().list()
        except Exception as exc:
            log.warning("profiles.in_use_model_scan_error error=%s", exc)
            return []
        return [model.id for model in models if model.defaults and model.defaults.profile == name]

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
            runner=profile.runner,
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
    "runtime_family_of",
    "screen_profile_flags",
    "screen_profile_runner",
]


def runtime_family_of(name: str, profile: ProfileConfig) -> RuntimeFamily:
    """Public seam for :func:`_runtime_family` — the runtime family a profile
    resolves to, for callers that hold a ProfileConfig that is not (yet) in
    the catalog. The import dry run reports it for an envelope that has not
    been created.
    """
    return _runtime_family(name, profile)


def _merge_runner(patched: str | None, stored: str | None) -> str | None:
    """Resolve an update's ``runner`` against the profile's stored value.

    The three wire cases (#2186), in order:

    * ``None`` — the field was omitted: leave the stored runtime alone.
    * ``""`` — the explicit CLEAR sentinel: back to Auto (no runtime pinned).
      Never screened: dropping a runtime is always a legal state, and this is
      the only in-product way off a key the registry no longer carries.
    * the value already stored — grandfathered through unscreened (#2183).
    * anything else — a real key, screened and canonicalized as on create.

    The grandfather clause mirrors ``screen_profile_flags``' (#1411): a runner
    key can leave ``RUNNER_IMAGES`` under the profile that stores it — a runtime
    renamed or dropped by a build, or a downgrade — and the drawer re-sends the
    stored runner verbatim on every save, so screening an UNCHANGED value 422'd
    a write that changes nothing and made the profile un-editable in EVERY
    field. The screen judges what an update INTRODUCES, not what it inherits;
    any actual change (including one unknown key for another) is still fully
    screened, and `create` stays strict — it has no stored baseline.
    """
    if patched is None:
        return stored
    if not patched.strip():
        return None
    if patched == stored:
        from hal0.runners import RUNNER_IMAGES  # lazy: runners must not import profiles

        if patched not in RUNNER_IMAGES:
            # Logged, not silently ignored — mirrors the flags grandfathering.
            # An unchanged key that IS in the registry is the ordinary save and
            # says nothing worth a line.
            log.info(
                "profile carries a runner key this box's registry no longer "
                "offers; grandfathered on update",
                extra={"event": "profile.runner_grandfathered", "runner": patched},
            )
        return stored
    return screen_profile_runner(patched)


def screen_profile_runner(runner: str | None) -> str | None:
    """Validate + canonicalize a profile's runner key (D4). None/blank passes.

    A blank string is treated as None — "no runtime pinned" — rather than
    looked up as a key: it is what the drawer's Auto option puts on the wire,
    and an empty key names nothing in any registry. (On an update the blank is
    already resolved by :func:`_merge_runner` as the clear sentinel; this is
    the create/import side of the same reading.)

    ``runner`` is a RUNNER_IMAGES registry key, never an image ref — keys
    survive image/tag updates (the same rot that got ``image`` removed from
    profiles, spec-hw-slot-ownership §3). A superseded alias (e.g.
    ``vulkanfpx``) is folded to its canonical key before persisting, so the
    stored value is always the canonical registry key. Shared by the profile
    catalog write seam (create/update) and the portable import path so both
    apply the identical check.
    """
    if runner is None or not runner.strip():
        return None
    from hal0.runners import RUNNER_IMAGES, canonical_runner_key

    key = canonical_runner_key(runner)
    if key not in RUNNER_IMAGES:
        raise UnprocessableEntity(
            f"unknown runtime {runner!r}",
            code="profiles.unknown_runner",
            details={"runner": runner, "available": sorted(RUNNER_IMAGES)},
        )
    return key


def screen_profile_flags(flags: str | None, *, grandfathered: str | None = None) -> None:
    """Reject physical slot flags and hal0-managed flags before a profile is persisted.

    The catalog is the actual write seam — routes, import and CLI paths all
    funnel through :meth:`ProfileCatalog.create` / :meth:`ProfileCatalog.update`
    — so the guard lives here and the HTTP layer delegates to it rather than
    keeping a second copy. Enforces the model/profile ownership partition
    (§5 hardware flags + §21.7 managed args). Empty/unset ``flags`` is a no-op.

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

    ``flags`` itself must be valid shell text (#1730). #1639 added a
    launch-time guard (``providers.container``, ``slot.profile_flags_malformed``)
    so a divergent profile with unmatched quoting no longer 500s the
    launch/preview path — but that only covers a profile that ALREADY made it
    onto disk. This screen is the actual write seam (routes/import/CLI all
    funnel through it), and until now it silently returned on a
    ``shlex.split`` failure instead of rejecting it, so bad quoting sailed
    straight through to persistence — the root cause #1639 patched around
    rather than fixed. Reject it here instead, with the same error family the
    launch-time guard uses, so the operator sees the problem at save time
    instead of the next launch.

    Malformed-quoting rejection is ALSO grandfathered (mirroring the §5
    hardware-flag grandfathering above): a profile persisted before #1737
    added this check can already carry bad quoting on disk. Routes forward
    ``body.flags`` on every PUT and the UI drawer resends the stored flags
    text verbatim, so without grandfathering, a save that only changes e.g.
    ``device_class`` would re-screen the SAME already-broken flags and 422 —
    making the profile un-editable for any field, the exact #1411 round-trip
    trap recreated for quoting instead of hardware flags. The screen only
    rejects a NEWLY-introduced or CHANGED malformed value; an unchanged
    inherited one passes through (logged, not silently ignored).
    """
    if not flags or not flags.strip():
        return
    try:
        tokens = shlex.split(flags)
    except ValueError as exc:
        if grandfathered is not None and flags == grandfathered:
            log.info(
                "profile flags carry pre-existing malformed quoting; grandfathered on update",
                extra={"event": "profile.malformed_flags_grandfathered", "flags": flags},
            )
            return
        raise UnprocessableEntity(
            f"profile flags are not valid shell text: {exc}",
            code="profiles.flags_malformed",
            details={"flags": flags},
        ) from exc
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
