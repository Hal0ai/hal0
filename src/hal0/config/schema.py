"""Pydantic v2 schema models for hal0 configuration.

All TOML files under /etc/hal0/ are validated against these models at
startup.  Typos like backend = "vukan" raise a ValidationError with the
field path (PLAN.md §5 Tier 1).

Model hierarchy:
    Hal0Config       — top-level hal0.toml
      MetaConfig       — [meta] schema_version (Tier 3 migrations)
      SlotsConfig      — [slots] global slot policy
      DispatcherConfig — [dispatcher] tunables (Tier 2 prefetch timeout)
      TelemetryConfig  — [telemetry] opt-in
    ProvidersConfig  — providers.toml (external LLM providers)
    UpstreamsConfig  — upstreams.toml (slot + remote upstream catalog)
    SlotConfig       — slots/<name>.toml
      ModelConfig      — [model] section within a slot config
    HardwareInfo     — /etc/hal0/hardware.json (written by `hal0 probe`)

Port target: haloai lib/config.py (420 lines).
See PLAN.md §3, §5 Tier 1 ("pydantic-validated TOML schema at load time").
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

from hal0.config import paths

log = logging.getLogger(__name__)

# ── Shared constants ───────────────────────────────────────────────────────────

# TIER1: surface-area for the backend whitelist. Typos like
# `backend = "vukan"` must raise at load time with the field path.
#
# DEPRECATED v0.2: ``SlotConfig.backend`` is being retired in favour of the
# hardware-preference field ``SlotConfig.device``. The whitelist is kept for
# one release so legacy slot TOMLs round-trip cleanly; a warning is logged
# whenever ``backend`` is read without an accompanying ``device``. See
# ADR-0006 §7 (v0.2 migration plan, decision 15).
_VALID_BACKENDS = frozenset({"vulkan", "rocm", "flm", "moonshine", "kokoro", "cpu"})

# v0.2 hardware-preference enum. ``device`` replaces the overloaded
# ``backend`` field — it carries hardware intent only, not provider choice.
# ``hal0.model_meta.device_to_backend`` maps these to the recipe:backend
# pair that feeds container profile/argv derivation.
DeviceLiteral = Literal["gpu-rocm", "gpu-vulkan", "cpu", "npu"]
_VALID_DEVICES = frozenset({"gpu-rocm", "gpu-vulkan", "cpu", "npu"})

# Default ``device`` for fresh installs. Strix Halo (hal0's reference
# target) gets best throughput on ROCm; the recommender may downgrade
# this in hardware-aware seeds.
DEFAULT_DEVICE: str = "gpu-rocm"

# Mapping from the legacy ``backend`` enum to the v0.2 ``device`` enum.
# Used by:
#   - ``SlotConfig`` model-validator (auto-promote on load).
#   - ``hal0/config/migrations/capabilities_v2.py`` (file migration).
#   - the capabilities on-load auto-migration.
# Keep these aligned with ADR-0006 §7. The values for moonshine/kokoro map
# to ``cpu`` because those toolboxes were always CPU runtimes — the legacy
# enum overloaded the term ``backend`` with provider identity.
BACKEND_TO_DEVICE: dict[str, str] = {
    "vulkan": "gpu-vulkan",
    "rocm": "gpu-rocm",
    "flm": "npu",
    "moonshine": "cpu",
    "kokoro": "cpu",
    "cpu": "cpu",
    # The capabilities catalog has historically stored values in the new
    # namespace already (e.g. ``gpu-rocm``); accept them idempotently so
    # both SlotConfig.backend and CapabilitySelection.backend can flow
    # through the same map without surprise.
    "gpu-rocm": "gpu-rocm",
    "gpu-vulkan": "gpu-vulkan",
    "npu": "npu",
}

# Valid provider names. ContainerProvider drives every slot lifecycle;
# the pre-container names remain accepted so legacy slot TOMLs round-trip
# without raising — the provider field exists only for round-trip + UI
# label compatibility. ``"comfyui"`` is the exception: it is the active
# container image-gen provider (img.toml, ADR image slots), not a
# deprecated legacy value.
_VALID_PROVIDERS = frozenset({"llama-server", "flm", "moonshine", "kokoro", "comfyui"})

# Slot port range.  8080 is the hal0 API; slots get 8081-8099; 8188 =
# ComfyUI's stock port for the img slot — kept well-known so operator
# bookmarks/tooling keep working.
_SLOT_PORT_MIN = 8081
_SLOT_PORT_MAX = 8200

# Schema version for migrations.  Bumped when a backwards-incompatible
# config-shape change lands.  See PLAN.md §5 Tier 3.
CURRENT_SCHEMA_VERSION = 1

# Capabilities-file schema version. Independent of ``hal0.toml``'s
# ``meta.schema_version`` — capabilities.toml carries its own counter so
# the v0.2 backend→device migration (ADR-0006 §7) can be detected and
# applied without coupling the two config files.
#
# - schema_version = 1 (or absent): legacy. CapabilitySelection uses
#   ``backend`` field.
# - schema_version = 2: post-v0.2 migration. CapabilitySelection
#   uses ``device``; ``backend`` round-trips as a deprecated alias.
CAPABILITIES_SCHEMA_VERSION_LEGACY = 1
CAPABILITIES_SCHEMA_VERSION_CURRENT = 2


def map_backend_to_device(backend: str | None) -> str:
    """Map a legacy ``backend`` value to the v0.2 ``device`` enum.

    Unknown values (e.g. an operator hand-edited a slot TOML with a
    bespoke backend tag) fall back to ``cpu`` so the runtime has a safe
    default rather than crashing at load. A warning is logged so the
    operator notices on the next ``hal0-api`` boot.

    Empty / None input is treated as "no opinion" and returns the
    package-level default ``DEFAULT_DEVICE``.
    """
    if not backend:
        return DEFAULT_DEVICE
    mapped = BACKEND_TO_DEVICE.get(backend)
    if mapped is not None:
        return mapped
    log.warning(
        "config.device_mapping_unknown_backend",
        extra={"backend": backend, "fallback": "cpu"},
    )
    return "cpu"


# ── ModelConfig + SlotConfig ───────────────────────────────────────────────────


class ModelConfig(BaseModel):
    """[model] section in a slot TOML.

    Specifies which model the slot loads by default and any inference
    parameters that override the global defaults.
    """

    model_config = {"populate_by_name": True, "extra": "allow"}

    default: str = Field(
        default="",
        description="Default model id from the registry.  Must exist in /var/lib/hal0/registry/.",
    )
    context_size: int = Field(
        default=4096,
        ge=128,
        description="Context window size in tokens.",
    )
    n_gpu_layers: int = Field(
        default=-1,
        description="Number of layers to offload to GPU.  -1 means all.",
    )
    rope_freq_base: float = Field(
        default=0.0,
        ge=0.0,
        description="RoPE frequency base override.  0.0 means use model default.",
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific model params passed verbatim to the backend.",
    )


class NpuConfig(BaseModel):
    """[npu] table in a slot TOML — FLM trio modality toggles.

    Maps to ``flm serve --asr 1 --embed 1`` flag construction performed by
    FLMProvider.container_spec at runtime.  This config file is the single
    source of truth; it replaces the legacy daemon's nested flm.args approach.

    Both fields default to ``False`` so a bare ``[npu]`` section in a slot
    TOML is valid (all-off) without requiring the operator to explicitly
    disable modalities they don't need.
    """

    model_config = {"extra": "forbid"}

    asr: bool = Field(
        default=False,
        description="Enable ASR (speech-to-text) modality via FLM --asr 1.",
    )
    embed: bool = Field(
        default=False,
        description="Enable embedding modality via FLM --embed 1.",
    )


class ImageGenConfig(BaseModel):
    """[image] table in a slot TOML — persisted image-gen settings (#599).

    Carried by the img (ComfyUI) slot.  ``idle_restore_minutes`` feeds the
    GpuArbiter's restore timer (Phase D spec §7): after the img slot has
    had no jobs for this many minutes, the arbiter restores the LLM GPU
    slots it stopped.  ``default_size``/``default_steps`` seed the image
    generation request defaults surfaced in the dashboard.
    """

    model_config = {"extra": "forbid"}

    idle_restore_minutes: int = Field(
        default=60,
        ge=0,
        description=(
            "Minutes of img-slot job inactivity before the GpuArbiter "
            "restores stopped LLM GPU slots.  0 = never auto-restore."
        ),
    )
    default_size: str = Field(
        default="1024x1024",
        description="Default output size (WxH) for image generation requests.",
    )
    default_steps: int = Field(
        default=0,
        ge=0,
        description="Default sampler steps.  0 = use the model-class default.",
    )


class ServerConfig(BaseModel):
    """[server] section in a slot TOML.

    Currently carries only ``extra_args`` — a freeform CLI-flag string
    appended after model defaults at launcher arg-build time.  Future
    server-side knobs (idle-eviction policy, request quotas, …) land
    here too rather than at top-level so the surface stays grouped.

    See docs/internal/models-slots-impl-plan.md §A3 and the ``flag_merge`` util.
    """

    model_config = {"populate_by_name": True, "extra": "allow"}

    extra_args: str | None = Field(
        default=None,
        description=(
            "Freeform llama-server CLI passthrough.  Tokenised via shlex; "
            "merged with the model's defaults.extra_args by "
            "hal0.slots.flag_merge.merge_flags so slot flags win on collisions "
            "(except for append-list flags like --lora / --draft-model / --override-kv)."
        ),
    )


class SlotConfig(BaseModel):
    """Pydantic model for a single slot's TOML config (slots/<name>.toml).

    Fields correspond to the [slot], [model], and [server] sections.
    See PLAN.md §2 (filesystem layout).
    """

    # NOTE: extra="allow" so future fields and provider-specific knobs
    # round-trip cleanly through load/save without dropping unknown keys.
    model_config = {"populate_by_name": True, "extra": "allow"}

    # [slot] section
    name: str = Field(..., description="Slot name, e.g. 'primary'.")
    port: int = Field(
        ...,
        ge=_SLOT_PORT_MIN,
        le=_SLOT_PORT_MAX,
        description=f"Host port for this slot ({_SLOT_PORT_MIN}-{_SLOT_PORT_MAX}, 127.0.0.1 only).",
    )
    backend: str = Field(
        default="vulkan",
        description=(
            "DEPRECATED (v0.2; removed v0.3): legacy overloaded backend enum. "
            "Use ``device`` instead. Reading a SlotConfig that has ``backend`` "
            "set without ``device`` logs a deprecation warning and auto-fills "
            "``device`` via ``map_backend_to_device``. See ADR-0006 §7."
        ),
    )
    device: str = Field(
        default=DEFAULT_DEVICE,
        description=(
            "v0.2 hardware-preference enum: 'gpu-rocm' | 'gpu-vulkan' | 'cpu' "
            "| 'npu'. Replaces the legacy ``backend`` field which mixed "
            "providers and backends. See ADR-0006 §7."
        ),
    )
    provider: str = Field(
        default="llama-server",
        description=(
            "DEPRECATED: the slot's legacy provider label. Slots run as "
            "podman containers (ContainerProvider); this field round-trips "
            "for backwards compatibility and UI labels only."
        ),
    )
    enabled: bool = Field(
        default=True,
        description="Whether this slot is started on hal0 startup.",
    )
    runtime: Literal["container"] = Field(
        default="container",
        description=(
            "DEPRECATED (kept one release): slot runtime engine. 'container' "
            "(podman, managed by ContainerProvider) is the only runtime; "
            "legacy values are migrated on load. See the "
            "container-runtime design doc §3."
        ),
    )
    profile: str | None = Field(
        default=None,
        description=(
            "Profile name from /etc/hal0/profiles.toml. The profile supplies "
            "the container image + bench-tuned flags; the slot supplies "
            "model, context_size, and port. See ProfileConfig and the "
            "container-runtime design doc §1."
        ),
    )
    role: str | None = Field(
        default=None,
        description=(
            "Optional role hint for normalization chain binding "
            "(e.g. 'primary', 'utility', 'npu'). When unset, role is derived "
            "from the slot name. Authoritative over the name when set."
        ),
    )
    enable_thinking: bool | None = Field(
        default=None,
        description=(
            "Per-slot reasoning default. true → requests routed to this slot "
            "default to thinking ON; false → OFF; None → global default "
            "(suppressed). Always overridable per request via top-level "
            "enable_thinking / chat_template_kwargs. See normalize/thinking.py."
        ),
    )
    mtp: bool | None = Field(
        default=None,
        description=(
            "Per-slot MTP (multi-token-prediction speculative decoding) override. "
            "true → force on; false → force off; None → inherit the profile's mtp. "
            "Only effective on rocmfp4 profiles with an MTP-capable model. "
            "See resolve_profile_flags and MTP_FLAG_BUNDLE."
        ),
    )

    # [model] section (nested)
    model: ModelConfig = Field(default_factory=ModelConfig)

    # [server] section
    # NOTE: ``workers`` and ``idle_timeout_s`` are flat top-level fields
    # for haloai-era round-trip compatibility (the loader hoists [slot]
    # keys, not [server] keys, into the validated SlotConfig).  The new
    # nested ``server`` model below holds fields that are authored under
    # [server] in TOML — keep additions there.
    workers: int = Field(
        default=1,
        ge=1,
        description="Number of parallel request workers.",
    )
    idle_timeout_s: int = Field(
        default=300,
        ge=0,
        description="Seconds idle before transitioning to 'idle' state.  0 disables.",
    )

    # Typed [server] subsection.  See ServerConfig + the round-trip
    # validator/serializer below: on load we hoist the [server] table out
    # of the catch-all ``extra`` dict; on dump we re-tuck it under extra
    # so loader._unflatten_slot_toml writes a proper [server] table.
    server: ServerConfig = Field(default_factory=ServerConfig)

    # Typed [npu] subsection.  Same hoist/tuck round-trip pattern as
    # [server]: loader._flatten_slot_toml lands [npu] in extra["npu"];
    # _hoist_npu_from_extra promotes it to the typed field; _tuck_server_into_extra
    # re-parks it under extra so _unflatten_slot_toml writes a proper [npu]
    # table on disk.
    npu: NpuConfig | None = Field(
        default=None,
        description=(
            "[npu] table — FLM trio modality toggles (asr, embed). "
            "Absent on non-NPU slots. See NpuConfig."
        ),
    )

    # Typed [image] subsection (#599) — persisted image-gen settings for
    # the img (ComfyUI) slot.  Same hoist/tuck round-trip pattern as
    # [server]/[npu].  Defaults apply on slots without an [image] table;
    # the dump serializer elides an all-defaults ImageGenConfig so
    # non-img slots don't grow a stray [image] table on disk.
    image_gen: ImageGenConfig = Field(
        default_factory=ImageGenConfig,
        alias="image",
        description=(
            "[image] table — image-gen settings (idle_restore_minutes, "
            "default_size, default_steps). See ImageGenConfig (#599)."
        ),
    )

    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific slot params passed verbatim.",
    )

    @model_validator(mode="before")
    @classmethod
    def _hoist_server_from_extra(cls, data: Any) -> Any:
        """Pull a `[server]` TOML table out of the loader's `extra` catch-all.

        ``hal0.config.loader._flatten_slot_toml`` shoves every unrecognised
        top-level TOML table (anything that isn't `[slot]` or `[model]`)
        into ``extra``.  Without this hoist, `[server].extra_args` written
        on disk would never reach the typed ``ServerConfig`` field; it would
        just round-trip opaquely through ``extra["server"]``.
        """
        if not isinstance(data, dict):
            return data
        # Already top-level — nothing to do.
        if "server" in data and data.get("server") is not None:
            return data
        extra = data.get("extra")
        if not isinstance(extra, dict):
            return data
        server = extra.get("server")
        if isinstance(server, dict):
            # Copy to avoid mutating the loader's dict in place.
            new_data = dict(data)
            new_extra = dict(extra)
            new_extra.pop("server", None)
            new_data["server"] = server
            new_data["extra"] = new_extra
            return new_data
        return data

    @model_validator(mode="before")
    @classmethod
    def _hoist_npu_from_extra(cls, data: Any) -> Any:
        """Pull a `[npu]` TOML table out of the loader's `extra` catch-all.

        Mirrors ``_hoist_server_from_extra``: ``_flatten_slot_toml`` stashes
        every unrecognised top-level table into ``extra``, so an on-disk
        ``[npu]`` section for an NPU slot would never reach the typed
        ``NpuConfig`` field without this hoist.
        """
        if not isinstance(data, dict):
            return data
        # Already top-level (e.g. passed directly in tests) — nothing to do.
        if "npu" in data and data.get("npu") is not None:
            return data
        extra = data.get("extra")
        if not isinstance(extra, dict):
            return data
        npu = extra.get("npu")
        if isinstance(npu, dict):
            new_data = dict(data)
            new_extra = dict(extra)
            new_extra.pop("npu", None)
            new_data["npu"] = npu
            new_data["extra"] = new_extra
            return new_data
        return data

    @model_validator(mode="before")
    @classmethod
    def _hoist_image_from_extra(cls, data: Any) -> Any:
        """Pull an `[image]` TOML table out of the loader's `extra` catch-all.

        Mirrors ``_hoist_npu_from_extra`` for the typed ``image_gen`` field
        (#599): ``_flatten_slot_toml`` stashes the on-disk ``[image]`` table
        into ``extra["image"]``, so the img slot's persisted image-gen
        settings would never reach :class:`ImageGenConfig` without this.

        Collision guard: a top-level *string* ``image`` is the documented
        per-slot container-image override (read by ``llama_server.image_ref``
        and ``comfyui.image_ref`` from the raw slot dict). It must NOT hit
        the ``image_gen`` alias — pre-D1 it round-tripped via
        ``extra="allow"``, so non-dict values are parked under
        ``extra["image"]`` to preserve that behavior.
        """
        if not isinstance(data, dict):
            return data
        image = data.get("image")
        if image is not None and not isinstance(image, dict):
            # Legacy string container-image override — park under extra so
            # the ImageGenConfig alias never sees it and providers can keep
            # reading it from the round-tripped config.
            new_data = dict(data)
            new_data.pop("image")
            old_extra = new_data.get("extra")
            new_extra = dict(old_extra) if isinstance(old_extra, dict) else {}
            new_extra["image"] = image
            new_data["extra"] = new_extra
            return new_data
        # Already top-level (direct model_validate of a flat TOML dict,
        # where the "image" alias applies) — nothing to do.
        if isinstance(image, dict) or data.get("image_gen") is not None:
            return data
        extra = data.get("extra")
        if not isinstance(extra, dict):
            return data
        image = extra.get("image")
        if isinstance(image, dict):
            new_data = dict(data)
            new_extra = dict(extra)
            new_extra.pop("image", None)
            new_data["image"] = image
            new_data["extra"] = new_extra
            return new_data
        return data

    @model_validator(mode="before")
    @classmethod
    def _promote_backend_to_device(cls, data: Any) -> Any:
        """Soft-deprecation hook: derive ``device`` from a legacy ``backend``.

        v0.2 (ADR-0006 §7) renames the hardware-preference field
        ``backend`` → ``device``. For one release we read both: if a TOML
        file or in-memory dict carries ``backend`` but no ``device`` we
        synthesise ``device`` via :func:`map_backend_to_device` so the
        rest of the system can pivot to the new field without losing
        operator data. A log warning fires per load so the deprecation
        is visible.

        We deliberately do NOT *delete* ``backend`` from the dict — the
        slot loader/dumper still round-trips it onto disk so a downgrade
        to v0.1.x stays clean. Removal lands in v0.3.
        """
        if not isinstance(data, dict):
            return data
        # Skip when the caller already supplied ``device`` explicitly.
        if data.get("device"):
            return data
        backend_value = data.get("backend")
        if not backend_value:
            return data
        # Tolerate already-new-namespace values (gpu-rocm etc) — those
        # round-trip through ``map_backend_to_device`` as identities.
        mapped = map_backend_to_device(str(backend_value))
        if backend_value not in _VALID_DEVICES:
            log.warning(
                "config.slot.backend_deprecated",
                extra={
                    "backend": backend_value,
                    "promoted_device": mapped,
                    "note": "SlotConfig.backend is deprecated; set 'device' instead. See ADR-0006 §7.",
                },
            )
        new_data = dict(data)
        new_data["device"] = mapped
        return new_data

    @model_serializer(mode="wrap")
    def _tuck_server_into_extra(self, handler: Any) -> dict[str, Any]:
        """Inverse of `_hoist_server_from_extra` for round-trip dumps.

        ``hal0.config.loader._unflatten_slot_toml`` rebuilds the on-disk
        shape by enumerating known top-level keys and then sweeping
        ``extra.items()`` back to top-level tables.  It does not know about
        the new ``server`` field, so we re-park its dump under
        ``extra["server"]`` and drop the duplicate top-level entry.  Empty
        ServerConfigs (all-None) are elided so we don't write an empty
        `[server]` table to disk.

        Also handles the typed ``npu`` field the same way: a non-None
        NpuConfig dump is re-parked under ``extra["npu"]`` so the loader
        writes a proper `[npu]` table; None (no NPU config) is elided.
        """
        data: dict[str, Any] = handler(self)
        server = data.pop("server", None)
        if isinstance(server, dict):
            # Drop None-valued fields so an untouched ServerConfig (all
            # defaults) doesn't produce a stray `[server]` table on disk.
            cleaned = {k: v for k, v in server.items() if v is not None}
            if cleaned:
                extra = data.get("extra")
                extra = dict(extra) if isinstance(extra, dict) else {}
                extra["server"] = cleaned
                data["extra"] = extra
        # Re-park [npu] under extra so _unflatten_slot_toml writes a proper
        # [npu] TOML table.  None means the slot has no NPU config — elide.
        npu = data.pop("npu", None)
        if isinstance(npu, dict):
            extra = data.get("extra")
            extra = dict(extra) if isinstance(extra, dict) else {}
            extra["npu"] = npu
            data["extra"] = extra
        # Re-park [image] (typed ``image_gen``, #599) under extra the same
        # way.  An all-defaults ImageGenConfig is elided so non-img slots
        # don't grow a stray [image] table on disk.
        image_gen = data.pop("image_gen", None)
        if isinstance(image_gen, dict) and image_gen != ImageGenConfig().model_dump():
            extra = data.get("extra")
            extra = dict(extra) if isinstance(extra, dict) else {}
            extra["image"] = image_gen
            data["extra"] = extra
        return data

    @field_validator("name")
    @classmethod
    def name_valid(cls, v: str) -> str:
        import re

        if not v or not v.strip():
            raise ValueError("slot name must not be empty")
        # Mirror haloai's slot-name policy: lowercase alphanumeric + - + _,
        # max 32 chars, must start with alphanumeric.  This is the same
        # regex used in haloai lib/config.py:create_slot_config().
        if not re.match(r"^[a-z0-9][a-z0-9_-]{0,31}$", v):
            raise ValueError(
                f"slot name {v!r}: use lowercase alphanumeric, hyphens, underscores; "
                f"start with alphanumeric; max 32 chars"
            )
        return v

    @field_validator("backend")
    @classmethod
    def backend_valid(cls, v: str) -> str:
        if v not in _VALID_BACKENDS:
            raise ValueError(f"backend {v!r} is not valid; choose from {sorted(_VALID_BACKENDS)}")
        return v

    @field_validator("device")
    @classmethod
    def device_valid(cls, v: str) -> str:
        # Same shape as ``backend_valid`` — catch typos at load time
        # ("gpu-rcom" → ValidationError with field path) per PLAN.md §5 Tier 1.
        if v not in _VALID_DEVICES:
            raise ValueError(f"device {v!r} is not valid; choose from {sorted(_VALID_DEVICES)}")
        return v

    @field_validator("provider")
    @classmethod
    def provider_valid(cls, v: str) -> str:
        if v not in _VALID_PROVIDERS:
            raise ValueError(f"provider {v!r} is not valid; choose from {sorted(_VALID_PROVIDERS)}")
        return v


# ── ProvidersConfig ────────────────────────────────────────────────────────────


class ProviderEntry(BaseModel):
    """One [[provider]] entry in providers.toml."""

    model_config = {"populate_by_name": True, "extra": "allow"}

    catalog_id: str = Field(
        ...,
        description="References an entry in upstreams.integrations._CATALOG.",
    )
    name: str = Field(default="", description="User-visible name override.")
    base_url: str = Field(
        default="",
        description="URL override (leave empty to use catalog default).",
    )
    auth_value_env: str = Field(
        default="",
        description="Env var holding the API key.  Never stored in plain text.",
    )
    enabled: bool = Field(default=True)
    models: list[str] = Field(default_factory=list, description="User-selected model ids.")

    @field_validator("catalog_id")
    @classmethod
    def catalog_id_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("provider catalog_id must not be empty")
        return v


class ProvidersConfig(BaseModel):
    """Parsed providers.toml."""

    model_config = {"populate_by_name": True, "extra": "allow"}

    provider: list[ProviderEntry] = Field(default_factory=list)


# ── ProfileConfig + ProfilesConfig ────────────────────────────────────────────

#: MTP draft-speculation flag bundle, bench-tuned.  Appended verbatim after
#: profile.flags when ``mtp=true``.  Keep in sync with the profiles.toml seed
#: and the bench doc (hal0-container-bench-2026-06-08.md).
MTP_FLAG_BUNDLE = (
    "--spec-type draft-mtp"
    " --spec-draft-device ROCm0"
    " --spec-draft-ngl all"
    " --spec-draft-n-max 4"
    " --spec-draft-n-min 0"
    " --spec-draft-p-min 0.0"
    " --spec-draft-p-split 0.10"
    " --spec-draft-type-k q4_0"
    " --spec-draft-type-v q4_0"
)

#: Seed profiles shipped with hal0.  Returned by ``load_profiles_config()``
#: when ``/etc/hal0/profiles.toml`` is absent so ``GET /api/profiles`` is
#: always populated on a fresh install.
#: Seed profile catalog.  Slugs are backend-agnostic workload names — the
#: ``backend`` field (not the slug) carries the ROCm/Vulkan choice, and the
#: card chip renders the backend as colour, so the slug no longer repeats it.
#: GPU profiles set ``backend``; non-GPU profiles (npu/cpu/img) omit it and
#: let ``device_class`` drive display.
SEED_PROFILES: dict[str, dict[str, object]] = {
    "rocm": {
        "image": "ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server",
        "flags": "-fa on -ctk q8_0 -ctv q8_0 -b 512 -ub 512 --parallel 1 --threads 8 --no-mmap",
        "mtp": False,
        "device_class": "gpu",
        "backend": "rocm",
        "intent": "MoE agents",
        "quant": "FP4",
    },
    "rocm-mtp": {
        "image": "ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server",
        "flags": "-fa on -ctk q8_0 -ctv q8_0 -b 512 -ub 512 --parallel 1 --threads 8 --no-mmap",
        "mtp": True,
        "device_class": "gpu",
        "backend": "rocm",
        "intent": "Dense chat + MTP",
        "quant": "FP4",
    },
    "vulkan": {
        "image": "ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server",
        "flags": "-fa on -b 512 -ub 512 --parallel 1 --threads 8 --no-mmap",
        "mtp": False,
        "device_class": "gpu",
        "backend": "vulkan",
        "intent": "Vulkan std · fallback",
        "quant": "Q4_K_M",
    },
    "flm": {
        "image": "ghcr.io/hal0ai/hal0-toolbox-flm:v1",
        "flags": "",
        "mtp": False,
        "device_class": "npu",
        "intent": "FLM NPU inference",
        "quant": "W4ABF16",
    },
    "tts": {
        "image": "ghcr.io/hal0ai/hal0-toolbox-kokoro:v1",
        "flags": "--model_path /mnt/ai-models/local/kokoro-v1/kokoro-onnx",
        "mtp": False,
        "device_class": "cpu",
        "intent": "TTS · Kokoro",
        "quant": "",
    },
    "comfyui": {
        "image": "docker.io/kyuz0/amd-strix-halo-comfyui:latest",
        "flags": "--disable-mmap --bf16-vae --cache-none",
        "mtp": False,
        "device_class": "img",
        "intent": "Image generation",
        "quant": "",
    },
}

#: Static bench numbers for seed profiles, surfaced as the card hero metric.
#: ``tps`` = tokens/sec (LLM throughput); ``rtf`` = real-time factor (synth,
#: e.g. TTS).  Grounded in hal0-container-bench-2026-06-08.md.  Custom
#: profiles have no entry → the card shows "—" until benched.
PROFILE_BENCH: dict[str, dict[str, float]] = {
    "rocm": {"tps": 52.8},
    "rocm-mtp": {"tps": 24.4},
    "vulkan": {"tps": 41.0},
    "flm": {"tps": 38.6},
    "tts": {"rtf": 0.18},
}

#: Preselect map for the create-modal device picker and legacy-slot
#: migration defaults.  Keys are ``DeviceLiteral`` values (gpu-rocm,
#: gpu-vulkan, cpu, npu); values are seed profile names that best represent
#: each device class.
DEVICE_DEFAULT_PROFILES: dict[str, str] = {
    "gpu-rocm": "rocm",
    "gpu-vulkan": "vulkan",
    "cpu": "tts",
    "npu": "flm",
}


class ProfileConfig(BaseModel):
    """One ``[profile.<name>]`` entry in profiles.toml.

    A profile is a reusable backend template — image + bench-tuned flag
    bundle + optional MTP toggle.  Slots reference a profile by name;
    the profile supplies everything except the model path, context size,
    and port (which belong to the slot).

    See the hal0 container-runtime design doc (§1) and bench doc for the
    rationale behind each seed profile.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    image: str = Field(
        ...,
        description="Container image ref, e.g. ghcr.io/hal0ai/…:rocm-7.2.4-rocmfp4-server.",
    )
    flags: str = Field(
        default="",
        description="Bench-tuned llama-server CLI flags (no model/port/ctx args).",
    )
    mtp: bool = Field(
        default=False,
        description=(
            "When true, the MTP draft-speculation bundle is appended to ``flags`` "
            "at resolve time (see ``resolve_profile_flags()``)."
        ),
    )
    device_class: Literal["gpu", "cpu", "npu", "img"] = Field(
        default="gpu",
        description=(
            "Device class this profile targets.  Drives drawer profile filtering "
            "and create-modal device defaults.  ``'img'`` is reserved for Phase D "
            "(ComfyUI image-generation slots) and is not yet used."
        ),
    )
    backend: Literal["rocm", "vulkan"] | None = Field(
        default=None,
        description=(
            "GPU runtime this profile targets — the authoritative source for the "
            "ROCm-vs-Vulkan choice (replaces sniffing the image tag).  ``None`` for "
            "non-GPU profiles (npu/cpu/img), where ``device_class`` drives display "
            "and slot-card colour."
        ),
    )
    cloned_from: str | None = Field(
        default=None,
        description=(
            "Provenance: name of the profile this one was cloned from "
            "(set by the dashboard clone / edit-a-copy flow).  Informational "
            "only — never resolved or validated against the catalog."
        ),
    )
    intent: str = Field(
        default="",
        description=(
            "Human label for what this profile is for, shown as the card "
            "headline in the dashboard (e.g. 'MoE agents · long-ctx').  "
            "Informational only."
        ),
    )
    quant: str = Field(
        default="",
        description=(
            "Weight quantization format shown as a card chip (e.g. 'FP4', "
            "'Q4_K_M', 'W4ABF16').  Informational only — the runtime reads "
            "the quant from the model, not this field."
        ),
    )

    @field_validator("image")
    @classmethod
    def image_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("profile image must not be empty")
        return v


class ProfilesConfig(BaseModel):
    """Parsed profiles.toml — top-level ``[profile]`` table.

    Each key under ``[profile]`` becomes an entry in ``profile``:

        [profile.rocm]
        image = "ghcr.io/..."
        flags = "-fa on ..."
        mtp   = false
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    profile: dict[str, ProfileConfig] = Field(default_factory=dict)


def resolve_profile_flags(profile: ProfileConfig, mtp_override: bool | None = None) -> str:
    """Return the full flag string for *profile*, expanding MTP when set.

    When the effective MTP setting is ``True``, ``MTP_FLAG_BUNDLE`` is
    appended after ``profile.flags`` (separated by a single space).  The
    model path, port, and context size are the slot's concern — they are
    NOT included here.

    The effective MTP value is resolved as follows:
      - ``mtp_override=True``  → force MTP on regardless of profile.mtp.
      - ``mtp_override=False`` → force MTP off regardless of profile.mtp.
      - ``mtp_override=None``  → inherit ``profile.mtp`` (default behaviour).

    Args:
        profile: A validated :class:`ProfileConfig`.
        mtp_override: Per-slot override from :attr:`SlotConfig.mtp`.
            ``None`` means "inherit from profile".

    Returns:
        The complete flag string ready to pass to llama-server.
    """
    base = profile.flags.strip()
    effective_mtp = mtp_override if mtp_override is not None else profile.mtp
    if effective_mtp:
        return f"{base} {MTP_FLAG_BUNDLE}".strip()
    return base


# ── UpstreamsConfig ────────────────────────────────────────────────────────────


_VALID_UPSTREAM_KINDS = frozenset({"slot", "remote"})
_VALID_AUTH_STYLES = frozenset({"bearer", "header", "none"})
_VALID_WARMUP = frozenset({"none", "lazy", "eager"})


class UpstreamEntry(BaseModel):
    """One [[upstream]] entry in upstreams.toml."""

    model_config = {"populate_by_name": True, "extra": "allow"}

    name: str = Field(..., description="Unique upstream name.")
    kind: str = Field(default="remote", description="'slot' | 'remote'.")
    url: str = Field(..., description="Base URL.")
    auth_style: str = Field(default="bearer")
    auth_value_env: str = Field(default="")
    timeout_seconds: float = Field(default=300.0, gt=0.0)
    slot_name: str | None = Field(default=None)
    warmup_strategy: str = Field(default="none")
    advertise_models: bool = Field(default=True)

    @field_validator("name")
    @classmethod
    def name_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("upstream name must not be empty")
        return v

    @field_validator("url")
    @classmethod
    def url_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("upstream url must not be empty")
        return v

    @field_validator("kind")
    @classmethod
    def kind_valid(cls, v: str) -> str:
        if v not in _VALID_UPSTREAM_KINDS:
            raise ValueError(
                f"upstream kind {v!r} is not valid; choose from {sorted(_VALID_UPSTREAM_KINDS)}"
            )
        return v

    @field_validator("auth_style")
    @classmethod
    def auth_style_valid(cls, v: str) -> str:
        if v not in _VALID_AUTH_STYLES:
            raise ValueError(
                f"auth_style {v!r} is not valid; choose from {sorted(_VALID_AUTH_STYLES)}"
            )
        return v

    @field_validator("warmup_strategy")
    @classmethod
    def warmup_valid(cls, v: str) -> str:
        if v not in _VALID_WARMUP:
            raise ValueError(
                f"warmup_strategy {v!r} is not valid; choose from {sorted(_VALID_WARMUP)}"
            )
        return v

    @model_validator(mode="after")
    def slot_kind_has_slot_name(self) -> UpstreamEntry:
        # NOTE: a `kind = "slot"` upstream MUST carry slot_name so the
        # dispatcher can resolve it to a hal0-slot@<name>.service unit.
        # Catch this at load rather than at dispatch time.
        if self.kind == "slot" and not (self.slot_name and self.slot_name.strip()):
            raise ValueError(f"upstream {self.name!r}: kind='slot' requires slot_name to be set")
        return self


class UpstreamsConfig(BaseModel):
    """Parsed upstreams.toml."""

    model_config = {"populate_by_name": True, "extra": "allow"}

    upstream: list[UpstreamEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def names_unique(self) -> UpstreamsConfig:
        seen: set[str] = set()
        for u in self.upstream:
            if u.name in seen:
                raise ValueError(f"upstream name {u.name!r} is duplicated in upstreams.toml")
            seen.add(u.name)
        return self


# ── HardwareInfo ───────────────────────────────────────────────────────────────
# Canonical home per PLAN.md §3. hardware/probe.py re-exports for callers
# that import from there. Units are MiB integers throughout; the dashboard
# divides by 1024 at render time.


class GPUInfo(BaseModel):
    """One detected GPU."""

    model_config = {"populate_by_name": True, "extra": "allow"}

    vendor: str = Field(default="", description="'amd' | 'nvidia' | 'intel' | 'unknown'.")
    name: str = Field(default="", description="Marketing name, e.g. 'RTX 4080'.")
    vram_mb: int = Field(
        default=0, ge=0, description="VRAM (or GTT pool for UMA) in MiB; 0 = unknown."
    )
    pci_id: str = Field(default="", description="PCI bus id, e.g. '0000:01:00.0'.")
    driver: str = Field(default="", description="Driver name reported by sysfs.")
    drm_path: str = Field(
        default="", description="DRM sysfs path, e.g. '/sys/class/drm/card1/device'."
    )
    compute_capable: bool = Field(default=False, description="True if ROCm/CUDA is available.")
    vulkan_capable: bool = Field(default=False, description="True if Vulkan is available.")


class NPUInfo(BaseModel):
    """One detected NPU (AMD XDNA / future vendors)."""

    model_config = {"populate_by_name": True, "extra": "allow"}

    present: bool = Field(default=False, description="True if an NPU was detected.")
    vendor: str = Field(default="", description="NPU vendor, e.g. 'amd'.")
    name: str = Field(default="", description="NPU name, e.g. 'AMD XDNA (Strix Halo)'.")
    driver: str = Field(default="", description="Driver name, e.g. 'amdxdna'.")


class HardwareInfo(BaseModel):
    """Pydantic model for /etc/hal0/hardware.json.

    Written by `hal0 probe` (hal0.hardware.probe).  Read by the slot
    config form and the dispatcher's "your hardware can run this" checks.

    See PLAN.md §2 (hardware.json) and §3 (hardware module port).
    """

    model_config = {"populate_by_name": True, "extra": "allow"}

    hostname: str = Field(default="", description="Kernel hostname (/proc/sys/kernel/hostname).")
    uptime_s: int = Field(
        default=0, ge=0, description="Seconds since boot at probe time (/proc/uptime)."
    )
    kernel: str = Field(
        default="", description="Kernel version string, e.g. 'Linux version 7.0.6-2-pve'."
    )
    distro: str = Field(
        default="",
        description="OS PRETTY_NAME from /etc/os-release, e.g. 'Debian GNU/Linux 13 (trixie)'.",
    )
    cpu_model: str = Field(default="", description="CPU model string, e.g. 'AMD Ryzen 9 7950X'.")
    cpu_cores: int = Field(default=0, ge=0, description="Physical core count.")
    cpu_threads: int = Field(default=0, ge=0, description="Logical thread count.")
    ram_mb: int = Field(default=0, ge=0, description="Total system RAM in MiB.")
    ram_available_mb: int = Field(
        default=0,
        ge=0,
        description="MemAvailable at probe time, MiB.",
    )
    swap_mb: int = Field(default=0, ge=0, description="Total swap in MiB.")
    # On AMD UMA (Strix Halo) the dashboard should show one unified pool — not
    # ram_mb + vram_mb, which double-counts because GTT is carved from RAM.
    # On discrete GPUs / non-UMA, this equals ram_mb.
    unified_memory_mb: int = Field(
        default=0,
        ge=0,
        description=(
            "True unified-memory pool size in MiB (host RAM that the GPU can "
            "share via GTT on UMA). Use this in the dashboard's "
            "'Unified memory · N GB pool' label rather than summing ram_mb + "
            "vram_mb (those overlap on UMA)."
        ),
    )
    gpus: list[GPUInfo] = Field(default_factory=list, description="Detected GPUs.")
    npu: NPUInfo = Field(
        default_factory=NPUInfo, description="Detected NPU (present=False if none)."
    )
    disk_free_mb: int = Field(
        default=0,
        ge=0,
        description="Free space on /var/lib/hal0 in MiB.",
    )
    cgroup_max_mb: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Running cgroup memory cap in MiB (issue #372). Read at probe "
            "time from /sys/fs/cgroup/memory.max (cgroup-v2) or the v1 "
            "fallback /sys/fs/cgroup/memory/memory.limit_in_bytes. None "
            "when the cgroup is unlimited (literal 'max' on v2, the "
            "9223372036854775807 sentinel on v1) or the file is unreadable. "
            "The dashboard treats this as a 3rd headroom candidate: when "
            "BELOW min(pool, host) it becomes the binding constraint and "
            "limitedBy is reported as 'cgroup'."
        ),
    )
    probed_at: str = Field(
        default="",
        description="ISO-8601 UTC timestamp of the last probe run.",
    )
    platform: str = Field(
        default="unknown",
        description=(
            "Detected platform string. One of: 'strix-halo', 'wsl2', "
            "'proxmox-kvm', 'kvm', 'lxc', 'bare-metal-amd-gpu', "
            "'bare-metal-nvidia-gpu', 'bare-metal-intel-igpu', "
            "'bare-metal-cpu-only', or 'unknown'. Used by the UI to label "
            "memory ('unified' only on strix-halo) and tailor docs links."
        ),
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Probe-time extras (kernel version, OS release, etc.).",
    )


# ── Hal0Config ─────────────────────────────────────────────────────────────────


class MetaConfig(BaseModel):
    """[meta] section in hal0.toml.  Tracks config schema version for migrations."""

    model_config = {"populate_by_name": True, "extra": "allow"}

    schema_version: int = Field(
        default=CURRENT_SCHEMA_VERSION,
        ge=1,
        description=(
            "Config schema version.  hal0 config migrate bumps this when applying "
            "versioned transforms.  See PLAN.md §5 Tier 3."
        ),
    )


class SlotsConfig(BaseModel):
    """[slots] section in hal0.toml.  Global slot policy."""

    model_config = {"populate_by_name": True, "extra": "allow"}

    max_slots: int = Field(
        default=0,
        ge=0,
        description="Maximum concurrent slots.  0 means unlimited.",
    )
    port_range_start: int = Field(
        default=_SLOT_PORT_MIN,
        ge=1024,
        le=65535,
        description="First port in the slot pool.",
    )
    port_range_end: int = Field(
        default=_SLOT_PORT_MAX,
        ge=1024,
        le=65535,
        description="Last port in the slot pool (inclusive).",
    )
    idle_timeout_s: int = Field(
        default=300,
        ge=0,
        description=(
            "Default idle-eviction TTL (seconds), applied per slot. "
            "A slot that has not served a request for this long transitions "
            "to idle. 0 disables eviction. Per-slot idle_timeout_s in each "
            "slot's TOML overrides this value."
        ),
    )

    @model_validator(mode="after")
    def port_range_sane(self) -> SlotsConfig:
        if self.port_range_end < self.port_range_start:
            raise ValueError(
                f"slot port_range_end ({self.port_range_end}) must be >= "
                f"port_range_start ({self.port_range_start})"
            )
        return self


class DispatcherConfig(BaseModel):
    """[dispatcher] section in hal0.toml."""

    model_config = {"populate_by_name": True, "extra": "allow"}

    # TIER2: configurable prefetch timeout (was hardcoded 4s in haloai
    # lib/dispatcher.py:217-237).  Default 8s per PLAN.md §5 Tier 2.
    prefetch_timeout_s: float = Field(
        default=8.0,
        gt=0.0,
        description="Cold-cache prefetch timeout (PLAN.md §5 Tier 2).",
    )
    prefetch_parallel_cap: int = Field(
        default=4,
        ge=1,
        description="Max concurrent upstream parallel prefetches.",
    )


class TelemetryConfig(BaseModel):
    """[telemetry] section in hal0.toml."""

    model_config = {"populate_by_name": True, "extra": "allow"}

    enabled: bool = Field(
        default=False,
        description="Opt-in anonymous telemetry.  Off by default.  See PLAN.md §14.",
    )
    channel: str = Field(
        default="stable",
        description="Update channel: 'stable' | 'nightly'.",
    )

    @field_validator("channel")
    @classmethod
    def channel_valid(cls, v: str) -> str:
        if v not in ("stable", "nightly"):
            raise ValueError(f"channel {v!r} must be 'stable' or 'nightly'")
        return v


# ── MemoryGraphConfig (ADR-0014) ──────────────────────────────────────────────

# ADR-0014 §2: typed enum for the graph-extraction route.
# - "upstream": resolve via providers.toml + upstreams.toml
# - "primary":  uses the live `primary` slot (user owns quality)
# - "agent":    uses the dedicated agent slot if installed
GraphRouteLiteral = Literal["upstream", "primary", "agent"]
_VALID_GRAPH_ROUTES = frozenset({"upstream", "primary", "agent"})


class GraphUpstreamConfig(BaseModel):
    """[memory.graph.upstream] section — provider + model for route=upstream.

    Required when ``MemoryGraphConfig.route == "upstream"`` AND the
    graph-extraction feature is enabled. ``api_key`` is NEVER held here
    — credentials resolve from ``providers.toml`` via the existing
    ``ProviderEntry.auth_value_env`` indirection so secrets never land
    in a config file the dashboard reads.
    """

    model_config = {"populate_by_name": True, "extra": "allow"}

    provider: str = Field(
        default="openrouter",
        description=(
            "Upstream provider id, e.g. 'openrouter' | 'anthropic' | "
            "'openai' | 'custom'. Must match a configured provider in "
            "providers.toml so api_key resolution works."
        ),
    )
    model: str = Field(
        default="",
        description=(
            "Model id the provider understands, e.g. "
            "'anthropic/claude-3.5-sonnet'. Empty is rejected when the "
            "parent route == 'upstream' and enabled = true."
        ),
    )

    @field_validator("provider")
    @classmethod
    def provider_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("memory.graph.upstream.provider must not be empty")
        return v


class MemoryGraphConfig(BaseModel):
    """[memory.graph] section of hal0.toml (ADR-0014).

    Controls whether Cognee's graph-extraction pipeline runs after
    ``memory_add`` (the cognify step that pulls entities + relations
    via an LLM with structured output).

    ADR-0014 §1: defaults OFF in v0.3. Small local models flake on
    structured-output prompts; enabling silently would leave the graph
    view empty and confuse users. ADR-0014 §5: ``route = "upstream"`` is
    the default *suggestion* when the user toggles on but is NEVER the
    default behavior.
    """

    model_config = {"populate_by_name": True, "extra": "allow"}

    enabled: bool = Field(
        default=False,
        description=(
            "When False (the v0.3 default per ADR-0014 §1), memory_add "
            "skips the graph-extraction cognify pass. memory_search's "
            "vector mode keeps working — the gate only affects graph "
            "builds + graph/hybrid search modes."
        ),
    )
    route: GraphRouteLiteral = Field(
        default="upstream",
        description=(
            "Where to dispatch the graph-extraction LLM call when "
            "enabled. ADR-0014 §2: 'upstream' resolves via "
            "providers.toml, 'primary' uses the live primary slot, "
            "'agent' uses the dedicated agent slot."
        ),
    )
    upstream: GraphUpstreamConfig | None = Field(
        default=None,
        description=(
            "Provider + model for route='upstream'. None when route is "
            "'primary' or 'agent'. Required when enabled = true AND "
            "route == 'upstream'."
        ),
    )

    @field_validator("route")
    @classmethod
    def route_valid(cls, v: str) -> str:
        if v not in _VALID_GRAPH_ROUTES:
            raise ValueError(
                f"memory.graph.route {v!r} is not valid; choose from {sorted(_VALID_GRAPH_ROUTES)}"
            )
        return v

    @model_validator(mode="after")
    def route_upstream_requires_model(self) -> MemoryGraphConfig:
        """When enabled + route=upstream, demand provider + model.

        Only enforced when ``enabled = true`` so the install-time
        default (enabled=false, no upstream block) round-trips cleanly.
        A user toggling on through the dashboard hits this validator
        and gets a field-path error if they didn't supply the upstream
        block.
        """
        if not self.enabled:
            return self
        if self.route == "upstream" and (self.upstream is None or not self.upstream.model.strip()):
            raise ValueError(
                "memory.graph.upstream.{provider,model} required when "
                "enabled = true and route = 'upstream'"
            )
        return self


# ── AgentConfig (ADR-0013) ─────────────────────────────────────────────────────

# ADR-0013 §1: schema version pin so a future incompatible change
# (e.g. nesting tool policies under a `[mcp.servers.<name>.policy]`
# block) can detect + migrate old agent TOMLs without silent breakage.
AGENT_CONFIG_SCHEMA_VERSION = 1

# ADR-0013 §6: outbound auth styles. Today only ``bearer-from-env``
# (token loaded at agent-process startup from an env file or
# systemd-credential) is implemented. Listed as a frozenset so future
# additions (mtls, oauth-device-flow, …) extend a single source.
_VALID_AGENT_AUTH_KINDS = frozenset({"none", "bearer-from-env"})

AgentAuthKindLiteral = Literal["none", "bearer-from-env"]


class AgentAuthConfig(BaseModel):
    """``[mcp.servers.<name>.auth]`` block.

    ADR-0013 §6: indirection via env var keeps tokens out of TOML so
    the config file remains commit-safe and the dashboard can render
    it. The actual token is loaded by the agent driver at process
    startup (from systemd-credential or a 0600 env file) and never
    appears on the command line.
    """

    model_config = {"populate_by_name": True, "extra": "allow"}

    kind: AgentAuthKindLiteral = Field(
        default="none",
        description=(
            "Outbound auth style. 'none' = no auth header. "
            "'bearer-from-env' = read token from the env var named in `env`."
        ),
    )
    env: str | None = Field(
        default=None,
        description=(
            "Env-var name to read for the bearer token. Required when kind == 'bearer-from-env'."
        ),
    )

    @field_validator("kind")
    @classmethod
    def kind_valid(cls, v: str) -> str:
        if v not in _VALID_AGENT_AUTH_KINDS:
            raise ValueError(
                f"agent auth kind {v!r} not valid; choose from {sorted(_VALID_AGENT_AUTH_KINDS)}"
            )
        return v

    @model_validator(mode="after")
    def env_required_for_bearer(self) -> AgentAuthConfig:
        if self.kind == "bearer-from-env" and (not self.env or not self.env.strip()):
            raise ValueError("auth.env (env-var name) required when auth.kind = 'bearer-from-env'")
        return self


class ToolPolicy(BaseModel):
    """``[mcp.servers.<name>.tools]`` block — three-tier classification.

    ADR-0013 §4:

    - ``allow``  : autonomous call (no approval queue).
    - ``gated``  : enqueue via ADR-0004 approval queue, await user pick.
    - ``blocked``: hard-reject at the client; never reaches the server.

    The lists MUST be disjoint — overlap is operator error and surfaces
    as a load-time ValidationError with the offending tool name in the
    message, NOT a silent "which list wins?" decision.

    Default is empty on all three axes. Combined with the default-deny
    posture in :class:`MCPServerConfig`, that means an MCP server with
    no ``tools`` block has *zero* callable tools — which is what we
    want for a fresh registration the user hasn't reviewed yet.
    """

    model_config = {"populate_by_name": True, "extra": "allow"}

    allow: list[str] = Field(
        default_factory=list,
        description=("Tools the agent may call autonomously. Empty = no autonomous tools."),
    )
    gated: list[str] = Field(
        default_factory=list,
        description=(
            "Tools the agent may request; each call enqueues an "
            "approval (ADR-0004 §5). Empty = no gated tools."
        ),
    )
    blocked: list[str] = Field(
        default_factory=list,
        description=(
            "Tools hard-rejected at the client. Installer-pinned "
            "blocks (e.g. delete_repo on github-mcp) protect against "
            "dashboard edits surfacing dangerous tools."
        ),
    )

    @model_validator(mode="after")
    def lists_are_disjoint(self) -> ToolPolicy:
        """Reject overlap between allow / gated / blocked.

        Operators sometimes paste-edit a TOML and forget to remove a
        tool from one list when promoting/demoting it; we catch that
        at load time with a specific error so they don't ship a config
        whose behavior depends on whichever check happens first at
        dispatch.
        """
        allow_set = set(self.allow)
        gated_set = set(self.gated)
        blocked_set = set(self.blocked)
        overlaps: list[tuple[str, str, set[str]]] = [
            ("allow", "gated", allow_set & gated_set),
            ("allow", "blocked", allow_set & blocked_set),
            ("gated", "blocked", gated_set & blocked_set),
        ]
        for a, b, shared in overlaps:
            if shared:
                # Sort the offenders so error messages are deterministic
                # across dict-iteration orderings (matters for tests).
                names = sorted(shared)
                raise ValueError(
                    f"tools.{a} and tools.{b} overlap on {names!r}; "
                    "each tool must appear in at most one list"
                )
        return self


class MCPServerConfig(BaseModel):
    """One ``[mcp.servers.<name>]`` entry in an agent TOML.

    ADR-0013 §3: server-axis default-deny — only servers listed here
    are reachable. ``builtin = true`` marks hal0-admin / hal0-memory
    which are always reachable for bundled agents and can't be removed
    without an explicit override.
    """

    model_config = {"populate_by_name": True, "extra": "allow"}

    url: str | None = Field(
        default=None,
        description=(
            "MCP server URL. Empty for builtin servers (hal0 mounts "
            "those internally at /mcp/admin + /mcp/memory). Required "
            "for user-added servers — stdio:// for local processes, "
            "http(s):// for remote."
        ),
    )
    enabled: bool = Field(
        default=True,
        description=(
            "When False, the server registration round-trips through "
            "config but is not connected at agent startup."
        ),
    )
    builtin: bool = Field(
        default=False,
        description=(
            "ADR-0013 §6: marks hal0-admin / hal0-memory. Bundled-agent "
            "installers set this; user-added servers leave it False."
        ),
    )
    auth: AgentAuthConfig = Field(default_factory=AgentAuthConfig)
    tools: ToolPolicy = Field(default_factory=ToolPolicy)

    @model_validator(mode="after")
    def url_required_for_external(self) -> MCPServerConfig:
        """External (non-builtin) servers must declare a URL."""
        if not self.builtin and (self.url is None or not self.url.strip()):
            raise ValueError("mcp.servers.<name>.url required for non-builtin servers")
        return self


class AgentMetadataConfig(BaseModel):
    """``[agent]`` block — name + display + filesystem sandbox root."""

    model_config = {"populate_by_name": True, "extra": "allow"}

    name: str = Field(..., description="Agent identifier (e.g. 'hermes').")
    display: str = Field(
        default="",
        description="Human-readable label for the dashboard.",
    )
    workspace: str = Field(
        default="",
        description=(
            "Filesystem sandbox root (ADR-0013 §5). Empty falls back "
            "to the canonical /var/lib/hal0/agents/<name>/workspace at "
            "load time."
        ),
    )

    @field_validator("name")
    @classmethod
    def name_valid(cls, v: str) -> str:
        # Agent names land in filesystem paths + systemd unit names —
        # keep them strict (lowercase alphanumeric + hyphen, max 32 chars).
        import re

        if not v or not v.strip():
            raise ValueError("agent name must not be empty")
        if not re.match(r"^[a-z0-9][a-z0-9-]{0,31}$", v):
            raise ValueError(
                f"agent name {v!r}: use lowercase alphanumeric + hyphens "
                "(must start with alphanumeric, max 32 chars)"
            )
        return v


class AgentMCPConfig(BaseModel):
    """``[mcp]`` block container.

    Holds the ``servers`` map. Lives as its own model so future MCP-
    wide knobs (default-deny override, connect-timeout, retry-backoff)
    have an obvious home that round-trips through pydantic.
    """

    model_config = {"populate_by_name": True, "extra": "allow"}

    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    """Top-level shape of ``/etc/hal0/agents/<name>.toml`` (ADR-0013 §1).

    ADR-0013 §1 spells out one file per agent — installer for bundled,
    user for user-added. Preserved across ``hal0 update``. Schema
    validated at agent bootstrap time + on dashboard-edit save.

    The ``mcp.servers`` map is dict-of-:class:`MCPServerConfig`. Pydantic
    accepts dicts on a ``dict[str, ...]`` field natively, so the TOML
    shape ``[mcp.servers.filesystem]`` round-trips cleanly without a
    custom flattener.
    """

    model_config = {"populate_by_name": True, "extra": "allow"}

    schema_version: int = Field(
        default=AGENT_CONFIG_SCHEMA_VERSION,
        ge=1,
        description=(
            "Pin so an incompatible future change (e.g. nested tool "
            "policies) can detect old files + migrate."
        ),
    )
    agent: AgentMetadataConfig = Field(...)
    mcp: AgentMCPConfig = Field(default_factory=AgentMCPConfig)

    @field_validator("schema_version")
    @classmethod
    def schema_version_known(cls, v: int) -> int:
        # Reject future versions explicitly so a downgrade doesn't
        # silently accept a config it can't actually understand.
        if v > AGENT_CONFIG_SCHEMA_VERSION:
            raise ValueError(
                f"agent schema_version {v} is newer than this hal0 "
                f"understands ({AGENT_CONFIG_SCHEMA_VERSION}); "
                "upgrade hal0 or pin the agent config to an older version"
            )
        return v


class MemoryEmbeddingConfig(BaseModel):
    """[memory.embedding] section of hal0.toml (issue #116 G3 + G4).

    Pins the embedding model Cognee uses for memory vector retrieval and
    (optionally) wires a second-pass rerank slot in front of
    :meth:`hal0.memory.cognee_wrapper.CogneeWrapper.search`.

    Defaults are zero-config preserving:

      - ``model`` keeps Cognee's stock pick (``BAAI/bge-small-en-v1.5``,
        384-dim) so an upgrade does NOT silently re-embed an existing
        LanceDB index (dimension mismatch corrupts the store).
      - ``rerank_enabled`` is OFF by default; when flipped on, the
        wrapper calls ``rerank_url`` after vector retrieval and reorders
        candidates by relevance score. Failures fall through to the
        original vector ordering — never block memory_search.
      - ``rerank_url`` points at hal0's built-in rerank container slot
        (port 8083, seeded by ``installer/etc-hal0/slots/rerank.toml``).
        The old value 8086 was the retired embed-rerank combined slot;
        ``hal0.toml`` overrides take precedence over this default.

    G3 (pin embedding model) deliberately ships the *existing* default
    so users who do not flip the toggle see no behavioral change. Future
    work (audit doc §G3) may bump the default to ``bge-large-en-v1.5``
    once a migration story for the LanceDB index exists.
    """

    model_config = {"populate_by_name": True, "extra": "allow"}

    model: str = Field(
        default="BAAI/bge-small-en-v1.5",
        description=(
            "Embedding model the Cognee wrapper pins (issue #116 G3). "
            "Defaults to the existing Cognee stock value so the install "
            "default is byte-identical to v0.3.0 behavior — bumping this "
            "without a re-embed migration corrupts the LanceDB index."
        ),
    )
    rerank_enabled: bool = Field(
        default=False,
        description=(
            "When True, memory_search posts the vector top-N candidates "
            "to ``rerank_url`` and reorders by ``relevance_score`` before "
            "returning the top-K (issue #116 G4). When False (default), "
            "vector ordering is returned unchanged."
        ),
    )
    rerank_url: str = Field(
        default="http://127.0.0.1:8083",
        description=(
            "Base URL of the llama.cpp rerank endpoint. The wrapper "
            "POSTs to ``{rerank_url}/rerank`` with "
            "``{model, query, documents}`` per llama.cpp's reranking "
            "protocol. Defaults to hal0's bundled rerank container slot "
            "(port 8083). hal0.toml overrides win."
        ),
    )
    rerank_over_fetch_factor: int = Field(
        default=5,
        ge=1,
        le=20,
        description=(
            "Multiplier on ``limit`` used to determine the vector-search "
            "candidate count fed into the rerank pass. With the default of "
            "5, a ``limit=10`` search rerank-scores up to 50 candidates "
            "before clipping back to the top 10. Bumping this trades "
            "rerank latency for recall; dropping it toward 1 makes the "
            "rerank pass increasingly pointless."
        ),
    )
    rerank_max_candidates: int = Field(
        default=500,
        ge=10,
        le=2000,
        description=(
            "Absolute cap on candidates per rerank call, applied AFTER "
            "``rerank_over_fetch_factor`` so memory + latency stay bounded "
            "regardless of the requested ``limit``. The pre-PR hard-coded "
            "100 silently collapsed the over-fetch ratio at ``limit >= 20`` "
            "and made rerank a no-op at ``limit >= 100``."
        ),
    )
    rerank_connect_timeout_s: float = Field(
        default=1.0,
        ge=0.05,
        le=10.0,
        description=(
            "TCP connect timeout for the rerank HTTP call. Kept short so "
            "a wedged rerank slot can't stall memory_search; the read "
            "budget is the larger of the two knobs."
        ),
    )
    rerank_read_timeout_s: float = Field(
        default=8.0,
        ge=0.05,
        le=60.0,
        description=(
            "Read budget for the rerank slot. Default raised from the "
            "previous shared 2.0s scalar because GPU rerank under "
            "concurrent load (CPU oversubscription stalls responses) "
            "regularly breaches a "
            "tight total budget, which silently falls through to vector "
            "ordering. Failures still fall through — this just stops "
            "spurious timeouts under healthy-but-loaded conditions."
        ),
    )

    @field_validator("model")
    @classmethod
    def model_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("memory.embedding.model must not be empty")
        return v

    @field_validator("rerank_url")
    @classmethod
    def rerank_url_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("memory.embedding.rerank_url must not be empty")
        return v


class MemoryConfig(BaseModel):
    """[memory] section of hal0.toml.

    Container for the per-subsystem memory tunables. Today carries
    ``[memory.graph]`` (ADR-0014) and ``[memory.embedding]`` (issue
    #116). Future memory features (retention, prune-policy, archival)
    land under a single namespace rather than scattering top-level
    tables.
    """

    model_config = {"populate_by_name": True, "extra": "allow"}

    graph: MemoryGraphConfig = Field(default_factory=MemoryGraphConfig)
    embedding: MemoryEmbeddingConfig = Field(default_factory=MemoryEmbeddingConfig)
    engine: str = Field(
        default="hindsight",
        description=(
            "Active memory engine. One of 'cognee' | 'hindsight' | 'mem0' | "
            "'pgvector'. Default 'hindsight' (P2 cutover). Set to 'cognee' to "
            "revert to the untouched Cognee store for one release."
        ),
    )

    @field_validator("engine")
    @classmethod
    def _engine_is_known(cls, v: str) -> str:
        known = {"cognee", "hindsight", "mem0", "pgvector"}
        s = str(v or "cognee").strip().lower()
        if s not in known:
            raise ValueError(f"memory.engine {v!r} must be one of {sorted(known)}")
        return s


class ModelsConfig(BaseModel):
    """[models] section of hal0.toml — discovery + auto-detect."""

    model_config = {"populate_by_name": True, "extra": "allow"}

    roots: list[str] = Field(
        default_factory=lambda: [str(paths.models_dir())],
        description=(
            "Filesystem roots scanned for downloaded model files. "
            "Each must be an absolute path; non-existent paths are skipped at scan time. "
            "Default tracks HAL0_HOME for dev installs."
        ),
    )
    auto_scan_on_start: bool = Field(
        default=True,
        description="Run the discovery scan during app startup.",
    )
    file_extensions: list[str] = Field(
        default_factory=lambda: [".gguf", ".safetensors"],
        description=(
            "Filename suffixes treated as candidate model files (lowercase, includes the dot)."
        ),
    )
    pull_root: str = Field(
        default_factory=lambda: str(paths.models_dir()),
        description=(
            "DEPRECATED — superseded by ``[models].store``. Retained so PR #313 "
            "installs round-trip without a manual edit. When ``store`` is set the "
            "pull engine ignores this field; clearing ``store`` falls "
            "back to ``pull_root`` so an operator who hand-edited their TOML pre-store "
            "still works. Will be removed in a future release."
        ),
    )
    store: str = Field(
        default="",
        description=(
            "Single source of truth for where hal0 reads + writes model files. "
            "When set (absolute path, e.g. ``/mnt/ai-models``), the pull engine "
            "writes here AND slot containers bind-mount the path identical-path "
            "with an SELinux relabel (observed on the next slot restart; see "
            "``paths.model_store_root``). ``HAL0_MODEL_STORE`` env overrides it. "
            "Empty falls back to ``pull_root`` for PR-#313 compatibility, which "
            "itself defaults to ``paths.models_dir()``; note the mount default "
            "stays ``/mnt/ai-models`` (not ``pull_root``) so existing "
            "deployments are unaffected until ``store`` is set."
        ),
    )

    @field_validator("roots")
    @classmethod
    def roots_are_absolute(cls, v: list[str]) -> list[str]:
        """Reject relative paths — discovery walks must start from an absolute root."""
        out: list[str] = []
        for entry in v:
            s = str(entry).strip()
            if not s:
                raise ValueError("models.roots entries must not be empty")
            if not Path(s).is_absolute():
                raise ValueError(f"models.roots entry {s!r} must be an absolute path")
            out.append(s)
        return out

    @field_validator("pull_root")
    @classmethod
    def pull_root_is_absolute(cls, v: str) -> str:
        s = str(v).strip()
        if not s:
            raise ValueError("models.pull_root must not be empty")
        if not Path(s).is_absolute():
            raise ValueError(f"models.pull_root {s!r} must be an absolute path")
        return s

    @field_validator("store")
    @classmethod
    def store_is_absolute_when_set(cls, v: str) -> str:
        """Empty means "use pull_root"; non-empty must be absolute."""
        s = str(v or "").strip()
        if not s:
            return ""
        if not Path(s).is_absolute():
            raise ValueError(
                f"models.store {s!r} must be an absolute path (or empty to use pull_root fallback)"
            )
        return s

    def effective_store(self) -> str:
        """Return the resolved model-store path consumers should point at.

        Precedence: ``store`` (the new single-source-of-truth field) wins
        when set; otherwise we fall back to the deprecated ``pull_root``
        so PR-#313 installs keep working without an edit. Both already
        validate as absolute paths.
        """
        if self.store:
            return self.store
        return self.pull_root


class ActivityConfig(BaseModel):
    """``[activity]`` — the durable audit/activity store (see hal0.activity).

    Records every config-mutating action and system state change to a SQLite
    table that survives restarts. ``retention_days`` and ``max_rows`` keep the
    DB bounded without losing recent history. ``HAL0_ACTIVITY_RETENTION_DAYS``
    overrides retention at the env layer.
    """

    enabled: bool = True
    retention_days: int = Field(default=30, ge=1)
    # None disables the row cap (retention_days still applies).
    max_rows: int | None = Field(default=50_000, ge=100)


class Hal0Config(BaseModel):
    """Top-level hal0.toml pydantic model.

    Populated by hal0.config.loader.load_hal0_config() at startup.
    Unknown top-level keys are accepted and stored via extra='allow' to
    allow forward compatibility with future schema versions.
    """

    # NOTE: extra="allow" keeps round-trip fidelity for unrecognized
    # top-level tables — e.g. a future [paths] section a newer hal0
    # version writes won't be dropped when an older hal0 reads the file.
    model_config = {"populate_by_name": True, "extra": "allow"}

    meta: MetaConfig = Field(default_factory=MetaConfig)
    slots: SlotsConfig = Field(default_factory=SlotsConfig)
    dispatcher: DispatcherConfig = Field(default_factory=DispatcherConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    activity: ActivityConfig = Field(default_factory=ActivityConfig)


__all__ = [
    "AGENT_CONFIG_SCHEMA_VERSION",
    "BACKEND_TO_DEVICE",
    "CAPABILITIES_SCHEMA_VERSION_CURRENT",
    "CAPABILITIES_SCHEMA_VERSION_LEGACY",
    "CURRENT_SCHEMA_VERSION",
    "DEFAULT_DEVICE",
    "DEVICE_DEFAULT_PROFILES",
    "MTP_FLAG_BUNDLE",
    "PROFILE_BENCH",
    "SEED_PROFILES",
    "ActivityConfig",
    "AgentAuthConfig",
    "AgentAuthKindLiteral",
    "AgentConfig",
    "AgentMCPConfig",
    "AgentMetadataConfig",
    "DeviceLiteral",
    "DispatcherConfig",
    "GPUInfo",
    "GraphRouteLiteral",
    "GraphUpstreamConfig",
    "Hal0Config",
    "HardwareInfo",
    "ImageGenConfig",
    "MCPServerConfig",
    "MemoryConfig",
    "MemoryEmbeddingConfig",
    "MemoryGraphConfig",
    "MetaConfig",
    "ModelConfig",
    "ModelsConfig",
    "NPUInfo",
    "NpuConfig",
    "ProfileConfig",
    "ProfilesConfig",
    "ProviderEntry",
    "ProvidersConfig",
    "ServerConfig",
    "SlotConfig",
    "SlotsConfig",
    "TelemetryConfig",
    "ToolPolicy",
    "UpstreamEntry",
    "UpstreamsConfig",
    "map_backend_to_device",
    "resolve_profile_flags",
]
