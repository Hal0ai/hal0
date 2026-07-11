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
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

from hal0.config import paths
from hal0.model_meta import (
    BACKEND_TO_DEVICE,
    DEFAULT_DEVICE,
    DEVICE_TO_DEFAULT_PROFILE,
    map_backend_to_device,
)
from hal0.model_meta import (
    LEGACY_BACKENDS as _LEGACY_BACKENDS,
)
from hal0.model_meta import (
    VALID_DEVICES as _VALID_DEVICES,
)

log = logging.getLogger(__name__)

# ── Shared constants ───────────────────────────────────────────────────────────
#
# The identity vocabularies (device enum, legacy backend enum, the
# backend→device map, per-device default profiles) live in ONE place —
# ``hal0.model_meta`` (see its module docstring for the full vocabulary
# table + unknown-value policy). This module re-exports them so every
# existing ``from hal0.config.schema import …`` call site keeps working.
# model_meta imports nothing from schema, so the dependency is one-way.

# TIER1: surface-area for the backend whitelist. Typos like
# `backend = "vukan"` must raise at load time with the field path.
#
# DEPRECATED v0.2: ``SlotConfig.backend`` is being retired in favour of the
# hardware-preference field ``SlotConfig.device``. The whitelist is kept for
# one release so legacy slot TOMLs round-trip cleanly; a warning is logged
# whenever ``backend`` is read without an accompanying ``device``. See
# ADR-0006 §7 (v0.2 migration plan, decision 15).
_VALID_BACKENDS = frozenset(_LEGACY_BACKENDS)

# v0.2 hardware-preference enum. ``device`` replaces the overloaded
# ``backend`` field — it carries hardware intent only, not provider choice.
# ``hal0.model_meta.device_to_backend`` maps these to the recipe:backend
# pair that feeds container profile/argv derivation. The Literal must match
# ``hal0.model_meta.VALID_DEVICES`` (asserted by tests/model_meta).
DeviceLiteral = Literal["gpu-rocm", "gpu-vulkan", "gpu-cuda", "cpu", "npu"]

# Valid provider names. ContainerProvider drives every slot lifecycle;
# the pre-container names remain accepted so legacy slot TOMLs round-trip
# without raising — the provider field exists only for round-trip + UI
# label compatibility. ``"comfyui"`` is the exception: it is the active
# container image-gen provider (img.toml, ADR image slots), not a
# deprecated legacy value.
_VALID_PROVIDERS = frozenset({"llama-server", "flm", "moonshine", "kokoro", "qwen3tts", "comfyui"})

# Slot port range.  8080 is the hal0 API; slots get 8081-8099; 8188 =
# ComfyUI's stock port for the img slot — kept well-known so operator
# bookmarks/tooling keep working.
#
# Two distinct bounds on purpose:
#   * _SLOT_PORT_MIN/_SLOT_PORT_MAX bound what a single slot's ``port``
#     field may be — wide enough to admit the img slot's 8188.
#   * _SLOT_PORT_POOL_END is the default END of the AUTO-ALLOCATION pool
#     ([slots].port_range_end) — deliberately below 8188 so freshly
#     created slots can never squat on ComfyUI's port. Operators may
#     widen the pool in hal0.toml; the allocator still skips ports
#     claimed by existing slot TOMLs.
_SLOT_PORT_MIN = 8081
_SLOT_PORT_MAX = 8200
_SLOT_PORT_POOL_END = 8099

#: A valid POSIX environment-variable name — used to validate [server].env keys.
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

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


# NOTE: ``map_backend_to_device`` now lives in ``hal0.model_meta`` (imported
# above and re-exported via ``__all__``) so the legacy→device translation and
# its unknown-value policy are defined exactly once.


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
    context_size: int | None = Field(
        default=None,
        ge=128,
        description=(
            "Context window size in tokens. Unset (None) is NOT 4096: the "
            "load path derives the model's native window (dense-capped) or a "
            "safe 8192 floor, so a slot never silently inherits llama-server's "
            "4096 default (chat@4096 incident, 2026-06-15)."
        ),
    )
    n_gpu_layers: int = Field(
        default=-1,
        description="Number of layers to offload to GPU.  -1 means all.",
    )
    rope_freq_base: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "DEPRECATED (accepted, ignored): the launch path no longer emits "
            "--rope-freq-base from this field. To override RoPE base, pass "
            "``--rope-freq-base <n>`` via [server].extra_args instead. Retained "
            "so existing TOMLs round-trip; 0.0 means use the model default."
        ),
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
    chat: bool = Field(
        default=True,
        description="Enable chat (LLM) modality on the FLM slot. Default ON.",
    )
    # NOTE: FLM has no per-role model selection — ``--asr`` / ``--embed`` are
    # boolean flags that load FLM's single bundled whisper + embed-gemma. There
    # is deliberately no asr_model/embed_model here; the chat model is the
    # ``flm serve`` positional tag ([model].default) and is the only choice.


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
            "Freeform llama-server CLI passthrough.  Tokenised via shlex and "
            "appended last in the launch argv; hal0.slots.argv.normalize_argv "
            "then collapses cross-source duplicates last-wins, so a flag set here "
            "overrides the same flag from the profile / model defaults "
            "(append-list flags like --lora / --draft-model / --override-kv are kept)."
        ),
    )
    env: dict[str, str] | None = Field(
        default=None,
        description=(
            "Environment variables injected into the slot container via "
            "docker/podman ``--env`` (e.g. HSA_OVERRIDE_GFX_VERSION). Lets an "
            "operator tune the runtime without forking the toolbox image. Keys "
            "must be valid env-var names ([A-Za-z_][A-Za-z0-9_]*); values must "
            "not contain newlines."
        ),
    )

    @field_validator("env")
    @classmethod
    def _env_keys_and_values_sane(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        """Reject non-env-var-name keys and multi-line values.

        A stray newline in a value would break the rendered ``--env=K=V``
        ExecStart line (systemd is line-oriented); an invalid key name would
        be silently ignored or mangled by the container runtime.
        """
        if v is None:
            return v
        cleaned: dict[str, str] = {}
        for key, value in v.items():
            if not _ENV_VAR_NAME_RE.match(str(key)):
                raise ValueError(
                    f"[server].env key {key!r} is not a valid environment variable name "
                    "(must match [A-Za-z_][A-Za-z0-9_]*)"
                )
            sval = str(value)
            if "\n" in sval or "\r" in sval:
                raise ValueError(f"[server].env value for {key!r} must not contain newlines")
            cleaned[str(key)] = sval
        return cleaned


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
            "v0.2 hardware-preference enum: 'gpu-rocm' | 'gpu-vulkan' | "
            "'gpu-cuda' | 'cpu' | 'npu'. Replaces the legacy ``backend`` "
            "field which mixed providers and backends. See ADR-0006 §7."
        ),
    )
    gpu_index: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Pin this slot to one GPU by index on multi-GPU hosts. Emits the "
            "backend-appropriate visibility env into the container "
            "(gpu-rocm → HIP_VISIBLE_DEVICES + ROCR_VISIBLE_DEVICES, "
            "gpu-vulkan → GGML_VK_VISIBLE_DEVICES) or maps only that GPU via "
            "CDI (gpu-cuda → --device nvidia.com/gpu=<n> with "
            "CUDA_VISIBLE_DEVICES=0 inside). Explicit [server].env keys win "
            "over the derived visibility vars. None (default) = all GPUs, "
            "unchanged behaviour."
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
            "true → force on; false → force off; None → AUTO. Auto enables MTP only "
            "when the profile opts in (profile.mtp) AND the model actually ships MTP "
            "heads (registry `mtp` tag or an MTP name marker), so a non-MTP model on "
            "an MTP profile no longer launches with dead --spec-draft-* flags. "
            "See providers.container._effective_mtp and build_mtp_flag_bundle."
        ),
    )
    parallel: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Per-slot llama-server sequence slots (--parallel / -np) for continuous "
            "batching: concurrent requests share the once-loaded weights instead of "
            "serializing through a single sequence and thrashing one prompt cache. "
            "None = inherit the profile flags (today: 1). When >1, --kv-unified is "
            "emitted alongside so --ctx-size stays a SHARED pool (each request may "
            "use up to the full context) instead of being silently split to ctx/N "
            "per slot. Interactive slots want low N (per-stream speed ~= 1/N); agent "
            "fan-in slots want 4-8 (bench-gated). See "
            "providers.container._effective_parallel."
        ),
    )
    chat_template: str | None = Field(
        default=None,
        description=(
            "Per-slot chat-template override (id from /api/chat-templates, or "
            "'auto'/None for the GGUF-embedded template). Wins over the model's "
            "default. See resolve_chat_template."
        ),
    )
    vision: bool = Field(
        default=True,
        description=(
            "Per-slot vision toggle (#901). When the bound model carries an "
            "mmproj sidecar, the container provider loads it (--mmproj) so the "
            "slot accepts images — default-on. Set false to boot the slot "
            "text-only (no --mmproj, modalities.vision:false) on memory-tight "
            "hosts; the projector is ~0.9 GB resident. No effect when the model "
            "has no sidecar."
        ),
    )

    # ── TTS request defaults (Settings → Voice) ─────────────────────────
    # Read by /v1/audio/speech at request time: when the body omits the
    # matching param, the serving tts slot's persisted default is injected
    # before dispatch — so changes apply immediately, no container bounce.
    # default_voice previously round-tripped via extra="allow" only (the
    # dashboard wrote it, nothing read it); declaring the trio makes the
    # values validated and schema-visible.
    default_voice: str | None = Field(
        default=None,
        description=(
            "TTS slots only — voice id injected into /v1/audio/speech when "
            "the request omits `voice`. None → engine default (Kokoro: "
            "af_bella)."
        ),
    )
    default_speed: float | None = Field(
        default=None,
        ge=0.25,
        le=4.0,
        description=(
            "TTS slots only — playback speed injected when the request omits "
            "`speed`. Engines clamp to their supported range (Kokoro: "
            "0.5-2.0). None → engine default (1.0)."
        ),
    )
    default_response_format: Literal["mp3", "wav", "opus", "flac", "pcm"] | None = Field(
        default=None,
        description=(
            "TTS slots only — audio container injected when the request "
            "omits `response_format`. None → engine default (mp3)."
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
        description=(
            "DEPRECATED / inert — a haloai-era field that was never emitted to "
            "llama-server argv (it did not mean sequence slots). Round-tripped for "
            "one release; a non-default value logs a warning at launch and does "
            "nothing. For continuous batching use the `parallel` field, which maps "
            "to llama-server --parallel."
        ),
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

#: MTP draft-speculation draft device, per profile backend.  The old bundle
#: hardcoded ``ROCm0``, so a Vulkan/CUDA profile with MTP on drafted on a ROCm
#: device.  Unknown / non-GPU / ``None`` backends keep the historical ``ROCm0``
#: default (byte-identical to the old constant).
_MTP_DRAFT_DEVICE: dict[str, str] = {"rocm": "ROCm0", "vulkan": "Vulkan0", "cuda": "CUDA0"}


def build_mtp_flag_bundle(backend: str | None) -> str:
    """Bench-tuned MTP draft-speculation flag bundle, with the draft device
    derived from *backend*.

    Appended after ``profile.flags`` when MTP is effective (see
    :func:`resolve_profile_flags`).  Every ``--spec-draft-*`` value here is a
    DEFAULT — a model may override any of them via its registry
    ``defaults.extra_args`` (``merge_flags`` precedence).  Keep the values in
    sync with the bench doc (hal0-container-bench-2026-06-08.md).
    """
    device = _MTP_DRAFT_DEVICE.get((backend or "").lower(), "ROCm0")
    return (
        "--spec-type draft-mtp"
        f" --spec-draft-device {device}"
        " --spec-draft-ngl all"
        " --spec-draft-n-max 4"
        " --spec-draft-n-min 0"
        " --spec-draft-p-min 0.0"
        " --spec-draft-p-split 0.10"
        " --spec-draft-type-k q8_0"
        " --spec-draft-type-v q8_0"
        " --spec-draft-threads 16"
        " --spec-draft-threads-batch 32"
        " --spec-draft-poll 1"
        " --spec-draft-poll-batch 1"
    )


#: Back-compat: the ROCm-flavoured bundle (the seed MTP profiles are all ROCm).
#: Prefer :func:`build_mtp_flag_bundle` so the draft device tracks the backend.
MTP_FLAG_BUNDLE = build_mtp_flag_bundle("rocm")

#: Seed profiles shipped with hal0.  Returned by ``load_profiles_config()``
#: when ``/etc/hal0/profiles.toml`` is absent so ``GET /api/profiles`` is
#: always populated on a fresh install.
#:
#: ``DEFAULT_ROCMFPX_IMAGE`` is the canonical ROCmFPX runner tag. It backs the
#: ``rocmfpx-rocm`` / ``vkfpx-moe`` / ``vkfpx-dense`` seed profiles and is the
#: fallback used by :func:`hal0.providers.container._resolve_image_ref` when
#: neither a slot-level override nor a profile-level ``image`` is present.
#: Bumping this constant in a release rolls the default runner for every
#: fresh install and every slot that hasn't pinned an image explicitly —
#: the slot-level ``image`` override is the lever for per-slot opt-outs
#: (debug builds, A/B tests, etc.).  See #__hal0_image_control__ for the
#: phasing: 0.9.5 wires slot.image + DEFAULT_ROCMFPX_IMAGE; 0.9.6 will
#: drop ``image`` from SEED_PROFILES entirely.
DEFAULT_ROCMFPX_IMAGE = "ghcr.io/hal0ai/hal0-rocmfpx:c077206"

#: Historical DEFAULT_ROCMFPX_IMAGE values (and their pre-consolidation
#: equivalents). A slot-level ``image`` pin equal to one of these is a STALE
#: FORMER DEFAULT — debris from slot creation under an older release — not a
#: deliberate operator opt-out, so the updater retags it to the current
#: default (:func:`hal0.updater.updater.retag_stale_slot_images`). A pin to
#: any ref NOT in this set is treated as intentional and never touched.
#:
#: c077206 lineage (2026-07-10): charlie ROCmFPX main @5b39566 (FP6 CPU
#: decode fix, MTP partial-draft-state fix, vecdotq pack window) + the
#: minicpm5 pre-tokenizer vocab mapping (ac0137d) + TurboQuant turbo3/turbo4
#: KV-cache types; image assembly drops the base toolbox's stale
#: /usr/local llama.cpp (the old mixed install ABI-segfaulted llama-bench).
STALE_ROCMFPX_IMAGE_REFS = frozenset(
    {
        "ghcr.io/hal0ai/hal0-rocmfpx:vulkan-minicpm5",
        "localhost/hal0-rocmfpx:vulkan-minicpm5",
        "ghcr.io/hal0ai/hal0-rocmfpx:server",
        "ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocmfpx-7aa484a",
    }
)

#: Seed profile catalog.  Slugs are backend-agnostic workload names — the
#: ``backend`` field (not the slug) carries the ROCm/Vulkan choice, and the
#: card chip renders the backend as colour, so the slug no longer repeats it.
#: GPU profiles set ``backend``; non-GPU profiles (npu/cpu/img) omit it and
#: let ``device_class`` drive display.
#:
#: The three ROCmFPX runner profiles (``rocmfpx-rocm``, ``vkfpx-moe``,
#: ``vkfpx-dense``) keep their ``image`` field for now (Phase 1, 0.9.5)
#: so existing custom profiles that depend on it round-trip cleanly.
#: Operators are encouraged to override per-slot (``image = "..."`` at the
#: top of ``/etc/hal0/slots/<name>.toml``) so future image bumps are a
#: code-only release, not a per-profile migration.
SEED_PROFILES: dict[str, dict[str, object]] = {
    "rocm": {
        # Basic general-purpose ROCm GPU LLM profile (Strix Halo toolbox image).
        # Intentionally minimal: -ngl 999 (offload all), -fa on (flash attn),
        # --jinja (chat templating). Per-model KV/batch/MTP tuning lives in the
        # model's defaults.extra_args. mtp stays False — the ROCmFPX MTP lanes
        # live in rocm-dense / rocm-moe (and the vulkan-dense / vulkan-moe
        # pair on the Vulkan backend); the old rocmfpx-rocm / vkfpx-* slugs
        # were consolidated into the 2x2 (backend x {dense,moe}) grid in 0.9.5.
        "image": "ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server",
        "flags": "-ngl 999 -fa on --jinja",
        "mtp": False,
        "device_class": "gpu",
        "backend": "rocm",
        "intent": "ROCm",
        "quant": "FP4",
    },
    # ROCmFPX runner — 2x2 grid (backend x {dense,moe}). Replaces the
    # rocmfpx-rocm / vkfpx-moe / vkfpx-dense slugs (consolidated 0.9.5).
    # Image = DEFAULT_ROCMFPX_IMAGE (see the constant for lineage).
    "rocm-dense": {
        # ROCm0/HIP + ROCmFP4 DENSE weights. Sustained-decode win on Strix Halo
        # (operator memory: ROCm lane wins sustained decode, Vulkan lane wins
        # prefill by ~+24% PP). --no-mmap + --ctx-checkpoints are the dense
        # workload's preferred memory pattern; per-model KV + spec-draft
        # tuning come from the model's defaults.extra_args.
        "image": DEFAULT_ROCMFPX_IMAGE,
        "flags": "-ngl 999 -fa on -dev ROCm0 -b 512 -ub 512 --parallel 1 --threads 16 --threads-batch 32 --no-mmap --jinja --metrics --no-webui --ctx-checkpoints 0 --checkpoint-every-n-tokens -1",
        "mtp": True,
        "device_class": "gpu",
        "backend": "rocm",
        "intent": "ROCmFPX · DENSE · MTP (sustained-decode)",
        "quant": "ROCmFP4",
    },
    "rocm-moe": {
        # ROCm0/HIP + ROCmFPX MoEQuality weights. Vulkan is the recommended
        # lane for MoE sustained-decode (better t/s); this profile is the
        # prefill-bound / -dev ROCm0 opt-in for operators who want the same
        # backend lane across all their ROCmFPX slots. -sm none (single GPU)
        # and --no-context-shift per the validated Tool-Eval card.
        "image": DEFAULT_ROCMFPX_IMAGE,
        "flags": "-ngl 999 -fa on -dev ROCm0 -sm none -b 2048 -ub 512 --parallel 1 --threads 16 --threads-batch 32 --no-context-shift --jinja --metrics --no-webui",
        "mtp": True,
        "device_class": "gpu",
        "backend": "rocm",
        "intent": "ROCmFPX · MOE · MTP (prefill-bound / -dev ROCm0)",
        "quant": "ROCmFPX",
    },
    "vulkan-dense": {
        # Vulkan0 + ROCmFP4 DENSE weights. Prefill win (~+24% PP) for prefill-
        # bound dense workloads (RAG / long-context reads / re-prefill after a
        # cache miss). Small ubatch wins on gfx1151; per-model KV + spec-draft
        # tuning come from the model's defaults.extra_args.
        "image": DEFAULT_ROCMFPX_IMAGE,
        "flags": "-ngl 999 -fa on -dev Vulkan0 -b 512 -ub 512 --parallel 1 --threads 16 --threads-batch 32 --no-context-shift --jinja --metrics --no-webui",
        "mtp": True,
        "device_class": "gpu",
        "backend": "vulkan",
        "intent": "VULKFPX · DENSE · MTP (prefill-bound)",
        "quant": "ROCmFP4",
    },
    "vulkan-moe": {
        # Vulkan0 + ROCmFPX MoEQuality weights. Best sustained-decode t/s for
        # MoE on Strix Halo. The validated Tool-Eval card. External chat
        # templates (e.g. Froggeric Qwen fixed) are set per-slot via
        # [server].extra_args.
        "image": DEFAULT_ROCMFPX_IMAGE,
        "flags": "-ngl 999 -fa on -dev Vulkan0 -sm none -b 2048 -ub 512 --parallel 1 --threads 16 --threads-batch 32 --no-context-shift --jinja --metrics --no-webui",
        "mtp": True,
        "device_class": "gpu",
        "backend": "vulkan",
        "intent": "VULKFPX · MOE · MTP (best decode t/s)",
        "quant": "ROCmFPX",
    },
    "vulkan": {
        # Basic general-purpose Vulkan (RADV) GPU LLM profile. Intentionally
        # minimal: -ngl 999, -fa on, --jinja. No KV quant (defaults to f16, which
        # is gemma-safe) — per-model KV/batch tuning lives in the model's
        # defaults.extra_args.
        "image": "ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server",
        "flags": "-ngl 999 -fa on --jinja",
        "mtp": False,
        "device_class": "gpu",
        "backend": "vulkan",
        "intent": "Vulkan",
        "quant": "Q4_K_M",
    },
    "cuda": {
        # NVIDIA GPUs via llama.cpp CUDA — experimental on hal0. The image is
        # UPSTREAM llama.cpp (ghcr.io/ggml-org/llama.cpp:server-cuda), not the
        # AMD Strix-Halo toolbox family the other GPU profiles use. Flags
        # mirror the vulkan profile's conservative structure (no AMD-specific
        # tuning, no KV-quant assumptions); requires nvidia-container-toolkit
        # (CDI) for GPU passthrough — see providers/_gpu.nvidia_cdi_devices.
        "image": "ghcr.io/ggml-org/llama.cpp:server-cuda",
        "flags": "-ngl 999 -fa on -b 512 -ub 512 --parallel 1 --threads 8 --no-mmap --jinja",
        "mtp": False,
        "device_class": "gpu",
        "backend": "cuda",
        "intent": "CUDA · experimental",
        "quant": "Q4_K_M",
    },
    "embed": {
        # GPU embedding template (llama-server --embedding). Serves
        # /v1/embeddings for Qwen3-Embedding / nomic / bge GGUFs. -ub must
        # cover the longest single input: pooled embeddings run the whole
        # sequence in ONE physical ubatch, so -ub 8192 (== -b) matches the
        # 8k-token models and larger inputs would truncate/fail on a smaller
        # ubatch (llama.cpp #6263/#11105). Pooling is left to GGUF metadata
        # (Qwen3-Embedding pins --pooling last via its model defaults); no KV
        # quant — meaningless for a single-pass encoder. GPU because these
        # tiny encoders are prefill-bound and cost ~nothing in the 128 GB pool.
        "image": "ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server",
        "flags": "--embedding -ngl 999 -fa on -b 8192 -ub 8192 --no-mmap",
        "mtp": False,
        "device_class": "gpu",
        "backend": "rocm",
        "intent": "Embeddings",
        "quant": "",
    },
    "rerank": {
        # GPU reranker template (llama-server --reranking → /v1/rerank, implies
        # embedding-mode + rank pooling). Sized for bge-reranker-v2-m3
        # (8192-token query+doc pairs): -ub 8192 must cover the longest pair or
        # the request truncates. MUST be a SEPARATE instance from `embed` —
        # combining --embedding and --reranking on one server yields all-zero
        # scores (llama.cpp #20085). For parallel scoring raise ctx via the
        # slot (-c 65536 --parallel 8 = n_seq x 8192, ggerganov's PR #9510).
        "image": "ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server",
        "flags": "--reranking -ngl 999 -fa on -b 8192 -ub 8192 --no-mmap",
        "mtp": False,
        "device_class": "gpu",
        "backend": "rocm",
        "intent": "Reranking",
        "quant": "",
    },
    "flm": {
        "image": "ghcr.io/hal0ai/hal0-toolbox-flm:0.9.44",
        "flags": "",
        "mtp": False,
        "device_class": "npu",
        "intent": "FLM · NPU",
        "quant": "W4ABF16",
    },
    "tts": {
        "image": "ghcr.io/hal0ai/hal0-toolbox-kokoro:v1",
        "flags": "--model_path /mnt/ai-models/local/kokoro-v1/kokoro-onnx",
        "mtp": False,
        "device_class": "cpu",
        "intent": "TTS · CPU",
        "quant": "",
    },
    "tts-qwen3": {
        "image": "ghcr.io/hal0ai/hal0-toolbox-qwen3tts:v1",
        "flags": (
            "--model_path /mnt/ai-models/local/qwen3-tts/Qwen3-TTS-12Hz-1.7B-CustomVoice "
            "--default_voice Ryan --default_language Auto"
        ),
        "mtp": False,
        "device_class": "gpu",
        "backend": "rocm",
        "intent": "TTS · GPU",
        "quant": "BF16",
    },
    "cpu-llm": {
        # The Vulkan toolbox image runs in CPU-only mode when no GPU devices
        # are passed to the container (llama-server auto-selects GGML_CPU).
        # CPU-optimal flags: no flash-attn (not available without GPU), smaller
        # batch to limit peak RAM, and a thread count sensible for a typical
        # multi-core host.  backend=None keeps the #807 coherence check happy.
        "image": "ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server",
        "flags": "--threads 4 --threads-batch 8 -b 256 -ub 256 --parallel 1 --no-mmap --jinja",
        "mtp": False,
        "device_class": "cpu",
        "intent": "CPU",
        "quant": "Q4_K_M",
    },
    "comfyui": {
        "image": "docker.io/kyuz0/amd-strix-halo-comfyui@sha256:0066678ae9043f69a1c8c7699e70626ceffd35c1a8ca03227a05640ad0241ed2",
        "flags": "--disable-mmap --bf16-vae --cache-none",
        "mtp": False,
        "device_class": "img",
        "intent": "ComfyUI",
        "quant": "",
    },
}

#: Static bench numbers for seed profiles, surfaced as the card hero metric.
#: ``tps`` = tokens/sec (LLM throughput); ``rtf`` = real-time factor (synth,
#: e.g. TTS).  Grounded in hal0-container-bench-2026-06-08.md.  Custom
#: profiles have no entry → the card shows "—" until benched.
#: Unchanged by the 2026-07-04 Strix Halo flag re-tune: every adopted change
#: (rocm-moe -ub 1024, vulkan -ub 256, dropped threads-batch/poll) is a prefill
#: (pp) win — token-generation throughput was flat across all matrix cells, so
#: these decode-based hero numbers still hold.
PROFILE_BENCH: dict[str, dict[str, float]] = {
    "rocm": {"tps": 52.8},
    "vulkan": {"tps": 41.0},
    "flm": {"tps": 38.6},
    "tts": {"rtf": 0.18},
    # Native gfx1151 ~2.1x realtime -> rtf ~= 1/2.1 (memory qwen3tts-voice-ct105).
    "tts-qwen3": {"rtf": 0.48},
}

#: Per-family llama-server flag overrides — the "model-architecture quirks"
#: layer, distinct from profiles (backend/hardware tuning) and slot config
#: (per-instance).  Applied when a slot's model resolves to the family; each
#: string merges into the ``model_defaults`` argv segment, so it OVERRIDES the
#: profile's generic flags (``normalize_argv`` keeps the last occurrence) but a
#: per-slot ``[model].defaults.extra_args`` still beats it.  Virtual like
#: SEED_PROFILES — ships to every install, never persisted to config.
FAMILY_DEFAULTS: dict[str, str] = {
    # Gemma is an iSWA (interleaved sliding-window) architecture: quantized KV
    # regresses prompt-processing — measured 2026-07-04 on gemma-4-12B @32k depth,
    # -ctk/-ctv q8_0 costs -28.5% pp on RADV / -10% tg on rocm vs f16 (the mirror
    # of qwen's +45% q8 gain) — and SWA + cache-reuse has upstream bugs
    # (#21468/#21749).  So any gemma model, on any q8 profile, is pinned back to
    # f16 KV with cache-reuse off.
    "gemma": "-ctk f16 -ctv f16 --cache-reuse 0",
}

#: Families FAMILY_DEFAULTS can key on, matched as a token in the model id /
#: filename.  GGUF ``general.architecture`` would be the canonical signal, but
#: it is not persisted on registry rows today (auto-scan stores only
#: ``{discovered, source}``); the id/filename carries the family reliably for
#: the GGUF fleet, and arch-from-header is the future hardening.
_KNOWN_FAMILIES: tuple[str, ...] = ("gemma", "qwen", "llama", "phi", "mistral", "deepseek")


def model_family(*hints: str | None) -> str | None:
    """Best-effort model family from id/name/path hints (lower-cased token scan).

    Returns the first :data:`_KNOWN_FAMILIES` token found across the hints, or
    ``None``.  Cheap and side-effect-free so the launch + preview argv paths can
    both call it.
    """
    hay = " ".join(h for h in hints if h).lower()
    return next((fam for fam in _KNOWN_FAMILIES if fam in hay), None)


def family_flags(*hints: str | None) -> str:
    """The :data:`FAMILY_DEFAULTS` flag string for the model's family, else ''."""
    fam = model_family(*hints)
    return FAMILY_DEFAULTS.get(fam, "") if fam else ""


#: Preselect map for the create-modal device picker and legacy-slot
#: migration defaults.  Keys are ``DeviceLiteral`` values (gpu-rocm,
#: gpu-vulkan, cpu, npu); values are seed profile names that best represent
#: each device class.  Alias of the canonical
#: :data:`hal0.model_meta.DEVICE_TO_DEFAULT_PROFILE` (kept under the old
#: name so existing imports don't break).
DEVICE_DEFAULT_PROFILES: dict[str, str] = DEVICE_TO_DEFAULT_PROFILE


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
    backend: Literal["rocm", "vulkan", "cuda"] | None = Field(
        default=None,
        description=(
            "GPU runtime this profile targets — the authoritative source for the "
            "ROCm-vs-Vulkan-vs-CUDA choice (replaces sniffing the image tag).  "
            "``None`` for non-GPU profiles (npu/cpu/img), where ``device_class`` "
            "drives display and slot-card colour."
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


# ── Stacks ────────────────────────────────────────────────────────────────────
# A Stack is a named, portable bundle of slots + their profiles + model
# assignments + capability selections. Stored single-file in stacks.toml keyed
# by slug, mirroring profiles.toml. See docs/superpowers/specs/2026-06-19-stacks-design.md.

# Stacks carry their own schema version (independent of hal0.toml meta.schema_version),
# stamped on every StackConfig and on the export envelope (PR-3).
STACK_SCHEMA_VERSION_CURRENT = 1

#: Profile export envelopes carry their own schema version (independent of
#: hal0.toml meta.schema_version), stamped on every ``.hal0profile.json`` export.
PROFILE_SCHEMA_VERSION_CURRENT = 1

_STACK_NAME_RE = r"^[a-z0-9][a-z0-9_-]{0,31}$"


class StackModelMeta(BaseModel):
    """Transport-safe metadata subset of a registry ``Model``.

    Embedded in a stack so an importer on another machine can resolve or
    pull a referenced model by id. Deliberately excludes the machine-specific
    ``path`` and any host-local fields — see spec §3/§6.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    id: str = Field(..., description="Registry model id this entry describes.")
    name: str = Field(default="", description="Human-readable display name.")
    hf_repo: str = Field(
        default="", description="HuggingFace repo id, for resolve-and-pull on import."
    )
    hf_filename: str = Field(default="", description="Filename within the HF repo.")
    size_bytes: int = Field(default=0, description="Total model size in bytes; 0 = unknown.")
    quant: str = Field(
        default="", description="Quantization label shown on cards (e.g. 'FP4', 'Q4_K_M')."
    )
    capabilities: list[str] = Field(
        default_factory=list, description="Capability strings, e.g. ['chat','vision']."
    )
    backends: list[str] = Field(
        default_factory=list, description="Runnable backends, e.g. ['rocm','vulkan']."
    )
    mmproj: str | None = Field(
        default=None,
        description="mmproj sidecar marker (presence flag); never a host path on import.",
    )

    @field_validator("id")
    @classmethod
    def id_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("stack model meta id must not be empty")
        return v.strip()


class StackCapabilityRow(BaseModel):
    """One (slot, child) capability selection carried by a stack slot entry.

    Mirrors the fields of ``hal0.capabilities.config.CapabilitySelection`` that
    are portable; the apply engine (PR-2) translates these into real
    CapabilitySelection rows at apply time.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    child: str = Field(
        ..., description="Capability child key, e.g. 'embed', 'rerank', 'stt', 'tts', 'vision'."
    )
    device: str = Field(..., description="Device target for this child.")
    provider: str = Field(..., description="Provider name for this child.")
    model: str = Field(..., description="Model id bound to this child.")
    enabled: bool = Field(default=True, description="Whether this child is active in the stack.")

    @field_validator("device")
    @classmethod
    def device_valid(cls, v: str) -> str:
        if v not in _VALID_DEVICES:
            raise ValueError(f"device {v!r}: must be one of {sorted(_VALID_DEVICES)}")
        return v


class StackSlotEntry(BaseModel):
    """One slot's contribution to a stack: which model/profile/caps it carries.

    References models and profiles by name/id; the embedded ``profiles`` and
    ``models`` maps on the parent ``StackConfig`` carry the metadata needed to
    resolve those references on another machine.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    slot: str = Field(..., description="Slot name this entry configures (kebab-case).")
    profile: str | None = Field(
        default=None, description="Profile name reference (resolved against StackConfig.profiles)."
    )
    model: str | None = Field(
        default=None, description="Model id reference (resolved against StackConfig.models)."
    )
    device: str | None = Field(default=None, description="Device override for the slot.")
    provider: str | None = Field(default=None, description="Provider override for the slot.")
    vision: bool = Field(
        default=False, description="Enable the mmproj vision sidecar for this slot."
    )
    mtp: bool | None = Field(
        default=None, description="Per-slot MTP override (inherits profile default when None)."
    )
    enable_thinking: bool | None = Field(default=None, description="Per-slot reasoning override.")
    server_extra_args: str | None = Field(
        default=None, description="Freeform llama-server CLI flags for this slot."
    )
    capabilities: list[StackCapabilityRow] = Field(
        default_factory=list, description="Capability child selections."
    )

    @field_validator("slot")
    @classmethod
    def slot_valid(cls, v: str) -> str:
        import re

        if not v or not v.strip():
            raise ValueError("slot name must not be empty")
        if not re.match(_STACK_NAME_RE, v):
            raise ValueError(
                f"slot name {v!r}: use lowercase alphanumeric, hyphens, underscores; "
                f"start with alphanumeric; max 32 chars"
            )
        return v

    @field_validator("device")
    @classmethod
    def device_valid(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_DEVICES:
            raise ValueError(f"device {v!r}: must be one of {sorted(_VALID_DEVICES)}")
        return v


class StackConfig(BaseModel):
    """One ``[stack.<slug>]`` entry in stacks.toml.

    A curated bundle of slots + embedded profiles + embedded model metadata.
    The slug is the dict key (validated by StacksCatalog on create), not a
    field here — mirroring ProfileConfig. ``name`` is the human display label.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    name: str = Field(default="", description="Human display label (falls back to slug in the UI).")
    description: str = Field(default="", description="What this stack is for.")
    author: str = Field(default="", description="Author/provenance, for the future directory.")
    icon: str = Field(default="", description="Accent token or emoji shown on the card.")
    tags: list[str] = Field(
        default_factory=list, description="Freeform tags for listing/filtering."
    )
    schema_version: int = Field(
        default=STACK_SCHEMA_VERSION_CURRENT,
        description="Stack schema version, stamped for forward-compat / envelope migration.",
    )
    hal0_version: str = Field(
        default="", description="hal0 version that produced this stack (provenance)."
    )
    slots: list[StackSlotEntry] = Field(
        default_factory=list, description="Slots this stack configures."
    )
    profiles: dict[str, ProfileConfig] = Field(
        default_factory=dict,
        description="Embedded profiles referenced by slots, so the stack is self-contained.",
    )
    models: dict[str, StackModelMeta] = Field(
        default_factory=dict,
        description="Embedded model metadata (no weights) for referenced model ids.",
    )


class StacksConfig(BaseModel):
    """Parsed stacks.toml — top-level ``[stack]`` table, keyed by slug."""

    model_config = {"populate_by_name": True, "extra": "forbid"}

    stack: dict[str, StackConfig] = Field(default_factory=dict)


# Built-in seed stacks (immutable, clone-only) — the day-one catalog the
# Stacks page ships with (spec §10). Returned by ``load_stacks_config`` when no
# stacks.toml exists yet, and consulted by ``StacksCatalog`` for the
# seed-immutability guard. Grounded in the live Strix-Halo model roster and the
# canonical ``agent``/``utility`` slots (ADR-0023; the spec's pre-ADR ``chat``/
# ``primary``/``util`` slot names are mapped onto these). Each carries embed +
# rerank capability rows so memory recall works out of the box. Model metadata
# (``models{}``) is intentionally left empty — ``export_envelope`` fills it from
# the live registry at export time, keeping the in-code seed lean.
#
# These reference this host's real registry ids; cloned-and-edited is the
# intended path, and apply's dry-run surfaces any model that isn't local.


def _embed_rerank_rows(device: str = "gpu-rocm") -> list[StackCapabilityRow]:
    """The shared embed + rerank capability pair every seed ships with."""
    return [
        StackCapabilityRow(
            child="embed",
            device=device,
            provider="llama-server",
            model="qwen3-embedding-0-6b-q8-0",
            enabled=True,
        ),
        StackCapabilityRow(
            child="rerank",
            device=device,
            provider="llama-server",
            model="bge-reranker-v2-m3-q4_k_m",
            enabled=True,
        ),
    ]


SEED_STACKS: dict[str, StackConfig] = {
    # saber — max-throughput agentic MoE. Decode-per-GB leader on the board.
    "saber": StackConfig(
        name="Saber",
        description="High-speed agentic MoE: a 35B-A3B agent on ROCm with a fast "
        "Vulkan utility helper, plus memory recall.",
        author="hal0",
        icon="⚡",
        tags=["agentic", "moe", "fast"],
        slots=[
            StackSlotEntry(
                slot="agent",
                model="qwen3-6-35b-a3b-nsc-ace-saber-mtp-f16-to-rocmfp4-strix-lean",
                device="gpu-rocm",
                profile="rocm",
                mtp=True,
                capabilities=_embed_rerank_rows(),
            ),
            StackSlotEntry(
                slot="utility",
                model="gemma-4-12b-it-ud-q4-k-xl",
                device="gpu-vulkan",
                profile="vulkan",
            ),
        ],
    ),
    # forge — coding-first developer loadout: a coder agent + a fast draft coder.
    "forge": StackConfig(
        name="Forge",
        description="Coding-first: a 27B coder agent on ROCm with a small fast "
        "draft coder as the utility, plus codebase retrieval.",
        author="hal0",
        icon="🛠️",
        tags=["coding", "developer"],
        slots=[
            StackSlotEntry(
                slot="agent",
                model="qwopus3-6-27b-coder-mtp-q6-k",
                device="gpu-rocm",
                profile="rocm",
                mtp=True,
                capabilities=_embed_rerank_rows(),
            ),
            StackSlotEntry(
                slot="utility",
                model="qwopus3-5-4b-coder-mtp-q6-k",
                device="gpu-vulkan",
                profile="vulkan",
                mtp=True,
            ),
        ],
    ),
    # pi — always-on support: faithful compaction, memory recall (quality > speed).
    "pi": StackConfig(
        name="Pi",
        description="Always-on support: a q-rich 27B utility for faithful "
        "compaction and recall, with a light Vulkan agent.",
        author="hal0",
        icon="🥧",
        tags=["support", "memory", "compaction"],
        slots=[
            StackSlotEntry(
                slot="utility",
                model="chadrock3-6-27b-pi-agent-mtp-rocmfp4-strix-lean",
                device="gpu-rocm",
                profile="rocm",
                mtp=True,
                capabilities=_embed_rerank_rows(),
            ),
            StackSlotEntry(
                slot="agent",
                model="gemma-4-12b-it-ud-q4-k-xl",
                device="gpu-vulkan",
                profile="vulkan",
            ),
        ],
    ),
}


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
        # MTP_FLAG_BUNDLE is a set of DEFAULTS. A profile may pin its own
        # ``--spec-draft-*`` values (a hand-tuned draft KV type, p-min, …); those
        # must WIN, with the bundle only supplying the flags the profile left
        # unset. ``merge_flags(defaults, override)`` gives exactly that
        # precedence — the override (here ``base``) strips any matching flag from
        # the defaults. Appending the bundle verbatim (the old behaviour) let it
        # silently clobber a profile's explicit spec flags, e.g. a profile
        # asking for ``--spec-draft-type-k f16`` still launched with q8_0.
        #
        # Local import: ``hal0.config`` is imported before ``hal0.slots``, so a
        # module-level import would create a cycle. ``merge_flags`` now lives in
        # hal0.slots.argv (folded from the retired flag_merge module) so it
        # shares argv's tokenizer + short/long alias table.
        from hal0.slots.argv import merge_flags

        bundle = build_mtp_flag_bundle(getattr(profile, "backend", None))
        return merge_flags(bundle, base)
    return base


def resolve_chat_template(slot_cfg: dict, model_info: dict) -> str | None:
    """Effective chat-template id: slot override > model default > None (auto).

    'auto' (or empty/None) means use the GGUF-embedded template (no
    ``--chat-template-file``). Returns the template id string otherwise.
    """
    for val in (
        slot_cfg.get("chat_template"),
        (model_info.get("defaults") or {}).get("chat_template"),
    ):
        if val and val != "auto":
            return str(val)
    return None


# ── UpstreamsConfig ────────────────────────────────────────────────────────────


_VALID_UPSTREAM_KINDS = frozenset({"slot", "remote"})
# The full set implemented by UpstreamRegistry.auth_headers().
_VALID_AUTH_STYLES = frozenset({"bearer", "anthropic", "google_query", "header", "none"})
# Canonical vocabulary matches the Upstream dataclass; "lazy"/"eager" were the
# original schema-only spellings and are still accepted as aliases on read.
_VALID_WARMUP = frozenset({"none", "ondemand", "always"})
_WARMUP_ALIASES = {"lazy": "ondemand", "eager": "always"}


class UpstreamModelFilters(BaseModel):
    """Optional [upstream.model_filters] table — curates /v1/models advertising.

    A model id is advertised when it is in ``models`` OR matches any ``include``
    glob (both empty ⇒ everything included), AND it does not match any
    ``exclude`` glob. Exclude always wins. Dispatch is unfiltered — excluded
    models stay reachable by explicit name.
    """

    model_config = {"extra": "forbid"}

    models: list[str] = Field(default_factory=list, description="Exact model ids to allowlist.")
    include: list[str] = Field(default_factory=list, description="fnmatch globs to include.")
    exclude: list[str] = Field(default_factory=list, description="fnmatch globs to exclude.")

    @field_validator("models", "include", "exclude")
    @classmethod
    def drop_empty_entries(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s and s.strip()]

    def is_empty(self) -> bool:
        return not (self.models or self.include or self.exclude)


class UpstreamEntry(BaseModel):
    """One [[upstream]] entry in upstreams.toml."""

    model_config = {"populate_by_name": True, "extra": "allow"}

    name: str = Field(..., description="Unique upstream name.")
    kind: str = Field(default="remote", description="'slot' | 'remote'.")
    url: str = Field(..., description="Base URL.")
    auth_style: str = Field(default="bearer")
    auth_header: str = Field(default="", description="Header name when auth_style='header'.")
    auth_value_env: str = Field(default="")
    timeout_seconds: float = Field(default=300.0, gt=0.0)
    slot_name: str | None = Field(default=None)
    warmup_strategy: str = Field(default="none")
    advertise_models: bool = Field(default=True)
    enabled: bool = Field(
        default=True,
        description="When false the upstream is skipped by routing and /v1/models.",
    )
    model_filters: UpstreamModelFilters | None = Field(default=None)

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
        v = _WARMUP_ALIASES.get(v, v)
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

    @model_validator(mode="after")
    def header_style_has_header_name(self) -> UpstreamEntry:
        if self.auth_style == "header" and not self.auth_header.strip():
            raise ValueError(
                f"upstream {self.name!r}: auth_style='header' requires auth_header to be set"
            )
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
    index: int = Field(
        default=0,
        ge=0,
        description=(
            "Vendor enumeration index of this GPU (nvidia-smi / DRM card "
            "order). Matches what *_VISIBLE_DEVICES and CDI per-index device "
            "names (nvidia.com/gpu=<n>) expect for SlotConfig.gpu_index "
            "pinning. 0 for single-GPU hosts (additive; older probes omit it)."
        ),
    )
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
    # Additive probe facts (GPU/NPU generalization wave). Consumers keep
    # module-constant fallbacks for snapshots written by older probes:
    # FLMProvider falls back to /dev/accel/accel0 + /dev/dri/renderD128 and
    # npu_columns falls back to the Strix Halo 8-column cap.
    accel_path: str = Field(
        default="",
        description=(
            "Actual accel device node detected at probe time (e.g. "
            "'/dev/accel/accel0'). Empty = unknown; consumers fall back to "
            "the Strix Halo default /dev/accel/accel0."
        ),
    )
    render_path: str = Field(
        default="",
        description=(
            "iGPU render companion node the NPU runtime needs (e.g. "
            "'/dev/dri/renderD128'). Empty = unknown; consumers fall back to "
            "the Strix Halo default /dev/dri/renderD128."
        ),
    )
    aie_columns: int = Field(
        default=0,
        ge=0,
        description=(
            "Total AIE column count discovered via xrt-smi at probe time. "
            "0 = unknown; consumers fall back to the Strix Halo constant 8."
        ),
    )
    validated: bool | None = Field(
        default=None,
        description=(
            "Functional NPU validation result from `flm validate`, run at "
            "install/setup time (not by the fast presence probe). None = not "
            "yet validated (presence is node-detection only); True = the NPU "
            "runtime is reachable; False = flm validate ran but failed (NPU "
            "absent or libxrt-npu2 mismatched). Distinct from `present`, which "
            "only reflects device-node detection."
        ),
    )


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
    gpu_group_gids: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Numeric GIDs of the host's GPU-access groups resolved at probe "
            "time, keyed by group name (e.g. {'render': 993, 'video': 44}). "
            "providers/_gpu.resolve_gpu_group_ids uses these when the live "
            "group lookup fails (fallback chain: live /etc/group → this "
            "probe-time record → the Linux-convention constants 993/44). "
            "Empty on hosts where neither group existed at probe time."
        ),
    )
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
        description=(
            "Maximum number of slots that may exist (creation gate; 0 = "
            "unlimited). Counts every slot TOML including the seeded ones "
            "(utility, rerank, tts, img, npu, …), so values below the "
            "current slot count block new-slot creation. Applies to the "
            "next slot creation — no restart needed."
        ),
    )
    port_range_start: int = Field(
        default=_SLOT_PORT_MIN,
        ge=1024,
        le=65535,
        description="First port in the auto-allocation pool for new slots.",
    )
    port_range_end: int = Field(
        default=_SLOT_PORT_POOL_END,
        ge=1024,
        le=65535,
        description=(
            "Last port in the auto-allocation pool (inclusive). The default "
            "stops below 8188 so new slots never claim ComfyUI's port; the "
            "allocator also skips any port already used by an existing slot "
            "TOML. Applies to the next slot creation — no restart needed."
        ),
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
    evict_pressure_mb: int = Field(
        default=8192,
        ge=0,
        description=(
            "Host free-RAM floor (MiB) for pressure-driven LRU eviction (#903). "
            "When host MemAvailable drops below this value, idle lru-eligible "
            "slots are evicted in least-recently-used order until free RAM is "
            "back above the floor. 0 disables pressure eviction."
        ),
    )
    publish_host: str = Field(
        default="127.0.0.1",
        description=(
            "Host address slot containers publish their port on "
            "(``--publish=<host>:<port>:<port>``). Default ``127.0.0.1`` keeps "
            "slot ports loopback-only, reachable solely through hal0-api/Traefik "
            "— the safe default. Set to ``0.0.0.0`` to bind every slot on all "
            "interfaces so raw slot ports are reachable directly over the LAN "
            "(e.g. ``http://<host>.local:<port>``); this EXPOSES inference "
            "endpoints on your network, bypassing the API/reverse-proxy front "
            "door. A specific interface IP (e.g. ``10.0.1.142``) binds just that "
            "address. Applies on the next slot (re)start. Host-networked slots "
            "(``network_mode=host``) ignore this — port publishing is a no-op there."
        ),
    )

    @field_validator("publish_host")
    @classmethod
    def _publish_host_sane(cls, v: str) -> str:
        """Reject shapes that would break the rendered ``--publish`` token.

        The value lands verbatim in ``--publish=<host>:<port>:<port>`` on the
        systemd ExecStart line, so a stray space, colon, or newline would
        corrupt the argv. We don't resolve/validate reachability — an operator
        may legitimately bind an address that isn't up yet — only that the
        token is well-formed.
        """
        host = str(v).strip()
        if not host:
            raise ValueError("[slots].publish_host must not be empty (use 127.0.0.1 for loopback)")
        if any(c.isspace() for c in host) or ":" in host or "/" in host:
            raise ValueError(
                f"[slots].publish_host {host!r} is not a bare IPv4/hostname "
                "(no spaces, ':', or '/'; IPv6 literals are not supported here)"
            )
        return host

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

    direct_read_timeout_s: float = Field(
        default=300.0,
        ge=30.0,
        le=600.0,
        description=(
            "Non-streaming upstream read timeout in seconds. "
            "Large consolidation/extraction prompts can exceed 60s on slow slots. "
            "Streaming paths are unaffected. Range 30-600."
        ),
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


# ── MemoryGraphConfig (ADR-0023) ──────────────────────────────────────────────


class MemoryGraphConfig(BaseModel):
    """[memory.graph] section of hal0.toml (ADR-0023).

    Controls graph extraction on the Hindsight engine. Hindsight builds its graph
    natively via its own extraction LLM; hal0 points that LLM at the
    ``extraction_slot`` (propagated to hindsight-api as
    ``HINDSIGHT_API_LLM_MODEL=hal0/<slot>`` through a systemd drop-in).

    ADR-0023 replaced the inert, cognee-era ``route``/``upstream`` enum with a
    single ``extraction_slot`` knob. ``model_config.extra = "ignore"`` means a
    lingering ``route``/``upstream`` key in an old hal0.toml is silently dropped on
    load (and gone on the next save) rather than failing validation.
    """

    model_config = {"populate_by_name": True, "extra": "ignore"}

    enabled: bool = Field(
        default=False,
        description=(
            "Reporting gate for the graph-extraction status surface. Hindsight "
            "builds its graph natively, so this flag is informational on that "
            "engine; vector recall is unaffected either way."
        ),
    )
    extraction_slot: str = Field(
        default="utility",
        description=(
            "The local llm slot Hindsight uses for graph extraction / "
            "consolidation / reflect. Propagated to hindsight-api as "
            "HINDSIGHT_API_LLM_MODEL=hal0/<slot> via a systemd drop-in. Validated "
            "against the live enabled-llm-slot set by the API at set-time."
        ),
    )
    llm_timeout_s: int = Field(
        default=300,
        ge=30,
        le=3600,
        description=(
            "Timeout (seconds) for the Hindsight daemon's LLM calls — "
            "extraction, consolidation, and reflect. Covers cold slot starts "
            "and long reflects. Propagated to hindsight-api as "
            "HINDSIGHT_API_LLM_TIMEOUT via the same systemd drop-in as the "
            "extraction slot; the daemon restarts to pick it up."
        ),
    )

    @field_validator("extraction_slot")
    @classmethod
    def extraction_slot_grammar(cls, v: str) -> str:
        # Shape-only here (alnum/-/_, ≤32, leading alnum — the slot-name grammar);
        # existence + type=llm is enforced by the API against the live slot set.
        import re as _re

        if not v or not _re.match(r"^[a-z0-9][a-z0-9_-]{0,31}$", v):
            raise ValueError(
                "memory.graph.extraction_slot must be a valid slot name "
                "(lowercase alnum/-/_, ≤32 chars, leading alphanumeric)"
            )
        return v


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
    """[memory.embedding] section of hal0.toml — Hindsight-era rerank knobs.

    ADR-0023 made Hindsight the platform memory engine. Hindsight embeds
    server-side with its own bundled model, so hal0 no longer pins an
    embedding model here; what remains configurable is the second-pass
    reranker hal0 runs over cross-bank recall results
    (:class:`hal0.memory.hindsight_provider.Hal0Reranker`):

      - ``rerank_gateway_url`` — the OpenAI-surface gateway the reranker
        POSTs ``/v1/rerankings`` to (hal0-api's dispatcher, which routes
        to the rerank slot).
      - ``rerank_model`` — the rerank model id sent in the request body.
      - ``rerank_connect_timeout_s`` / ``rerank_read_timeout_s`` — the
        HTTP budgets; failures fall through to fused vector ordering,
        never blocking recall.

    The cognee-era fields (``model``, ``rerank_enabled``, ``rerank_url``,
    ``rerank_over_fetch_factor``, ``rerank_max_candidates``) were removed
    with the cognee wrapper; ``extra = "ignore"`` silently drops them
    from an older hal0.toml on load (gone on next save), mirroring how
    ADR-0023 retired ``[memory.graph]``'s ``route``/``upstream``.
    """

    model_config = {"populate_by_name": True, "extra": "ignore"}

    rerank_gateway_url: str = Field(
        default="http://127.0.0.1:8080",
        description=(
            "Base URL of the OpenAI-compatible gateway the memory reranker "
            "calls (``POST {url}/v1/rerankings``). Defaults to hal0-api "
            "itself, whose dispatcher routes to the rerank slot."
        ),
    )
    rerank_model: str = Field(
        default="builtin.jina-reranker-v1-tiny-en-q8",
        description=(
            "Rerank model id sent to the gateway. Must resolve to a model "
            "the rerank slot serves (see /v1/models on the gateway)."
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

    @field_validator("rerank_gateway_url", "rerank_model")
    @classmethod
    def rerank_fields_nonempty(cls, v: str, info: Any) -> str:
        if not v or not v.strip():
            raise ValueError(f"memory.embedding.{info.field_name} must not be empty")
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
    flm_store: str = Field(
        default="",
        description=(
            "Where FLM (NPU backend) model weights live. The NPU slot container "
            "bind-mounts this directory over FLM's hardcoded ~/.config/flm/models "
            "cache, and the host ``flm`` probe/pull bookkeeping points at it. "
            "Empty falls back to the HAL0_FLM_MODELS_DIR env var, then to FLM's "
            "default cache under the hal0 HOME (/var/lib/hal0/.config/flm/models). "
            "Set an absolute path (e.g. /mnt/ai-models/flm/models) to keep NPU "
            "weights off the root filesystem; hal0 creates the directory "
            "(container-uid-writable) at slot-spec build time and the generated "
            "unit orders after the backing mount, so a reboot with a late or "
            "missing mount no longer kills the slot with podman exit 125."
        ),
    )

    @field_validator("flm_store")
    @classmethod
    def flm_store_is_absolute_when_set(cls, v: str) -> str:
        """Empty means "env var / FLM default cache"; non-empty must be absolute."""
        s = str(v or "").strip()
        if not s:
            return ""
        if not Path(s).is_absolute():
            raise ValueError(
                f"models.flm_store {s!r} must be an absolute path (or empty for the default cache)"
            )
        return s

    def scan_roots(self) -> list[str]:
        """Roots the discovery scan actually walks: declared ``roots`` plus the
        effective store (``store`` when set, else the legacy ``pull_root``).

        The installer writes ``pull_root`` from ``--models-dir`` but historically
        never added it to ``roots`` (despite the pull_root doc claiming it's
        "auto-included"), so a headless install whose models live under a custom
        store/pull_root scanned only the default ``models_dir`` and found nothing
        — slots then failed to load (no path resolved for the model name). Folding
        the effective store in here makes discovery robust regardless of how the
        TOML was written. Order-preserving, deduped.
        """
        out: list[str] = list(self.roots)
        effective_store = (self.store or self.pull_root or "").strip()
        if effective_store and effective_store not in out:
            out.append(effective_store)
        return out

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
    "FAMILY_DEFAULTS",
    "MTP_FLAG_BUNDLE",
    "PROFILE_BENCH",
    "PROFILE_SCHEMA_VERSION_CURRENT",
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
    "family_flags",
    "map_backend_to_device",
    "model_family",
    "resolve_profile_flags",
]
