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
    VALID_DEVICES as _VALID_DEVICES,
)
from hal0.release.policy import ReleaseKind

log = logging.getLogger(__name__)

# ── Shared constants ───────────────────────────────────────────────────────────
#
# The identity vocabularies (device enum, legacy backend enum, the
# backend→device map, per-device default profiles) live in ONE place —
# ``hal0.model_meta`` (see its module docstring for the full vocabulary
# table + unknown-value policy). This module re-exports them so every
# existing ``from hal0.config.schema import …`` call site keeps working.
# model_meta imports nothing from schema, so the dependency is one-way.

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
# the v0.2 backend→device migration can be detected and
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
    # HAL0-SUNSET: v1.0.0 — flags own by models (spec-flags-ownership §2/§4).
    # This slot [model].n_gpu_layers no longer reaches the launch argv; the
    # migrator folds a slot's effective -ngl into its model's
    # ``defaults.n_gpu_layers`` (trusted, managed-flag) field. Field stays for
    # round-trip until the sunset ratchet drops it.
    n_gpu_layers: int = Field(
        default=-1,
        description=(
            "Number of layers to offload to GPU.  -1 means all. INERT at launch "
            "(flags own by models): the migrator folds this into the model's "
            "defaults.n_gpu_layers; it no longer reaches the argv chain."
        ),
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

    ``extra="forbid"`` (P3-schema Part C): there is no legitimate unknown
    ``[server]`` key — a typo (e.g. ``extraargs``) should fail loudly at load
    time rather than silently vanish. The escape hatch for anything not
    modeled here is ``extra_args`` itself (a freeform CLI passthrough string)
    plus ``env`` (an arbitrary env-var dict) — both are already declared
    fields, so this is additive hardening, not a behavior change for any
    TOML that only sets ``extra_args``/``env``.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    # HAL0-SUNSET: v1.0.0 — flags own by models (spec-flags-ownership §2/§4).
    # This slot [server].extra_args no longer reaches the launch argv chain;
    # the migrator folds a slot's effective tune into its model's
    # ``defaults.extra_args`` (the screened ``model_extra_args`` segment). Kept
    # for round-trip until the sunset ratchet drops it.
    extra_args: str | None = Field(
        default=None,
        description=(
            "Freeform llama-server CLI passthrough. INERT at launch (flags own "
            "by models): no longer tokenised into the argv chain. The migrator "
            "folds a slot's effective tune into the bound model's "
            "defaults.extra_args, where hal0.slots.argv still screens it against "
            "the §21.7 managed-arg denylist. Retained for TOML round-trip."
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
    #
    # ``id`` is the stable opaque slot identity (rework §11.1): assigned by
    # the ``slot`` table (hal0.slots.identity.SlotIdentityStore) and mirrored
    # into the TOML so on-disk reads resolve it without the DB. Units, ports,
    # state and routes key off ``id``; ``name`` is a mutable display label, so
    # a rename is a pure relabel with no reference churn. Optional so a TOML
    # written before this field existed still validates — the identity store
    # assigns the id on first sight.
    id: int | None = Field(
        default=None,
        ge=1,
        description="Stable opaque slot id (assigned by the slot identity store; mirror of the DB row).",
    )
    name: str = Field(
        ..., description="Slot display label, e.g. 'primary' (mutable; id is the stable key)."
    )
    port: int = Field(
        ...,
        ge=_SLOT_PORT_MIN,
        le=_SLOT_PORT_MAX,
        description=f"Host port for this slot ({_SLOT_PORT_MIN}-{_SLOT_PORT_MAX}, 127.0.0.1 only).",
    )
    device: str = Field(
        default=DEFAULT_DEVICE,
        description=(
            "v0.2 hardware-preference enum: 'gpu-rocm' | 'gpu-vulkan' | "
            "'gpu-cuda' | 'cpu' | 'npu'. Replaces the legacy ``backend`` "
            "field which mixed providers and backends."
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
    # ── Hardware grid (spec-hw-slot-ownership §2) ────────────────────────
    # The slot owns the physical/placement layer as typed fields:
    # ``device`` (class+backend enum, above) · ``n_gpu_layers`` (NGL) ·
    # ``threads`` · ``binary`` (runner image ref). Plus an optional
    # ``image_pin`` escape hatch. Reverses the spec-flags-ownership §5 fold
    # that moved NGL into ``model.defaults.n_gpu_layers``: hardware is
    # single-owner on the slot, the model stays logical/device-agnostic.
    n_gpu_layers: int = Field(
        default=-1,
        description=(
            "NGL — layers to offload to GPU; emits ``-ngl``. Authoritative on "
            "the slot (spec-hw-slot-ownership §2, reversing the §5 fold into "
            "model.defaults.n_gpu_layers). -1 = all layers, 0 = CPU only. "
            "Distinct from the nested [model].n_gpu_layers (ModelConfig), which "
            "the one-shot migration folds into this field and then sunsets."
        ),
    )
    threads: int = Field(
        default=0,
        ge=0,
        description=(
            "THREADS — CPU threads for the runner; emits ``--threads`` "
            "(spec-hw-slot-ownership §2). 0 = unset → the launcher omits "
            "``--threads`` and lets the runtime pick its own default."
        ),
    )
    binary: str = Field(
        default="",
        description=(
            "BINARY — the runner image ref: a key into "
            "hal0.runners.RUNNER_IMAGES (container build) that resolves the "
            "slot's launch image (spec-hw-slot-ownership §2/§3). Replaces the "
            "sunset model.preferred_runner. Its ``supported_backends`` is "
            "fit-check metadata, NOT a selector — a multi-backend image is "
            "disambiguated by ``device``, never by BINARY. Empty = derive the "
            "HW-gated default from ``device`` (hal0.runners.runner_for_backend)."
        ),
    )
    image_pin: str | None = Field(
        default=None,
        description=(
            "Optional image escape hatch (spec-hw-slot-ownership §3): a fully "
            "resolved image ref that overrides RUNNER_IMAGES[binary] for this "
            "slot (debug build / A-B / rollback-to-last-known-good). Canonical "
            "TOML key ``image_pin``; the prior ``image`` / ``[slot].image`` "
            "nestings collapse into it in the migration lane. None (default) = "
            "use the BINARY-resolved default image. A non-default pin is shown "
            "on the slot card so drift is never hidden."
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
    # HAL0-SUNSET: v1.0.0 — runtime is Literal["container"] only; field is ceremony, drop it.
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
    # spec-hw-slot-ownership §1: enable_thinking / mtp are model-owned typed
    # capabilities now (see ModelDefaults.enable_thinking / .mtp in
    # hal0.registry.model) — the former SlotConfig fields let a slot pill and
    # the model drawer both persist the same fact, which could silently
    # disagree. A slot config write can no longer set either key
    # (hal0.slots.config_write.MODEL_OWNED_SLOT_KEYS hard-rejects it); the
    # one-shot hal0.config.migrations.model_tune_ownership migrator folds any
    # pre-existing slot value onto the assigned model before dropping it.
    # HAL0-SUNSET: v1.0.0 — flags own by models (spec-flags-ownership §2/§4).
    # This slot-level parallelism knob no longer reaches the launch argv; the
    # migrator folds an effective ``--parallel N`` (plus ``--kv-unified`` when
    # N>1) into the bound model's ``defaults.extra_args``. Kept for round-trip
    # until the sunset ratchet drops it.
    parallel: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Per-slot llama-server sequence slots (--parallel / -np) for continuous "
            "batching. INERT at launch (flags own by models): no longer emitted to "
            "the argv chain. The migrator folds an effective --parallel N (and "
            "--kv-unified when N>1) into the bound model's defaults.extra_args. "
            "Retained for TOML round-trip."
        ),
    )
    # HAL0-SUNSET: v1.0.0 — chat_template is model-intrinsic and folds into the
    # model (spec-flags-ownership §7 slot-purity). INERT at launch: the slot
    # tier was removed from resolve_chat_template, so model.defaults.chat_template
    # is the single source. The one-shot migrator folds each slot's effective
    # template into its bound model (divergent-share refusal). Retained for TOML
    # round-trip until the sunset ratchet drops it.
    chat_template: str | None = Field(
        default=None,
        description=(
            "SUNSET (spec-flags-ownership §7): per-slot chat-template override "
            "(id from /api/chat-templates, or 'auto'/None for the GGUF-embedded "
            "template). INERT at launch — the chat template is now model-intrinsic "
            "and read only from model.defaults.chat_template; the migrator folds "
            "this into the bound model. Round-trips for one release. See "
            "resolve_chat_template and slot_flags_fold."
        ),
    )
    # spec-hw-slot-ownership §1: vision (#901) is a model-owned typed
    # capability now (ModelDefaults.vision) — the former per-slot toggle let
    # a slot pill and the model drawer both persist the same fact. A slot
    # config write can no longer set it (MODEL_OWNED_SLOT_KEYS hard-rejects
    # it); the container provider reads ``model.defaults.vision`` at launch
    # (see providers.container._resolve_llama_scalars).

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
    # HAL0-SUNSET: v1.0.0 — workers is inert; a non-default value only logs a warning.
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
    pinned: bool = Field(
        default=False,
        description=(
            "P3-slots §21.10 operator pin. When true, this slot is exempt from "
            "automatic idle/pressure eviction (hal0.slots.reaper.is_pinned() ORs "
            "this onto the built-in agent/utility/npu anchor set) AND a manual "
            "POST /{name}/unload or DELETE /{name} refuses without ?force=true "
            "(HTTP 409 slot.pinned). Additive field — default False preserves "
            "existing behavior for every slot that doesn't set it."
        ),
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
        """Read-only promotion shim: derive ``device`` from a legacy
        on-disk ``backend`` key, then drop ``backend`` from the dict.

        ``SlotConfig.backend`` no longer exists as a field (P2-device:
        ``device`` is the sole persisted truth). But there is no on-disk
        slot-TOML migration for backend→device, so a pre-device slot TOML
        (``backend`` set, no ``device``) still needs to resolve to the
        right hardware on load — without this, such a slot would silently
        regress to :data:`DEFAULT_DEVICE` (gpu-rocm). This validator is
        the ONLY thing that keeps that promotion alive; do NOT delete it.

        Unlike the old dual-write era, ``backend`` is popped from the
        dict rather than kept: with ``extra="allow"`` a leftover key
        would otherwise round-trip forever via ``extra`` once the field
        is gone, which would defeat "device sole truth". The pop always
        runs when ``backend`` is present, even if ``device`` was already
        supplied (e.g. a stale dual-written TOML from before this
        change) — only the *promotion* is gated on ``device`` being
        absent.
        """
        if not isinstance(data, dict):
            return data
        backend_value = data.get("backend")
        if backend_value is None:
            return data
        new_data = dict(data)
        new_data.pop("backend", None)
        if not data.get("device"):
            # Tolerate already-new-namespace values (gpu-rocm etc) — those
            # round-trip through ``map_backend_to_device`` as identities.
            mapped = map_backend_to_device(str(backend_value))
            if backend_value not in _VALID_DEVICES:
                log.warning(
                    "config.slot.backend_deprecated",
                    extra={
                        "backend": backend_value,
                        "promoted_device": mapped,
                        "note": (
                            "SlotConfig.backend is removed; 'device' is now the "
                            "sole persisted truth. See ADR-0006 §7."
                        ),
                    },
                )
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
    """One [[provider]] entry in providers.toml.

    ``extra="forbid"`` (P3-schema Part C): typo'd provider keys should raise
    at load time rather than silently round-trip as dead weight. The
    containing ``ProvidersConfig`` (the ``[[provider]]`` list) stays
    ``allow`` for forward-compat.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

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
        # Former basic-lane seed defaults (pre HW-gated default). A slot pinned to
        # one of these is materialised-default debris — the updater re-resolves it
        # through the HW gate (:func:`resolve_default_image`): Strix boxes migrate
        # to the rocmfpx runner, other hosts stay on the same toolbox (no-op).
        "ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server",
        "ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server",
    }
)

#: Lean fallback toolbox images for the two non-rocmfpx lanes. The rocmfpx
#: runner is Vulkan-portable — its Mesa/RADV Vulkan backend runs on any AMD GPU
#: (the gfx1151 HIP kernels are a bonus on Strix Halo, not a requirement) — so
#: it serves every AMD GPU lane. Only a CUDA host and a CPU-only host want a
#: different/leaner image (the 7.5 GB ROCm-based rocmfpx image is pointless for
#: CPU-only inference).
FALLBACK_VULKAN_IMAGE = "ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server"
FALLBACK_CUDA_IMAGE = "ghcr.io/ggml-org/llama.cpp:server-cuda"


def resolve_default_image(backend: str | None, device_class: str | None = None) -> str:
    """Default container image for a slot lane with no explicit image pin.

    Precedence is handled by the caller (slot ``image`` override → profile
    ``image`` → *this*). ``hal0-rocmfpx`` is the universal AMD-GPU default —
    its Vulkan/RADV backend is GPU-agnostic, so it is not gated on Strix-Halo —
    with two carve-outs:

    * CUDA lanes → the upstream llama.cpp CUDA image.
    * CPU-only lanes → the lean Vulkan toolbox (llama-server runs CPU-only when
      no GPU devices are passed; the big rocmfpx image is wasteful for CPU).
    * Every other (AMD GPU: ``rocm`` / ``vulkan``) lane → :data:`DEFAULT_ROCMFPX_IMAGE`
      (the unified ROCmFPX runner; serves chat + ``--embedding`` + ``--reranking``).

    Deterministic and probe-free — no hardware read on the hot render path.

    §7.1b / ML-4: this is now a thin back-compat SHIM over the runner-image
    registry (:mod:`hal0.runners`) — ``hal0.runners.runner_for_backend`` owns
    the HW-gate logic above (verbatim) and ``resolve_runner_image`` adds the
    env-var / manifest-digest tiers this function never had. The name +
    signature stay put because a wide set of imports/tests depend on them
    (updater, tests/config/test_default_image_gate.py, …). The import is
    LOCAL (not at module top) to avoid a cycle: ``hal0.runners`` imports the
    image constants from THIS module at its own top level, so this module
    can't import ``hal0.runners`` back until this function actually runs.
    """
    from hal0.runners import resolve_runner_image, runner_for_backend

    return resolve_runner_image(runner_for_backend(backend, device_class))


#: Seed profile catalog — externalized to shipped TOML (P3-schema, spec
#: Part A). See ``hal0/config/data/seed_profiles.toml`` for the 20 seed
#: entries (with their per-profile rationale comments) and
#: ``hal0.config.seeds.seed_profiles()`` for the loader. ``SEED_PROFILES``
#: is (re)assigned at the bottom of this module, once every model above
#: exists, as a module-scope re-export so every existing
#: ``from hal0.config.schema import SEED_PROFILES`` keeps working.

#: Static bench numbers for seed profiles — externalized to shipped TOML
#: (P3-schema, spec Part A). See ``hal0/config/data/profile_bench.toml``
#: and ``hal0.config.seeds.profile_bench()``. ``PROFILE_BENCH`` is
#: (re)assigned at the bottom of this module as a module-scope re-export.

#: Per-family llama-server flag overrides — externalized to shipped TOML
#: (P3-schema, spec Part A). See ``hal0/config/data/family_defaults.toml``
#: and ``hal0.config.seeds.family_defaults()``. ``FAMILY_DEFAULTS`` is
#: (re)assigned at the bottom of this module as a module-scope re-export;
#: ``_KNOWN_FAMILIES``/``model_family``/``family_flags`` below are
#: derivation logic, not data, and stay in Python unchanged.

#: Families FAMILY_DEFAULTS can key on, matched as a token in the model id /
#: filename.  GGUF ``general.architecture`` would be the canonical signal, but
#: it is not persisted on registry rows today (auto-scan stores only
#: ``{discovered, source}``); the id/filename carries the family reliably for
#: the GGUF fleet, and arch-from-header is the future hardening.
_KNOWN_FAMILIES: tuple[str, ...] = ("gemma", "qwen", "llama", "phi", "mistral", "deepseek")


def model_family(*hints: str | None, architecture: str | None = None) -> str | None:
    """Best-effort model family, preferring the registry's ``architecture``.

    §7.1a / ML-5: when ``architecture`` (``registry.model.Model.architecture``,
    e.g. ``"gemma3"``, ``"qwen2"``, ``"qwen3next"``, ``"gpt-oss"``, ``"mamba"``)
    is given (non-empty), it is the ONLY signal consulted — a token-membership
    match against :data:`_KNOWN_FAMILIES` (so ``"gemma3"`` -> ``"gemma"``,
    ``"qwen3next"`` -> ``"qwen"``), else ``None``. An architecture hal0 has no
    :data:`FAMILY_DEFAULTS` override for (e.g. ``"gpt-oss"``, ``"mamba"``) is
    deliberately NOT re-guessed from the filename hints below — a real,
    authoritative signal that just doesn't map to a known family beats a
    coincidental filename token match.

    ``hints`` (id/filename/path) are the FALLBACK token scan, used only when
    ``architecture`` is unset/empty — the pre-existing path for registry
    rows the architecture-detection lane (§7.1d) hasn't backfilled yet.
    Cheap and side-effect-free so the launch + preview argv paths can both
    call it.
    """
    if architecture:
        arch_hay = architecture.lower()
        return next((fam for fam in _KNOWN_FAMILIES if fam in arch_hay), None)
    hay = " ".join(h for h in hints if h).lower()
    return next((fam for fam in _KNOWN_FAMILIES if fam in hay), None)


def family_flags(*hints: str | None, architecture: str | None = None) -> str:
    """The :data:`FAMILY_DEFAULTS` flag string for the model's family, else ''."""
    fam = model_family(*hints, architecture=architecture)
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

    # spec-hw-slot-ownership §3: ``image`` is GONE from profiles — a profile is a
    # device-agnostic tune template. The image is slot-owned
    # (``slot.image_pin or RUNNER_IMAGES[slot.binary]``); the deploy-window
    # migration (hal0.config.migrations.hw_slot_ownership) folds any prior
    # profile.image onto its slots and strips the key, and
    # ``loader.load_profiles_config`` drops a stray ``image`` key from an
    # un-migrated profiles.toml so the field's removal is load-safe.
    flags: str = Field(
        default="",
        description="Bench-tuned llama-server CLI flags (no model/port/ctx args).",
    )
    mtp: bool = Field(
        default=False,
        description=(
            "INFORMATIONAL ONLY (§7.1a / ML-5) — no longer read by "
            "resolve_profile_flags() or the launch path. MTP is a MODEL "
            "capability now (ModelDefaults.mtp / the registry 'mtp' tag), gated "
            "by the launching runner's supports.mtp; see "
            "providers.container._effective_mtp. Kept on this class for "
            "on-disk/API back-compat until P3-schema externalizes profiles."
        ),
    )
    device_class: Literal["gpu", "cpu", "npu", "img"] | None = Field(
        default=None,
        description=(
            "Device class this profile targets.  None (default, per "
            "spec-hw-slot-ownership.md §4.1 / seeded-profile-rework §4.1) "
            "means the profile is device-agnostic — the slot owns device "
            "and the profile supplies the logical tune only.  ``'gpu'``, "
            "``'cpu'``, ``'npu'``, ``'img'`` are explicit-fit values; "
            "``profile_fits_slot`` skips the device-class gate when this is "
            "None."
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


# Built-in seed stacks — externalized to shipped TOML (P3-schema, spec Part
# A). See ``hal0/config/data/seed_stacks.toml`` and
# ``hal0.config.seeds.seed_stacks()``/``seeds._embed_rerank_rows()`` (the
# shared embed+rerank capability pair moved there too, per spec §A.2(a)).
# ``SEED_STACKS`` is (re)assigned at the bottom of this module as a
# module-scope re-export so every existing
# ``from hal0.config.schema import SEED_STACKS`` / ``schema.SEED_STACKS``
# call site keeps working unchanged.


def resolve_profile_flags(profile: ProfileConfig, mtp_override: bool | None = None) -> str:
    """Return the full flag string for *profile*, expanding MTP when set.

    When the effective MTP setting is ``True``, ``MTP_FLAG_BUNDLE`` is
    appended after ``profile.flags`` (separated by a single space).  The
    model path, port, and context size are the slot's concern — they are
    NOT included here.

    §7.1a / ML-5: ``profile.mtp`` is NO LONGER consulted here — MTP is a
    MODEL capability now (``ModelDefaults.mtp`` / the registry ``mtp``
    tag), gated by the launching runner's ``supports.mtp``. This function
    only expands the bundle when the CALLER explicitly says so:

      - ``mtp_override=True``  → append the bundle.
      - ``mtp_override=False`` / ``None`` → do not append it.

    :func:`hal0.providers.container._resolve_llama_scalars` is the single
    caller that computes a real decision (via ``_effective_mtp``, which
    folds in slot.mtp / defaults.mtp / the tag+runner-support AUTO tier)
    and always passes an explicit bool; every other caller (e.g.
    ``ResolvedProfile.resolved_flags``, computed with no override for
    profile-catalog listings/cards) now correctly renders MTP-free, since a
    profile alone — without a model bound to it — has no opinion on MTP.

    Args:
        profile: A validated :class:`ProfileConfig`.
        mtp_override: Explicit True/False decision from the caller.
            ``None`` behaves the same as ``False`` (no bundle).

    Returns:
        The complete flag string ready to pass to llama-server.
    """
    base = profile.flags.strip()
    if mtp_override:
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
    """Effective chat-template id: model default > None (auto).

    FLAGS-own §7 (slot-purity fold): the chat template is model-intrinsic — it
    is a property of the artifact, not the slot — so the model's
    ``defaults.chat_template`` is now the single launch source. The per-slot
    ``chat_template`` override is sunset (inert at launch); the one-shot
    migrator (:mod:`hal0.config.migrations.slot_flags_fold`) folds each slot's
    effective template into its bound model, refusing any model whose slots
    carry divergent templates. ``slot_cfg`` is retained in the signature for
    the container provider's call-site shape (and so a future reader can key
    other model-vs-slot resolution here) but its ``chat_template`` key is no
    longer consulted.

    'auto' (or empty/None) means use the GGUF-embedded template (no
    ``--chat-template-file``). Returns the template id string otherwise.
    """
    val = (model_info.get("defaults") or {}).get("chat_template")
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
    """One [[upstream]] entry in upstreams.toml.

    ``extra="forbid"`` (P3-schema Part C): typo'd upstream keys should raise
    at load time. The containing ``UpstreamsConfig`` (the ``[[upstream]]``
    list) stays ``allow`` for forward-compat.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

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
    """[meta] section in hal0.toml.  Tracks config schema version for migrations.

    ``extra="forbid"`` (P3-schema Part C): a leaf tunable table with no
    legitimate unknown key — see PLAN.md §5 Tier 1 ("backend = vukan raises
    with the field path"), which only holds if leaf tables reject typos.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    schema_version: int = Field(
        default=CURRENT_SCHEMA_VERSION,
        ge=1,
        description=(
            "Config schema version.  hal0 config migrate bumps this when applying "
            "versioned transforms.  See PLAN.md §5 Tier 3."
        ),
    )


class SlotsConfig(BaseModel):
    """[slots] section in hal0.toml.  Global slot policy.

    ``extra="forbid"`` (P3-schema Part C): a leaf tunable table.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

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

    network_mode: str = Field(
        default="",
        description=(
            "Box-default podman network mode for slot containers "
            "(``Network=<mode>`` in the generated Quadlet). Empty (the default) "
            "means bridge networking with a loopback ``PublishPort`` — the safe "
            "default that keeps raw slot ports off the LAN behind hal0-api. "
            "Set to ``host`` on netns-limited substrates (unprivileged "
            "podman-in-LXC, where bridge netns teardown races leave slots "
            "unloadable) so every slot renders ``Network=host``. This is "
            "DEPLOY-TIME configuration for the box's substrate, not a runtime "
            "probe — the renderer never sniffs podman/LXC itself. "
            "SECURITY NOTE: host networking shares the container's loopback "
            "with hal0-api, so a host-net slot can reach 127.0.0.1 services on "
            "the CT (e.g. hal0-api itself); the compensating fence is that the "
            "slot process is force-bound to 127.0.0.1 (not 0.0.0.0) and every "
            "hal0-api route is auth-gated — the raw unauthenticated slot port "
            "is never LAN-reachable. This shared-loopback reach is the accepted "
            "residual (see docs/rework/podman-unprivileged-findings.md). A slot "
            "whose provider REQUIRES host net (ComfyUI) already forces it "
            "regardless of this default. Applies on the next slot (re)start."
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

    @field_validator("network_mode")
    @classmethod
    def _network_mode_known(cls, v: str) -> str:
        """Allow only the modes the renderer actually couples a fence to.

        ``""`` (bridge + loopback PublishPort) and ``host`` (Network=host +
        forced loopback bind) are the two the fence logic understands. Anything
        else (``bridge``, ``none``, a custom CNI name) would render an
        un-fenced ``Network=`` with no corresponding bind flip, so it is
        rejected rather than silently accepted — the value lands verbatim in a
        Quadlet ``Network=`` key, so it must also be a bare token.
        """
        mode = str(v).strip()
        if mode in ("", "host"):
            return mode
        raise ValueError(
            f"[slots].network_mode {mode!r} is not supported "
            "(use '' for bridge+loopback-publish, or 'host' for host networking)"
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
    """[dispatcher] section in hal0.toml.

    ``extra="forbid"`` (P3-schema Part C): a leaf tunable table.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

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
    """[telemetry] section in hal0.toml.

    ``extra="forbid"`` (P3-schema Part C): a leaf tunable table.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    enabled: bool = Field(
        default=False,
        description="Opt-in anonymous telemetry.  Off by default.  See PLAN.md §14.",
    )
    channel: ReleaseKind = Field(
        default="stable",
        description="Update channel: 'stable' | 'preview' | 'nightly'.",
    )


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


# ── AgentConfig ────────────────────────────────────────────────────────────────

# Schema version pin so a future incompatible change
# (e.g. nesting tool policies under a `[mcp.servers.<name>.policy]`
# block) can detect + migrate old agent TOMLs without silent breakage.
AGENT_CONFIG_SCHEMA_VERSION = 1

# Outbound auth styles. Today only ``bearer-from-env``
# (token loaded at agent-process startup from an env file or
# systemd-credential) is implemented. Listed as a frozenset so future
# additions (mtls, oauth-device-flow, …) extend a single source.
_VALID_AGENT_AUTH_KINDS = frozenset({"none", "bearer-from-env"})

AgentAuthKindLiteral = Literal["none", "bearer-from-env"]


class AgentAuthConfig(BaseModel):
    """``[mcp.servers.<name>.auth]`` block.

    Indirection via env var keeps tokens out of TOML so
    the config file remains commit-safe and the dashboard can render
    it. The actual token is loaded by the agent driver at process
    startup (from systemd-credential or a 0600 env file) and never
    appears on the command line.

    ``extra="forbid"`` (P3-schema Part C): a leaf tunable table.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

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

    - ``allow``  : autonomous call (no approval queue).
    - ``gated``  : enqueue via approval queue, await user pick.
    - ``blocked``: hard-reject at the client; never reaches the server.

    The lists MUST be disjoint — overlap is operator error and surfaces
    as a load-time ValidationError with the offending tool name in the
    message, NOT a silent "which list wins?" decision.

    Default is empty on all three axes. Combined with the default-deny
    posture in :class:`MCPServerConfig`, that means an MCP server with
    no ``tools`` block has *zero* callable tools — which is what we
    want for a fresh registration the user hasn't reviewed yet.

    ``extra="forbid"`` (P3-schema Part C): a leaf tunable table.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    allow: list[str] = Field(
        default_factory=list,
        description=("Tools the agent may call autonomously. Empty = no autonomous tools."),
    )
    gated: list[str] = Field(
        default_factory=list,
        description=(
            "Tools the agent may request; each call enqueues an approval. Empty = no gated tools."
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

    Server-axis default-deny — only servers listed here
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
            "Marks hal0-admin / hal0-memory. Bundled-agent "
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
            "Filesystem sandbox root. Empty falls back "
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
    """Top-level shape of ``/etc/hal0/agents/<name>.toml``.

    By design, one file per agent — installer for bundled,
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


_VALID_AGENT_MEMORY_PROVIDERS = frozenset({"hindsight", "honcho"})


class MemoryConfig(BaseModel):
    """[memory] section of hal0.toml.

    Container for the per-subsystem memory tunables. Today carries
    ``[memory.graph]``, ``[memory.embedding]`` (issue #116), and
    the per-agent provider routing (``agent_providers``/``agent_private``).
    Future memory features (retention, prune-policy, archival) land under a
    single namespace rather than scattering top-level tables.
    """

    model_config = {"populate_by_name": True, "extra": "allow"}

    enabled: bool = Field(
        default=True,
        description=(
            "Whether the memory subsystem (Hindsight engine, /mcp/memory, "
            "/api/memory/*, the dashboard's Agent -> Memory tab) is built at "
            "startup. Replaces the old HAL0_MEMORY_ENABLED env var — toggle via "
            "'hal0 memory enable' / 'hal0 memory disable'. Consumed once at "
            "create_app(), so a change needs a hal0-api restart."
        ),
    )
    graph: MemoryGraphConfig = Field(default_factory=MemoryGraphConfig)
    embedding: MemoryEmbeddingConfig = Field(default_factory=MemoryEmbeddingConfig)
    unified_bank: bool = Field(
        default=True,
        description=(
            "Route all memory into ONE Hindsight bank ('shared') instead of "
            "per-agent private banks. When true, the X-hal0-Private toggle no "
            "longer forks a private:<agent> bank — the write still lands in "
            "'shared' but is stamped with a 'visibility:private' tag (plus an "
            "'agent:<id>' tag), and recall is single-bank (no cross-bank "
            "fan-out). Set false to restore the legacy multi-bank model "
            "(private:<agent> / project:<id> banks + fan-out recall)."
        ),
    )
    # HAL0-SUNSET: v1.0.0 — 'cognee' engine value resolves to hindsight at runtime; drop the alias.
    engine: str = Field(
        default="hindsight",
        description=(
            "Active memory engine. 'hindsight' (default) is the platform "
            "engine; 'mem0' and 'pgvector' are alternates. 'cognee' is "
            "DEPRECATED — the legacy Cognee store has been dark since v0.4 and "
            "its wrapper was removed; the value is still accepted for "
            "back-compat but resolves to 'hindsight' at runtime. Use "
            "'hindsight'."
        ),
    )
    agent_providers: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Agent id -> memory provider ('hindsight' | 'honcho'). Selects "
            "which provider the agent provisioner wires into that agent's "
            "Hermes memory.provider + honcho.json. hal0's own [memory].engine "
            "(above) is unaffected — it always stays 'hindsight' regardless "
            "of what agents are routed to Honcho."
        ),
    )
    agent_private: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "Agent id -> private-workspace flag. When True the agent writes "
            "to an isolated Honcho workspace "
            "'<honcho.workspace>__private__<agent>' instead of the unified "
            "workspace. Only meaningful for agents routed to 'honcho' in "
            "agent_providers."
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

    @field_validator("agent_providers")
    @classmethod
    def _agent_providers_known(cls, v: dict[str, str]) -> dict[str, str]:
        for agent_id, provider in v.items():
            if provider not in _VALID_AGENT_MEMORY_PROVIDERS:
                raise ValueError(
                    f"memory.agent_providers[{agent_id!r}] = {provider!r} must be "
                    f"one of {sorted(_VALID_AGENT_MEMORY_PROVIDERS)}"
                )
        return v


# ── HonchoConfig ────────────────────────────────────────────────────────────

_VALID_HONCHO_TRANSPORTS = frozenset({"openai", "anthropic", "gemini"})
_HONCHO_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class HonchoLLMFeatureConfig(BaseModel):
    """One Honcho LLM feature route (``deriver``/``dialectic``/``summary``/
    ``dream``/``embedding``) inside ``[honcho.llm]``.

    Empty ``model``/``base_url`` mean "use the local-default for this
    feature" — resolved by :mod:`hal0.memory.honcho_env` at render time, not
    here, since the defaults depend on the running hal0-api's slot names.
    """

    model_config = {"populate_by_name": True, "extra": "ignore"}

    transport: str = Field(
        default="openai",
        description="Honcho LLM transport for this feature. One of 'openai' | 'anthropic' | 'gemini'.",
    )
    model: str = Field(
        default="",
        description="Model id for this feature. Empty = per-feature local default.",
    )
    base_url: str = Field(
        default="",
        description="Upstream base URL for this feature. Empty = local hal0-api gateway.",
    )
    api_key_env: str = Field(
        default="",
        description=(
            "Name of the env var carrying the API key for a cloud upstream. "
            "Empty for the local (keyless) gateway."
        ),
    )

    @field_validator("transport")
    @classmethod
    def _transport_known(cls, v: str) -> str:
        s = str(v or "openai").strip().lower()
        if s not in _VALID_HONCHO_TRANSPORTS:
            raise ValueError(
                f"honcho llm transport {v!r} must be one of {sorted(_VALID_HONCHO_TRANSPORTS)}"
            )
        return s


class HonchoLLMConfig(BaseModel):
    """``[honcho.llm]`` block — per-feature model routing for the Honcho stack."""

    model_config = {"populate_by_name": True, "extra": "ignore"}

    deriver: HonchoLLMFeatureConfig = Field(default_factory=HonchoLLMFeatureConfig)
    dialectic: HonchoLLMFeatureConfig = Field(default_factory=HonchoLLMFeatureConfig)
    summary: HonchoLLMFeatureConfig = Field(default_factory=HonchoLLMFeatureConfig)
    dream: HonchoLLMFeatureConfig = Field(default_factory=HonchoLLMFeatureConfig)
    embedding: HonchoLLMFeatureConfig = Field(default_factory=HonchoLLMFeatureConfig)
    embedding_dimensions: int = Field(
        default=1024,
        ge=32,
        le=4096,
        description="Embedding vector dimensionality Honcho stores (EMBEDDING_VECTOR_DIMENSIONS).",
    )


class HonchoConfig(BaseModel):
    """[honcho] section of hal0.toml — self-hosted Honcho v3 memory stack.

    Honcho is an opt-in alternative memory provider (see
    ``memory.agent_providers``) rendered to ``/etc/hal0/honcho.env`` for its
    docker compose stack by :mod:`hal0.memory.honcho_env`. Disabled by
    default; hal0's own ``[memory].engine`` stays 'hindsight' regardless.
    """

    model_config = {"populate_by_name": True, "extra": "ignore"}

    enabled: bool = Field(
        default=False, description="Provision + render config for the Honcho stack."
    )
    port: int = Field(
        default=8000, ge=1, le=65535, description="Host port Honcho's API listens on."
    )
    workspace: str = Field(
        default="hal0",
        description="Unified Honcho workspace name shared by non-private agents.",
    )
    user_peer: str = Field(
        default="operator",
        description="Single human peer id shared by all clients in the unified workspace.",
    )
    auth_enabled: bool = Field(
        default=False,
        description=(
            "Require Bearer auth on the Honcho API. Default False (loopback-only "
            "posture, no Bearer needed on LAN)."
        ),
    )
    llm: HonchoLLMConfig = Field(default_factory=HonchoLLMConfig)

    @field_validator("workspace", "user_peer")
    @classmethod
    def _name_grammar(cls, v: str, info: Any) -> str:
        if not v or not _HONCHO_NAME_RE.match(v):
            raise ValueError(
                f"honcho.{info.field_name} {v!r} must match Honcho's peer/workspace "
                r"name pattern ^[a-zA-Z0-9_-]+$"
            )
        return v


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

    ``extra="forbid"`` (P3-schema Part C): a leaf tunable table. Previously
    had no explicit ``model_config`` (pydantic's default is ``"ignore"``, not
    ``"allow"``) -- made explicit here rather than left implicit.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    enabled: bool = True
    retention_days: int = Field(default=30, ge=1)
    # None disables the row cap (retention_days still applies).
    max_rows: int | None = Field(default=50_000, ge=100)


class BrainChatConfig(BaseModel):
    """``[brain_chat]`` — the dashboard's agent-chat steward (hal0-brain).

    Guardrails and loop tuning for the slide-out chat that administers the
    instance through tools (slots, models, benchmarks, the Operator Board).

    ``enabled`` is a hard kill switch: when false the endpoint refuses every
    turn, so the steward chat is off. ``read_only`` keeps the chat answering
    and reading state but refuses every mutating or admin-write tool
    server-side — a guardrail that holds INDEPENDENTLY of the ``hal0-brain``
    persona's ``tools_allowed`` / approval policy (a persona edit can loosen
    the persona, never this). ``model`` overrides which model/slot the chat
    drives (e.g. ``hal0/npu`` to run the steward on the NPU chat slot); empty
    keeps the persona's ``preferred_model`` (``hal0/brain``, which itself
    falls back to the ``agent`` slot). ``max_rounds`` bounds the per-turn tool
    loop (runaway backstop); ``completion_timeout_s`` is the transport timeout
    for each LLM round against the target slot.

    Context floor (fresh-box finding, docs/rework/r4-stage-validation.md
    "steward config note"): whichever slot ends up serving the chat --
    ``model``/``tool_model`` here, the persona's ``preferred_model``, or the
    ``hal0/brain`` -> ``agent`` resolver fallback -- MUST be loaded with at
    least 8k tokens of context. The built-in hal0-brain system prompt alone
    is ~7.3k tokens before any conversation history or tool schemas are
    added; a smaller context window truncates the prompt and the steward
    degrades silently (malformed tool calls, prompt-following failures)
    rather than failing loudly. Separately, a ``model``/``tool_model`` (or
    resolver fallback) that resolves to NO loaded slot at all 404s the self
    ``/v1/chat/completions`` call outright -- surfaced by
    :mod:`hal0.brain.chat` as an actionable SSE ``error`` frame (naming the
    model tried and how to fix it) rather than the raw transport failure.

    ``extra="forbid"`` (P3-schema Part C): a leaf tunable table. Previously
    had no explicit ``model_config`` (pydantic's default is ``"ignore"``, not
    ``"allow"``) -- made explicit here rather than left implicit.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    enabled: bool = True
    # Ships TRUE (KB-2/3): the steward answers and reads state out of the box,
    # but every mutating / admin-write tool is refused server-side until an
    # operator explicitly opts in with [brain_chat] read_only=false. Safe
    # default over convenient default — the widening path is one config line.
    read_only: bool = True
    # Empty → persona preferred_model (hal0/brain). Set to a virtual slot model
    # like "hal0/npu" / "hal0/utility" to drive the steward on that slot; an
    # explicit per-request ``model`` in the chat body still wins over this.
    # Whatever slot this ends up pointing at (directly, via the persona, or
    # via the hal0/brain -> agent resolver fallback) needs >= 8k context —
    # the steward system prompt alone is ~7.3k tokens — and must actually be
    # LOADED, or the chat 404s (see BrainChatConfig docstring above).
    model: str = ""
    # Route tool-calling turns to a capable, tool-format-compatible model. The
    # steward always offers tools, so when set this is the model its tool loop
    # runs on — the escape hatch for boxes whose ``model`` (e.g. a small 1B
    # brain slot) can't emit tool calls the local runtime parses natively (it
    # leaks/500s). Point it at a model that tool-calls cleanly on this runtime
    # (a capable local slot like ``hal0/agent``, or the fallback provider).
    # Default "hal0/agent" per spec-p3-brain.final.md §5a + ADR-0023
    # (always-on anchor every fallback chain ends in). Set to "" to opt back
    # into routing tool turns to ``model``/persona. An explicit per-request
    # ``model`` wins over both.
    tool_model: str = "hal0/agent"
    max_rounds: int = Field(default=8, ge=1, le=100)
    completion_timeout_s: float = Field(default=300.0, gt=0)


class SecurityConfig(BaseModel):
    """[security] section — persisted auth-enforcement posture (KB-1 / O19).

    ``require_auth`` is the durable enable/disable toggle the dashboard
    Security page writes (``PUT /api/auth/require``). Its default is
    ``None`` = *unset*, which the runtime resolves to auth **OFF** — the
    shipped posture as of the 2026-07-19 operator decision: hal0 runs
    trusted-LAN-open by default, matching how the boxes are actually run.

    This deliberately retires KB-1's bind-address / key-presence auto-on:
    that auto-on armed enforcement on a 0.0.0.0 bind but the dashboard
    shipped no login UI, so every route answered ``authentication
    required`` and operators disabled auth wholesale (``HAL0_REQUIRE_AUTH=0``)
    to use the product (docs/rework r4 finding O19). Auth is now
    explicit-enable only.

    Resolution precedence (see :func:`hal0.api.auth.require_auth_enabled`):
    the ``HAL0_REQUIRE_AUTH`` env var wins over this persisted value, which
    in turn wins over the OFF default.

    ``extra="forbid"`` (P3-schema Part C leaf-table policy, same as
    ``[brain_chat]``): a typo'd key in the SECURITY section must fail loudly
    at load, never silently no-op an enforcement toggle.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    require_auth: bool | None = Field(
        default=None,
        description=(
            "Persisted auth-enforcement toggle. None = unset → auth OFF "
            "(trusted-LAN open, the shipped default). True/False are explicit "
            "operator choices. Overridden at runtime by the HAL0_REQUIRE_AUTH "
            "env var."
        ),
    )


class RealtimeConfig(BaseModel):
    """``[realtime]`` — the OpenAI-Realtime WebSocket surface (HP-realtime inc-1).

    Tunes the ``WS /v1/realtime`` endpoint: which local slots serve STT/TTS, the
    fixed pcm16 sample rate, the in-process energy-VAD thresholds (server-VAD
    turn detection — user decision 1), the output audio frame size, and the
    voice-bounded approval wait.

    ``enabled`` is a hard kill switch. ``sample_rate`` is fixed at 24 kHz for the
    MVP (matches the demo client's ``-sample-rate 24000`` and kokoro's native
    pcm output — no resample either direction). ``stt_model`` / ``tts_model`` /
    ``tts_voice`` name the loaded slots the gateway calls over loopback (empty
    ``stt_model``/``tts_model`` falls back to the session's chat model, empty
    ``tts_voice`` lets the tts slot's own default apply).

    VAD (``vad_*``): a zero-dependency energy-RMS detector (the venv has no
    onnxruntime/webrtcvad/silero; adding them would pull a heavy dep + an
    unpullable multi-file model — spec §2d). ``vad_energy_threshold`` is
    normalized RMS (0-1); ``vad_silence_ms`` of trailing silence ends a turn;
    a segment shorter than ``vad_min_speech_ms`` of voiced audio is treated as
    noise and does not fire a turn. A silero backend can replace it in
    increment 2 without touching these knobs' meaning.

    ``approval_wait_s`` bounds how long a gated steward tool may leave the voice
    session silent before the assistant speaks a "still waiting — approve at the
    bell" notice and ends the turn (the brain's own SSE would otherwise block up
    to 300s — spec §2b / user decision 3). ``frame_ms`` is the output audio
    frame size (``response.output_audio.delta`` granularity).

    ``extra="forbid"`` (P3-schema Part C): a leaf tunable table — a typo'd key
    must fail loudly at load, never silently no-op.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    enabled: bool = True
    sample_rate: int = Field(default=24000, ge=8000, le=48000)
    default_model: str = ""
    stt_model: str = ""
    tts_model: str = "kokoro"
    tts_voice: str = ""
    vad_energy_threshold: float = Field(default=0.02, ge=0.0, le=1.0)
    vad_silence_ms: int = Field(default=500, ge=50, le=10000)
    vad_min_speech_ms: int = Field(default=200, ge=0, le=10000)
    vad_window_ms: int = Field(default=20, ge=5, le=100)
    frame_ms: int = Field(default=20, ge=5, le=200)
    approval_wait_s: float = Field(default=20.0, gt=0, le=300.0)
    max_buffer_seconds: float = Field(default=30.0, gt=0, le=600.0)


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
    honcho: HonchoConfig = Field(default_factory=HonchoConfig)
    activity: ActivityConfig = Field(default_factory=ActivityConfig)
    brain_chat: BrainChatConfig = Field(default_factory=BrainChatConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    realtime: RealtimeConfig = Field(default_factory=RealtimeConfig)


# ── Shipped seed-data shims (P3-schema, spec Part A) ──────────────────────────
#
# SEED_PROFILES / SEED_STACKS / PROFILE_BENCH / FAMILY_DEFAULTS used to be
# hardcoded dicts defined inline, above. They are now shipped TOML under
# ``hal0/config/data/`` read + validated + cached by ``hal0.config.seeds``.
# This import is placed at the BOTTOM of the module, after every pydantic
# model above is fully defined, to break the circular import: ``seeds.py``
# needs ``ProfileConfig``/``StackConfig`` (for validation) and this module
# needs ``seeds`` (for the data) — importing ``seeds`` only once schema.py
# has already built every class it might ask for avoids the deadlock (spec
# risk R2; see ``tests/config/test_seeds_data.py`` for the cold-import
# regression test: ``import hal0.config.schema`` and ``import
# hal0.config.seeds`` must each succeed standalone).
#
# These remain plain module-level attributes (not a lazy ``__getattr__``
# hook) so existing test fixtures that do
# ``monkeypatch.setattr(schema, "SEED_STACKS", {})`` /
# ``monkeypatch.setitem(schema.SEED_STACKS, ...)`` keep working unchanged.
from hal0.config import seeds as _seeds  # noqa: E402

SEED_PROFILES: dict[str, dict[str, object]] = _seeds.seed_profiles()
SEED_STACKS: dict[str, StackConfig] = _seeds.seed_stacks()
PROFILE_BENCH: dict[str, dict[str, float]] = _seeds.profile_bench()
FAMILY_DEFAULTS: dict[str, str] = _seeds.family_defaults()


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
    "BrainChatConfig",
    "DeviceLiteral",
    "DispatcherConfig",
    "GPUInfo",
    "Hal0Config",
    "HardwareInfo",
    "HonchoConfig",
    "HonchoLLMConfig",
    "HonchoLLMFeatureConfig",
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
    "SecurityConfig",
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
