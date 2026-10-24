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
    HardwareInfo     — /etc/hal0/hardware.json (written by `hal0 config hardware --refresh`)

Port target: haloai lib/config.py (420 lines).
See PLAN.md §3, §5 Tier 1 ("pydantic-validated TOML schema at load time").
"""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import SplitResult, urlsplit

from pydantic import (
    BaseModel,
    Field,
    PrivateAttr,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)

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
# ``meta.schema_version``. v2 (since v0.2) means CapabilitySelection uses
# ``device``; every write stamps it.
CAPABILITIES_SCHEMA_VERSION_CURRENT = 2


# NOTE: ``map_backend_to_device`` now lives in ``hal0.model_meta`` (imported
# above and re-exported via ``__all__``) so the legacy→device translation and
# its unknown-value policy are defined exactly once.


# ── ModelConfig + SlotConfig ───────────────────────────────────────────────────


class ModelConfig(BaseModel):
    """[model] section in a slot TOML: which model the slot loads.

    ADR-0029: the slot names its model and nothing else about it. Context
    size, layers and every other tune live on the slot's profile.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    default: str = Field(
        default="",
        description="Model id from the registry this slot loads.",
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
        default="",
        description=(
            "Default output size (WxH) for image generation requests that name none. "
            "Empty = the model's native size."
        ),
    )
    default_steps: int = Field(
        default=0,
        ge=0,
        description="Default sampler steps.  0 = use the model-class default.",
    )


class ServerConfig(BaseModel):
    """[server] section in a slot TOML: container environment.

    Carries ``env`` (variables injected into the slot container, e.g.
    ``HSA_OVERRIDE_GFX_VERSION``). Launch flags are not slot config: they
    live on the slot's profile (ADR-0029). ``extra="forbid"``: an unknown key
    fails loudly at load time.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

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


#: Slot keys that carried tuning before ADR-0029. Top level and ``[model]``
#: / ``[server]`` tables; see :func:`slot_tuning_keys`.
SLOT_TUNING_TOP_KEYS: tuple[str, ...] = (
    "parallel",
    "chat_template",
    "workers",
    "ctx_size",
    "mtp",
    "speculation",
    "enable_thinking",
    "vision",
)
SLOT_TUNING_MODEL_KEYS: tuple[str, ...] = (
    "context_size",
    "ctx_size",
    "n_gpu_layers",
    "rope_freq_base",
    "extra",
)
SLOT_TUNING_SERVER_KEYS: tuple[str, ...] = ("extra_args",)


def slot_tuning_keys(data: Any) -> list[str]:
    """Tuning keys a raw (flat or ``[slot]``-nested) slot dict still carries."""
    if not isinstance(data, Mapping):
        return []
    found: list[str] = []
    tables: list[Mapping[str, Any]] = [data]
    if isinstance(data.get("slot"), Mapping):
        tables.append(data["slot"])
    for table in tables:
        found += [k for k in SLOT_TUNING_TOP_KEYS if k in table and k not in found]
    model = data.get("model")
    if isinstance(model, Mapping):
        found += [f"[model].{k}" for k in SLOT_TUNING_MODEL_KEYS if k in model]
    server = data.get("server")
    if isinstance(server, Mapping):
        found += [f"[server].{k}" for k in SLOT_TUNING_SERVER_KEYS if k in server]
    if isinstance(data.get("defaults"), Mapping):
        found.append("[defaults]")
    return found


#: Keys a slot file used to carry that no longer exist, with what replaced
#: them. ``SlotConfig`` names the replacement when it refuses one, and the
#: slot write boundary (:func:`hal0.slot_config.reject_removed_slot_keys`)
#: refuses it with the same text, so the two surfaces cannot drift. ``enabled``
#: split into two distinct verbs: bind/clear ``[model].default`` (activation)
#: and ``POST /unload`` (lifecycle).
REMOVED_SLOT_KEYS: dict[str, str] = {
    "enabled": (
        "activation is now model-presence — bind or clear '[model].default' "
        "instead; to stop a running slot use POST /api/slots/{name}/unload"
    ),
    "backend": "replaced by 'device' (gpu-rocm, gpu-vulkan, gpu-cuda, cpu or npu)",
    "lru": (
        "retired — eviction order is 'priority' (0-100, lower first); use 'pinned' to exempt a slot"
    ),
}


class SlotConfig(BaseModel):
    """Pydantic model for a single slot's TOML config (slots/<name>.toml).

    Fields correspond to the [slot], [model], [server], [npu] and [image]
    sections. See PLAN.md §2 (filesystem layout).

    ``extra="forbid"`` (ADR-0029 §5): every key a slot file may carry is a
    declared field, so a typo, a retired key or a stray table fails the load
    with the key named (:func:`hal0.config.loader.load_slot_config`) instead of
    riding along unread. Any key a hal0 writer emits must be declared here;
    ``tests/config/test_slot_config_strict.py`` runs every writer and loads what
    it wrote.
    """

    model_config = {"extra": "forbid"}

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
    # ── Placement (ADR-0029) ─────────────────────────────────────────────
    # The slot owns placement as typed fields: ``device`` (above) ·
    # ``n_gpu_layers`` (NGL) · ``threads`` · ``binary`` (runner key), plus the
    # ``image_pin`` escape hatch. Every tune lives on the slot's profile.
    n_gpu_layers: int = Field(
        default=-1,
        description="NGL — layers to offload to GPU; emits ``-ngl``. -1 = all, 0 = CPU only.",
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
    metrics: bool = Field(
        default=True,
        description=(
            "METRICS — emits ``--metrics``, enabling llama-server's "
            "``/metrics`` endpoint (Prometheus counters: tok/s, request "
            "counts, …). Authoritative on the slot (spec-hw-slot-ownership "
            "§2/§4 — an operational flag, never a profile concern). Default "
            "True: the endpoint is loopback-only (127.0.0.1, never proxied "
            "by the gateway) and the per-request counter overhead is "
            "negligible. Without it, ``/metrics`` 501s and "
            "``hal0.slots.metrics_collect`` can never populate tok/s or "
            "request counts (#1810)."
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
    # NOTE: there is no ``enabled`` field (#1369). A non-empty
    # ``[model].default`` IS the activation signal — boot autostart is the
    # Quadlet ``[Install] WantedBy=hal0.target`` stanza gated by
    # ``SlotManager.load()``'s empty-model guard, and every routability check
    # already required a model id. A file still carrying ``enabled`` is refused
    # (REMOVED_SLOT_KEYS); the profile-centric conversion strips it.
    # HAL0-SUNSET: v2.0.0 — runtime is Literal["container"] only; field is ceremony, drop it.
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
            "Profile name (a seed or a profile in profiles.toml). The profile "
            "is the whole tune: flags, context window, reasoning default, "
            "vision and MTP (ADR-0029). The slot supplies placement and the model."
        ),
    )
    type: str | None = Field(
        default=None,
        description=(
            "Slot kind (a hal0.model_meta.SLOT_TYPES member: llm, embedding, "
            "reranking, tts, transcription, image, ...). Read by routing, the "
            "profile-implied defaults and the dashboard grouping. A type-less "
            "llm-shaped file is healed to 'llm' on load."
        ),
    )
    default: bool | None = Field(
        default=None,
        description=(
            "SC-4 per-type default marker: at most one slot per type is the "
            "default (hal0.slots.routing.default_slot_for). The first slot of a "
            "type becomes its default at create."
        ),
    )
    served_by: str | None = Field(
        default=None,
        description=(
            "FLM trio shadows only (stt / embed): the display name of the NPU "
            "anchor slot whose single flm process serves this slot. A shadow "
            "has no process of its own."
        ),
    )
    output_sanity: bool | None = Field(
        default=None,
        description=(
            "Per-slot opt-out for the llm output-sanity gate (#1922): false "
            "skips the post-load completion probe for this slot. None = the "
            "gate runs. The gate's own failure message tells the operator to "
            "set it."
        ),
    )
    system_prompt: str | None = Field(
        default=None,
        description=(
            "Persona text for hal0/<slot> delegation (hal0.slots.routing "
            "LoadedSlot.system_prompt, omni route_to_chat). Empty or absent = "
            "the delegated request carries no system message."
        ),
    )
    # ── TTS request defaults (Settings → Voice) ─────────────────────────
    # Read by /v1/audio/speech at request time: when the body omits the
    # matching param, the serving tts slot's persisted default is injected
    # before dispatch — so changes apply immediately, no container bounce.
    # Declared so the values are validated and schema-visible.
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

    # ``idle_timeout_s`` is a flat top-level field (the loader hoists [slot]
    # keys, not [server] keys); [server] holds only ``env``.
    idle_timeout_s: int = Field(
        default=300,
        ge=0,
        description=(
            "Per-slot idle-TTL override (seconds) for hard eviction "
            "(SlotReaper.evict_timeout_for). 0 disables IDLE-TTL eviction "
            "for this slot only — pressure eviction and pre-load eviction "
            "are separate paths, exempted only by `pinned`, not by this "
            "value."
        ),
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
    autoload: bool | None = Field(
        default=None,
        description=(
            "Explicit boot-start setting (spec 2026-08-02). True → the "
            "generated Quadlet unit carries [Install] WantedBy=hal0.target "
            "and the slot starts at boot; False → the unit exists but "
            "nothing starts it automatically (manual load/swap only). "
            "None (key absent on disk) is the migration shim: derived from "
            "the pre-field implicit signal — non-empty [model].default — by "
            "_derive_autoload below, so pre-field TOMLs keep their boot "
            "behavior. API-created slots always persist an explicit value "
            "(create route defaults it to false)."
        ),
    )
    priority: int = Field(
        default=50,
        ge=0,
        le=100,
        description=(
            "Eviction priority (spec 2026-08-02): lower evicts first, "
            "last_used breaks ties. Orders victims in pressure eviction "
            "(SlotReaper.pressure_evict_once) and pre-load eviction "
            "(preload_evict). Replaces the retired lru=true opt-in — "
            "every non-pinned slot is now a candidate. 100 is NOT a pin "
            "(evicted last, still evictable); use `pinned` to exempt."
        ),
    )

    # Typed sub-tables. ``_flatten_slot_toml`` hands each on-disk table to the
    # field of the same name; any other table is an unknown key and fails the
    # load (extra="forbid"). ``image`` is the on-disk name of ``image_gen``.
    server: ServerConfig = Field(default_factory=ServerConfig)

    npu: NpuConfig | None = Field(
        default=None,
        description=(
            "[npu] table — FLM trio modality toggles (asr, embed). "
            "Absent on non-NPU slots. See NpuConfig."
        ),
    )

    # Persisted image-gen settings for the img (ComfyUI) slot (#599). Defaults
    # apply on slots without an [image] table; the dump serializer elides an
    # all-defaults ImageGenConfig so non-img slots don't grow a stray [image]
    # table on disk.
    image_gen: ImageGenConfig = Field(
        default_factory=ImageGenConfig,
        alias="image",
        description=(
            "[image] table — image-gen settings (idle_restore_minutes, "
            "default_size, default_steps). See ImageGenConfig (#599)."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _refuse_tuning_keys(cls, data: Any) -> Any:
        """A slot file still carrying tuning keys has not been converted.

        ADR-0029 moved every tune onto the slot's profile; the one-shot
        conversion (``hal0 profile migrate``) removes these keys. A file that
        still has one fails to load with ``slot.unconverted`` rather than
        launching with a value nothing reads.
        """
        found = slot_tuning_keys(data)
        if found:
            raise ValueError(
                f"slot.unconverted: {', '.join(found)} belong(s) on a profile now "
                "(ADR-0029); run `hal0 profile migrate` to convert this slot"
            )
        return data

    @model_validator(mode="after")
    def _derive_autoload(self) -> SlotConfig:
        """Resolve the migration shim: absent key → pre-field implicit signal.

        A TOML written before the field existed boot-started iff it had a
        model bound (#1369 activation semantics); deriving here means every
        config READ resolves autoload from that signal. This does NOT get
        written back to disk in production — the config write path merges
        RAW dicts (reconcile_slot_updates), not this pydantic model, so an
        absent-key TOML stays absent-key and the shim re-derives on every
        subsequent read until an explicit `autoload` key is authored (e.g.
        via the create route's `body.setdefault("autoload", False)`, or an
        operator toggling the drawer).
        """
        if self.autoload is None:
            self.autoload = bool(self.model.default)
        return self

    @model_serializer(mode="wrap")
    def _dump_as_toml_tables(self, handler: Any) -> dict[str, Any]:
        """Dump in the on-disk table shape: ``[server]`` / ``[npu]`` / ``[image]``.

        An all-default ``[server]``, an absent ``[npu]`` and an all-default
        ``[image]`` are elided, so a slot that never set them doesn't grow an
        empty table on disk, and the typed ``image_gen`` field dumps under its
        TOML name ``image``. Readers of a dumped config therefore see the same
        keys they see in a raw slot dict.
        """
        data: dict[str, Any] = handler(self)
        server = data.get("server")
        if isinstance(server, dict):
            cleaned = {k: v for k, v in server.items() if v is not None}
            if cleaned:
                data["server"] = cleaned
            else:
                data.pop("server")
        if data.get("npu") is None:
            data.pop("npu", None)
        # ``by_alias`` dumps already say ``image``; either way elide the default.
        image = data.pop("image_gen", None)
        image = data.pop("image", None) if image is None else image
        if isinstance(image, dict) and image != ImageGenConfig().model_dump():
            data["image"] = image
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

#: llama.cpp device name per slot backend (the slot's device). The container
#: always sees the chosen GPU as index 0, so the name never carries
#: ``gpu_index``. Placement, so it is emitted in the slot layer of the launch
#: argv (``--device``, and ``--spec-draft-device`` for embedded MTP heads).
_LANE_DEVICE: dict[str, str] = {
    "rocm": "ROCm0",
    "vulkan": "Vulkan0",
    "cuda": "CUDA0",
    "cpu": "none",
}

#: ``speculation`` on a profile (ADR-0031): ``auto`` drafts through whatever the
#: model carries (a drafter file, else MTP heads), ``off`` never speculates,
#: ``ngram`` uses n-gram lookup and needs no extra file.
SpeculationMode = Literal["auto", "off", "ngram"]

#: Shared tail of every draft bundle: the KV cache types for the draft context.
_DRAFT_KV = " --spec-draft-type-k q8_0 --spec-draft-type-v q8_0"

#: Shared draft scheduling tail (ADR-0031 §3: it applies to every kind).
_DRAFT_SCHED = (
    " --spec-draft-threads 16 --spec-draft-threads-batch 32"
    " --spec-draft-poll 1 --spec-draft-poll-batch 1"
)

#: Bench-tuned draft-speculation defaults per draft kind (profile layer; the
#: draft device is placement and comes from the slot, and ``--spec-type`` is a
#: model fact emitted in the model layer). A profile may override any of them
#: in its own ``flags``, which follow the bundle and so win (last-wins). The
#: ``dflash`` ``--spec-draft-n-max`` is a ``{n_max}`` placeholder the launch
#: path fills from the drafter's block size (capped at 8).
DRAFT_BUNDLE: dict[str, str] = {
    "dflash": "--spec-draft-ngl all --spec-draft-n-max {n_max}" + _DRAFT_KV + _DRAFT_SCHED,
    "eagle3": "--spec-draft-ngl all --spec-draft-n-max 4" + _DRAFT_KV + _DRAFT_SCHED,
    "simple": (
        "--spec-draft-ngl all --spec-draft-n-max 8 --spec-draft-n-min 0"
        " --spec-draft-p-min 0.75" + _DRAFT_KV + _DRAFT_SCHED
    ),
    "mtp": (
        "--spec-draft-ngl all"
        " --spec-draft-n-max 4"
        " --spec-draft-n-min 0"
        " --spec-draft-p-min 0.0"
        " --spec-draft-p-split 0.10" + _DRAFT_KV + _DRAFT_SCHED
    ),
}

#: The MTP-heads bundle (no ``--spec-type`` token: the model layer owns it).
MTP_DRAFT_BUNDLE: str = DRAFT_BUNDLE["mtp"]

#: ``speculation = "ngram"``: llama.cpp's ``--spec-default`` n-gram lookup.
NGRAM_BUNDLE: str = (
    "--spec-type ngram-mod --spec-ngram-size-n 24 --spec-ngram-size-m 48 --spec-ngram-n-max 64"
)


def lane_device(backend: str | None) -> str | None:
    """The llama-server ``--device`` for a slot whose device maps to *backend*.

    ``rocm`` -> ``ROCm0``, ``vulkan`` -> ``Vulkan0``, ``cuda`` -> ``CUDA0`` and
    ``cpu`` -> ``none``; ``None`` for an unknown or empty backend.
    """
    return _LANE_DEVICE.get((backend or "").lower())


#: Seed profile catalog — externalized to shipped TOML (P3-schema, spec
#: Part A). See ``hal0/config/data/seed_profiles.toml`` for the seed
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


#: Plausible context window range for a profile's ``ctx_size`` (the launch
#: path's ``--ctx-size``). Below 128 llama-server cannot run; above 2**24 is
#: never a real window.
CTX_SIZE_MIN = 128
CTX_SIZE_MAX = 2**24


class ProfileConfig(BaseModel):
    """One ``[profile.<name>]`` entry in profiles.toml: a complete tune.

    ADR-0029: the profile holds every tune a slot launches with — the free
    llama-server flags, the context window, the reasoning default, whether
    the model's mmproj loads and whether MTP drafts. The model holds only
    facts about its file; the slot holds placement (device, layers, threads,
    runner, port) plus which model and which profile. A model runs
    differently by running under a different profile.

    ``runner`` is the profile-carried runtime: applying the profile to a slot
    sets the slot's ``binary`` (and its device, for a single-backend runner).
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    flags: str = Field(
        default="",
        description=(
            "Free llama-server flags (KV cache types, batch sizes, sampling, "
            "reasoning, --parallel, ...). Never hardware (slot), managed "
            "(--model/--port/--ctx-size/...) or model-fact flags (--jinja, "
            "--chat-template*, --mmproj, --no-mmproj)."
        ),
    )
    ctx_size: int | None = Field(
        default=None,
        ge=CTX_SIZE_MIN,
        le=CTX_SIZE_MAX,
        description=(
            "Context window (--ctx-size). None = the model's native window "
            "capped at 32768, or 8192 when the native window is unknown."
        ),
    )
    enable_thinking: bool | None = Field(
        default=None,
        description=(
            "Request-time reasoning default for slots running this profile. "
            "None = off unless the request asks; always overridable per request."
        ),
    )
    vision: Literal["auto", "off"] = Field(
        default="auto",
        description="'auto' loads the model's mmproj when it has one; 'off' never does.",
    )
    speculation: SpeculationMode = Field(
        default="auto",
        description=(
            "'auto' speculates with whatever the model carries (a paired drafter, "
            "else its MTP heads) when the runner supports it; 'ngram' uses n-gram "
            "lookup and needs no extra file; 'off' never speculates."
        ),
    )
    runner: str | None = Field(
        default=None,
        description=(
            "Optional RUNNER_IMAGES key (the profile-carried runtime). Applying "
            "this profile sets the slot's binary to this runtime and, for a "
            "single-backend runtime, derives the device. None = the slot's own runner."
        ),
    )
    cloned_from: str | None = Field(
        default=None,
        description="Provenance: the profile this one was cloned or converted from. Informational.",
    )
    intent: str = Field(
        default="",
        description="Human label for what this profile is for (the dashboard card headline).",
    )


class ProfilesConfig(BaseModel):
    """Parsed profiles.toml — top-level ``[profile]`` table of custom profiles.

    Seed profiles are not stored here; they are overlaid from code on every
    load (``hal0.config.loader.load_profiles_config``).
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    profile: dict[str, ProfileConfig] = Field(default_factory=dict)
    #: Stored entries that failed validation, kept verbatim: name ->
    #: ``{"entry": raw, "reason": str}``. They resolve to nothing (a slot naming
    #: one is refused with ``profiles.invalid``) and are written back unchanged.
    _quarantined: dict[str, dict[str, Any]] = PrivateAttr(default_factory=dict)
    #: Unknown top-level keys, kept verbatim and written back unchanged.
    _unknown: dict[str, Any] = PrivateAttr(default_factory=dict)

    @property
    def quarantined(self) -> dict[str, dict[str, Any]]:
        return self._quarantined


# ── Stacks ────────────────────────────────────────────────────────────────────
# A Stack is a named, portable bundle of slots + their profiles + model
# assignments + capability selections. Stored single-file in stacks.toml keyed
# by slug, mirroring profiles.toml. See docs/superpowers/specs/2026-06-19-stacks-design.md.

# Stacks carry their own schema version (independent of hal0.toml meta.schema_version),
# stamped on every StackConfig and on the export envelope (PR-3).
STACK_SCHEMA_VERSION_CURRENT = 3

#: Profile export envelopes carry their own schema version (independent of
#: hal0.toml meta.schema_version), stamped on every ``.hal0profile.json`` export.
PROFILE_SCHEMA_VERSION_CURRENT = 2

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
        default_factory=list,
        description=(
            "Runnable backends, e.g. ['rocm','vulkan'] — vestigial lane hints kept for "
            "transport; ``provider`` is the engine-identity source of truth (Task 3)."
        ),
    )
    provider: str | None = Field(
        default=None,
        description=(
            "Engine identity (a hal0.model_meta.RUNTIME_FAMILIES member), embedded "
            "verbatim from the source row's explicit provider, or derived from "
            "``backends`` via hal0.model_meta.derive_model_provider when the row had "
            "none set. None only for a bare-id unresolvable ref."
        ),
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


def resolve_chat_template(model_info: Mapping[str, Any]) -> str | None:
    """The model's chat-template id, or None for the GGUF-embedded template.

    ADR-0029: the chat template is a fact about the model file (the
    registry row's ``chat_template``). ``'auto'`` (or empty/None) means the
    template embedded in the GGUF (no ``--chat-template-file``).
    """
    val = model_info.get("chat_template")
    if val and val != "auto":
        return str(val)
    return None


# ── UpstreamsConfig ────────────────────────────────────────────────────────────


_VALID_UPSTREAM_KINDS = frozenset({"slot", "remote"})
# The full set implemented by UpstreamRegistry.auth_headers() — and this
# comment is now enforced (tests/security/test_upstream_auth_contract.py).
# "google_query" sat here for releases with no implementation behind it, so
# Google AI Studio dispatched unauthenticated while the UI showed "key set"
# (#1513).
_VALID_AUTH_STYLES = frozenset({"bearer", "anthropic", "header", "none"})

#: Retired auth styles → the style that replaces them on read. Dropping
#: "google_query" from the enum outright would hard-fail config load on any
#: box that already has a Google upstream configured; coercing it to "bearer"
#: both keeps that box booting AND starts authenticating its Gemini calls,
#: because bearer is what the endpoint it points at actually accepts.
_AUTH_STYLE_ALIASES: dict[str, str] = {"google_query": "bearer"}


def coerce_auth_style(style: str) -> str:
    """Map a retired auth style onto its replacement; pass others through."""
    return _AUTH_STYLE_ALIASES.get((style or "").strip().lower(), style)


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
    hal0_peer: bool | None = Field(
        default=None,
        description=(
            "Is this upstream another hal0 whose internal dashboard API "
            "(/api/stats/hardware, /api/slots/metrics) may be polled? "
            "Unset (null) auto-derives from the URL host: private/loopback "
            "hosts are treated as peers, public hosts never are, so "
            "hal0-internal paths are never sent to a third-party provider "
            "(issue #1425). Set true to opt a public-hostname hal0 peer in."
        ),
    )

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
        # Coerce retired styles before validating (#1513) — an existing box
        # carrying auth_style = "google_query" must keep booting, and must
        # start authenticating rather than continue dispatching naked.
        coerced = coerce_auth_style(v)
        if coerced != v:
            # stdlib logging here (this module predates the structlog sweep),
            # so %-style rather than kwargs.
            log.warning(
                "upstream.auth_style_retired: auth_style %r was never "
                "implemented and dispatched unauthenticated; reading it as "
                "%r. Update /etc/hal0/upstreams.toml to silence this.",
                v,
                coerced,
            )
        if coerced not in _VALID_AUTH_STYLES:
            raise ValueError(
                f"auth_style {v!r} is not valid; choose from {sorted(_VALID_AUTH_STYLES)}"
            )
        return coerced

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

    Written by `hal0 config hardware --refresh` (hal0.hardware.probe).  Read by the slot
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
    kfd_present: bool = Field(
        default=False,
        description=(
            "Whether /dev/kfd is present AND usable by the slot-runner "
            "identity, per hal0.providers._gpu.kfd_present. This is the "
            "same host-truth signal the backend's device/profile "
            "reconciliation gate (_reconcile_device_profile) uses to decide "
            "ROCm feasibility. Distinct from gpus[].compute_capable, which "
            "requires a HOST rocminfo probe to succeed — many boxes run "
            "ROCm slots fine via a containerized userland with no host "
            "rocminfo, so compute_capable alone under-reports ROCm "
            "feasibility. Consumers should treat ROCm as feasible when "
            "EITHER signal is true."
        ),
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
            "Default idle-TTL (seconds) for hard eviction, applied per slot "
            "when the slot's own TOML has no idle_timeout_s override. 0 "
            "disables IDLE-TTL eviction for slots using this default — "
            "pressure eviction and pre-load eviction are separate paths, "
            "exempted only by `pinned`, not by this value."
        ),
    )
    evict_pressure_mb: int = Field(
        default=8192,
        ge=0,
        description=(
            "Host free-RAM floor (MiB) for pressure-driven eviction (#903, "
            "spec 2026-08-02). When host MemAvailable drops below this "
            "value, every non-pinned resident slot is a candidate, evicted "
            "in priority order (lowest `priority` first, "
            "least-recently-used as the tie-break within a tier) until "
            "free RAM is back above the floor. 0 disables pressure "
            "eviction. `pinned` is the only exemption — `idle_timeout_s = "
            "0` on a slot does not exempt it from this path."
        ),
    )
    preload_evict_enabled: bool = Field(
        default=True,
        description=(
            "Free memory SYNCHRONOUSLY before a load starts, when the "
            "incoming model's estimated footprint plus "
            "preload_evict_headroom_mb would not fit in projected free "
            "memory. Evicts non-pinned resident slots in priority order "
            "(lowest `priority` first, least-recently-used as the "
            "tie-break within a tier) — the same eligibility "
            "evict_pressure_mb uses — until it fits, or fails the load "
            "with a clear error if it still doesn't fit after evicting "
            "everything eligible. Set to false to rely solely on the "
            "reactive pressure sweeper (#903), which only reacts after "
            "the fact on a timer."
        ),
    )
    preload_evict_headroom_mb: int = Field(
        default=1024,
        ge=0,
        description=(
            "Extra free-RAM slack (MiB) required on top of a model's "
            "estimated footprint before pre-load eviction "
            "(preload_evict_enabled) considers a load safe to start. "
            "Absorbs error in the coarse file-size + KV-cache footprint "
            "estimate and runtime/container overhead the estimate doesn't "
            "capture."
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
            "door. A specific interface IP (e.g. ``192.0.2.10``) binds just that "
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

    default_images: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Per-runner-family operator default-image overrides "
            "(runner-image-catalogue v2). Keys are hal0.runners.RUNNER_IMAGES "
            "family keys (standard, cpu, cuda, flm, kokoro, "
            "moonshine, qwen3tts, comfyui); values are full image refs "
            "(e.g. 'ghcr.io/hal0ai/hal0-runner-standard:<tag>'). A family listed "
            "here launches that ref instead of the release-baked default "
            "wherever no per-slot image_pin applies "
            "(hal0.providers.container._resolve_image_ref). CLEAR SEMANTICS: "
            "the settings PUT deep-merge has no key-delete idiom, so sending "
            "an explicit null value for a family removes the override (the "
            "validator drops null-valued keys); an empty string is rejected. "
            "Applies on the next slot (re)start."
        ),
    )

    @field_validator("default_images", mode="before")
    @classmethod
    def _default_images_known_families(cls, v: Any) -> Any:
        """Validate the override map — and implement its clear idiom.

        A ``None`` VALUE is the documented way to clear one family's
        override (the settings PUT deep-merge replaces scalars and has no
        delete idiom, so ``{"standard": null}`` must mean "remove the key"
        rather than fail validation). Unknown family keys and empty/
        non-string refs are rejected with the offending key named — the
        family vocabulary is ``hal0.runners.RUNNER_IMAGES`` (imported
        lazily). Keys a removed runner left behind are rewritten by the
        one-shot runner-system migration before config is ever validated
        (``hal0.config.migrations.runner_system``).
        """
        if v is None:
            return {}
        if not isinstance(v, dict):
            return v  # let pydantic's dict[str, str] coercion produce the error
        from hal0.runners import RUNNER_IMAGES

        cleaned: dict[str, Any] = {}
        for key, ref in v.items():
            if ref is None:
                continue  # explicit null = clear this family's override
            if key not in RUNNER_IMAGES:
                raise ValueError(
                    f"[slots].default_images key {key!r} is not a known runner "
                    f"family (valid: {', '.join(sorted(RUNNER_IMAGES))})"
                )
            if not isinstance(ref, str) or not ref.strip():
                raise ValueError(
                    f"[slots].default_images[{key!r}] must be a non-empty image "
                    "ref (send null to clear the override)"
                )
            cleaned[key] = ref.strip()
        return cleaned

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
            "Streaming requests disable this per-request while the stall guard "
            "is active so the guard owns the body bound; it still bounds the "
            "wait for response headers on those requests, since the guard's "
            "own clocks only start once headers arrive. With both stall-guard "
            "bounds set to 0 it applies per body read as the transport "
            "backstop. Range 30-600."
        ),
    )
    stream_total_timeout_s: float = Field(
        default=900.0,
        ge=0.0,
        le=86400.0,
        description=(
            "Stall guard: max wall-clock duration of a relayed upstream stream "
            "before hal0 cuts it off and emits a terminal diagnostic chunk. "
            "Without this a chatty never-terminating upstream is relayed "
            "forever — each chunk resets the transport read timer (#1893). "
            "Note this is wall clock: a healthy long generation on slow "
            "hardware that outlives the budget is also cut. 0 disables the "
            "bound."
        ),
    )
    stream_idle_timeout_s: float = Field(
        default=300.0,
        ge=0.0,
        le=86400.0,
        description=(
            "Stall guard: max gap between two chunks of a relayed upstream "
            "stream before hal0 cuts it off. Must stay above the slowest "
            "expected prompt-processing silence on a cold CPU slot. "
            "0 disables the bound."
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


class UpdatesConfig(BaseModel):
    """``[updates]`` section in hal0.toml.

    ``extra="forbid"``: a leaf tunable table.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    auto_restart_image_drift: bool = Field(
        default=True,
        description=(
            "At every hal0-api start, restart slots whose running container uses a "
            "different runner image than the one a fresh render would pick (a stale "
            "image keeps serving the build a release replaced). Slots with a request "
            "in flight, or whose activity cannot be read (no metrics endpoint, e.g. "
            "image/speech runtimes), are skipped. Argv-only drift is never restarted. Consumed once "
            "at startup, so a change needs a hal0-api restart."
        ),
    )


# ── MemoryGraphConfig (ADR-0023) ──────────────────────────────────────────────


class MemoryGraphConfig(BaseModel):
    """[memory.graph] section of hal0.toml (ADR-0023).

    Controls graph extraction on the Hindsight engine. Hindsight builds its graph
    natively via its own extraction LLM; hal0 points that LLM at the fixed
    virtual id ``hal0/memory`` (through a systemd drop-in) and resolves it on
    every call to ``hal0/<extraction_slot>`` or, when set, to
    ``extraction_model`` (#2919), so the engine's model changes without a
    restart.

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
            "consolidation / reflect while extraction_model is unset. hindsight-api "
            "calls the fixed id hal0/memory; hal0's /v1 resolves it to this slot "
            "per call, so a change needs no engine restart. Validated against the "
            "live enabled-llm-slot set by the API at set-time."
        ),
    )
    extraction_model: str | None = Field(
        default=None,
        description=(
            "Optional upstream model the memory engine runs on instead of the "
            "local extraction_slot (#2919): an id hal0's own /v1 already routes to "
            "a configured upstream, e.g. 'MiniMax-M2.5' or 'minimax/minimax-m3'. "
            "hindsight-api always calls the fixed virtual id hal0/memory; hal0 "
            "resolves that to this model when set, else to hal0/<extraction_slot>, "
            "so switching needs no engine restart. Validated against the live "
            "upstream catalogs by the API at set-time. Never a hal0/<slot> id — "
            "a local slot belongs in extraction_slot."
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
    # Extraction limits (#1834). The extraction slot is a shared inference
    # slot, usually one llama-server with --parallel 1; unbounded extraction
    # (hindsight's own defaults: 32 concurrent LLM calls, 64000 completion
    # tokens, 3 retries at each of two layers) saturates it and the retain
    # queue never drains. Each knob maps 1:1 onto a hindsight-api env var in
    # the drop-in hal0 already owns; the ranges below are also what the
    # root-side drop-in validator (installer/wrappers/hal0-systemctl) accepts.
    extraction_max_concurrent: int = Field(
        default=1,
        ge=1,
        le=8,
        description=(
            "How many extraction LLM calls Hindsight may have in flight at once "
            "against the extraction slot (HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT). "
            "1 serialises them, which is right for a single llama-server; raise it "
            "only for a slot that genuinely serves parallel requests. Applied via "
            "the hindsight-api drop-in; the daemon restarts to pick it up."
        ),
    )
    extraction_max_tokens: int = Field(
        default=4096,
        ge=3072,
        le=32768,
        description=(
            "Completion-token cap per extraction call "
            "(HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS). Bounds how long one "
            "call can run; the floor stays above Hindsight's retain chunk size "
            "(3000), which it refuses to start without. Applied via the "
            "hindsight-api drop-in; the daemon restarts to pick it up."
        ),
    )
    extraction_llm_retries: int = Field(
        default=1,
        ge=0,
        le=5,
        description=(
            "Retries of a single extraction LLM call inside one retain attempt "
            "(HINDSIGHT_API_RETAIN_LLM_MAX_RETRIES). Each retry can wait the full "
            "LLM timeout. Applied via the hindsight-api drop-in; the daemon "
            "restarts to pick it up."
        ),
    )
    extraction_task_retries: int = Field(
        default=2,
        ge=0,
        le=10,
        description=(
            "How many times a failed queued memory task is requeued before it is "
            "marked failed (HINDSIGHT_API_WORKER_MAX_RETRIES) and shows under "
            "'Retry failed' on the Memory page. Bounds the total time one document "
            "can occupy the slot. This is Hindsight's worker-wide setting: it "
            "applies to every queued operation (retain, consolidation, "
            "mental-model refresh), not only extraction. Applied via the "
            "hindsight-api drop-in; the daemon restarts to pick it up."
        ),
    )
    extraction_retry_backoff_s: int = Field(
        default=120,
        ge=10,
        le=3600,
        description=(
            "Seconds a failed queued memory task waits before it is requeued "
            "(HINDSIGHT_API_WORKER_TASK_RETRY_BACKOFF_SECONDS), so a retry does "
            "not land on a slot that is still busy with the attempt that just "
            "timed out. Worker-wide like extraction_task_retries: every queued "
            "operation shares it. Applied via the hindsight-api drop-in; the "
            "daemon restarts to pick it up."
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

    @field_validator("extraction_model", mode="before")
    @classmethod
    def extraction_model_grammar(cls, v: object) -> str | None:
        # Shape-only: an upstream model id as hal0's /v1 catalog spells it
        # (OpenRouter 'vendor/model', MiniMax 'MiniMax-M2.5', ':' tags). The
        # dashboard sends "" to return to the local slot, so blank clears.
        # Existence in a live upstream catalog is the API's check at set-time.
        import re as _re

        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("memory.graph.extraction_model must be a string or null")
        v = v.strip()
        if not v:
            return None
        if v.startswith("hal0/"):
            raise ValueError(
                "memory.graph.extraction_model names an upstream model id; a local "
                "slot is set with memory.graph.extraction_slot"
            )
        if not _re.match(r"^[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}$", v):
            raise ValueError(
                "memory.graph.extraction_model must be a model id "
                "(alnum/-/_/./:// , ≤128 chars, leading alphanumeric, no spaces)"
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


HermesCommandApprovalsLiteral = Literal["smart", "manual"]
HERMES_COMMAND_APPROVALS_MODES: tuple[str, ...] = ("smart", "manual")
HERMES_DEFAULT_DELEGATION_SLOT = "agent"


class HermesAgentSettings(BaseModel):
    """``[hermes]`` table of ``/etc/hal0/agents/hermes.toml`` — operator settings.

    Deliberately NOT a field of :class:`AgentConfig`: that model is validated
    strictly by the MCP allow-list classifier
    (:mod:`hal0.agents.mcp_client`), so a typed field there would turn a
    hand-edited typo in this table into a broken MCP client. The table rides
    along through ``AgentConfig``'s ``extra="allow"``; this model validates
    it on every write (API/CLI) and the provisioner reads it leniently
    (:mod:`hal0.agents.hermes_command_approvals`).

    ``command_approvals`` maps to Hermes's own ``approvals.mode``: whether a
    dangerous terminal command or ``execute_code`` script is first judged by
    an auxiliary model call (``smart``) or always asked of a human
    (``manual``). Unrelated to hal0's MCP tool approval queue
    (``/api/agent/approvals``), which gates hal0 MCP tool calls.

    ``extra="forbid"``: a leaf tunable table.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    command_approvals: HermesCommandApprovalsLiteral = Field(
        default="smart",
        description=(
            "Hermes command approvals (Hermes's approvals.mode). 'smart' = an "
            "auxiliary model call approves a flagged command, and anything but "
            "APPROVE falls back to asking a human. 'manual' = a human approves "
            "every flagged command."
        ),
    )
    delegation_slot: str = Field(
        default=HERMES_DEFAULT_DELEGATION_SLOT,
        pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$",
        description=(
            "Name of the hal0 slot whose model Hermes delegate_task children run "
            "on (renders delegation.model). Default 'agent' runs children on the "
            "parent's slot; naming another slot (e.g. 'utility') lets children "
            "run beside the parent instead of queueing behind it on a "
            "single-parallel llama-server. If the named slot is not ready when "
            "Hermes is provisioned, the delegation block is not rendered (an "
            "earlier rendering is left in place) until the next repair."
        ),
    )


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


class MemoryConfig(BaseModel):
    """[memory] section of hal0.toml.

    Container for the per-subsystem memory tunables. Today carries
    ``[memory.graph]`` and ``[memory.embedding]`` (issue #116).
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
            "fan-out). Set false to restore the pre-unified multi-bank model "
            "(private:<agent> / project:<id> banks + fan-out recall)."
        ),
    )
    # HAL0-SUNSET: v2.0.0 — 'cognee' engine literal retired here. It is no
    # longer an accepted config value (was previously a back-compat alias
    # that resolved to hindsight at runtime). provider_from_config's runtime
    # resolution still treats any *unrecognized* engine as Hindsight as a
    # defense-in-depth fallback for callers that bypass this validator
    # (e.g. duck-typed namespaces), but a hal0.toml with engine = "cognee"
    # now fails config validation outright — the Cognee store has been dark
    # since v0.4.
    engine: str = Field(
        default="hindsight",
        description=(
            "Active memory engine. 'hindsight' (default) is the platform "
            "engine; 'mem0' and 'pgvector' are alternates. The legacy "
            "'cognee' value was retired in v1.0.0 (the Cognee store has been "
            "dark since v0.4 and its wrapper was removed) — a hal0.toml "
            "still carrying it must be updated to 'hindsight'."
        ),
    )

    @field_validator("engine")
    @classmethod
    def _engine_is_known(cls, v: str) -> str:
        known = {"hindsight", "mem0", "pgvector"}
        s = str(v or "hindsight").strip().lower()
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

    ``extra="forbid"`` (P3-schema Part C): a leaf tunable table. Previously
    had no explicit ``model_config`` (pydantic's default is ``"ignore"``, not
    ``"allow"``) -- made explicit here rather than left implicit.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    enabled: bool = Field(
        default=True,
        description=(
            "Whether config-mutating actions and system state changes are "
            "recorded to the durable activity store. Consumed once at "
            "create_app(), so a change needs a hal0-api restart."
        ),
    )
    retention_days: int = Field(
        default=30,
        ge=1,
        description=(
            "How long an activity row survives before pruning. Overridden "
            "at the env layer by HAL0_ACTIVITY_RETENTION_DAYS."
        ),
    )
    # None disables the row cap (retention_days still applies).
    #
    # PERSISTENCE (#1665): ``save_hal0_config``/``_maybe_run_config_migrations``
    # both dump with ``exclude_none=True`` — None has no TOML representation
    # and ``tomli_w`` raises TypeError on it. Dumping None for "unlimited"
    # therefore drops the key outright, and the next load restores this
    # 50_000 default: the documented unlimited mode was unreachable through
    # any persisted config. ``0`` is already unreachable as a genuine row cap
    # (below the ``ge=100`` floor), so it is reserved as the on-disk spelling
    # of "unlimited" — see ``_normalise_max_rows`` (load) and
    # ``_serialize_max_rows`` (save) below. Same fix shape as
    # ``BrainChatConfig.tool_model`` / ``BRAIN_TOOL_MODEL_DISABLED`` (#1644):
    # give the unrepresentable sentinel a real word/value on disk instead of
    # relying on ``exclude_none`` to round-trip it.
    max_rows: int | None = Field(
        default=50_000,
        ge=100,
        description=(
            "Row cap for the activity table; oldest rows are pruned past "
            "this count regardless of retention_days. Set to 0 to disable "
            "the cap (retention_days still applies)."
        ),
    )

    @field_validator("max_rows", mode="before")
    @classmethod
    def _normalise_max_rows(cls, v: Any) -> Any:
        """``0`` on disk (or via the API) means "unlimited" — see above."""
        if v == 0:
            return None
        return v

    @model_serializer(mode="wrap")
    def _serialize_max_rows(self, handler: Any) -> dict[str, Any]:
        """Persist ``None`` ("unlimited") as ``0``, not absent (#1665).

        ``exclude_none=True`` drops a None-valued field before any
        field-level serializer would run, so the disabled-cap intent is
        lost silently on write. A wrapping model serializer runs after
        ``handler`` has already applied that exclusion, so it can add the
        sentinel back in only when the key was actually dropped — an
        ``exclude_none=False`` dump (e.g. the API echo) already has
        ``max_rows: None`` in ``data`` and is left untouched.
        """
        data = handler(self)
        if self.max_rows is None and "max_rows" not in data:
            data["max_rows"] = 0
        return data


#: Where the steward sends TOOL turns when nothing else says otherwise. ADR-0023's
#: always-on LLM anchor — the model every ``hal0/<slot>`` fallback chain ends in,
#: and the slot ``installer/etc-hal0/slots/agent.toml`` seeds.
BRAIN_TOOL_MODEL_DEFAULT = "hal0/agent"

#: The spellings that mean "deliberately route tool turns nowhere". Written out
#: because the obvious spelling — the empty string — is indistinguishable on
#: disk from a key nobody set, and silently disabling tool routing is exactly
#: the failure this vocabulary exists to prevent. See
#: :meth:`BrainChatConfig._normalise_tool_model`.
BRAIN_TOOL_MODEL_DISABLED = frozenset({"off", "none", "disabled"})


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
    ``model`` here, the persona's ``preferred_model``, or the ``hal0/brain``
    -> ``agent`` resolver fallback -- MUST be loaded with at least 8k tokens
    of context. The built-in hal0-brain system prompt alone is ~7.3k tokens
    before any conversation history or tool schemas are added; a smaller
    context window truncates the prompt and the steward degrades silently
    (malformed tool calls, prompt-following failures) rather than failing
    loudly. Separately, a ``model`` (or resolver fallback) that resolves to
    NO loaded slot at all 404s the self ``/v1/chat/completions`` call
    outright -- surfaced by :mod:`hal0.brain.chat` as an actionable SSE
    ``error`` frame (naming the model tried and how to fix it) rather than
    the raw transport failure.

    ``extra="forbid"`` (P3-schema Part C): a leaf tunable table. Previously
    had no explicit ``model_config`` (pydantic's default is ``"ignore"``, not
    ``"allow"``) -- made explicit here rather than left implicit.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    enabled: bool = Field(
        default=True,
        description=(
            "Hard kill switch for the dashboard's agent-chat steward. When "
            "false, every chat turn is refused and the slide-out chat is off."
        ),
    )
    # Ships TRUE (KB-2/3): the steward answers and reads state out of the box,
    # but every mutating / admin-write tool is refused server-side until an
    # operator explicitly opts in with [brain_chat] read_only=false. Safe
    # default over convenient default — the widening path is one config line.
    read_only: bool = Field(
        default=True,
        description=(
            "When true (the shipped default), the steward answers and reads "
            "state but every mutating or admin-write tool is refused "
            "server-side — independently of the persona's own tool policy. "
            "The steward's own memory_add into its private namespace is the "
            "one exception (#2658). "
            "Set false to let the steward act on the instance through tools."
        ),
    )
    # Empty → persona preferred_model (hal0/brain). Set to a virtual slot model
    # like "hal0/npu" / "hal0/utility" to drive the steward on that slot; an
    # explicit per-request ``model`` in the chat body still wins over this.
    # Whatever slot this ends up pointing at (directly, via the persona, or
    # via the hal0/brain -> agent resolver fallback) needs >= 8k context —
    # the steward system prompt alone is ~7.3k tokens — and must actually be
    # LOADED, or the chat 404s (see BrainChatConfig docstring above).
    model: str = Field(
        default="",
        description=(
            "Which model/slot drives the steward chat. Empty keeps the "
            "persona's preferred_model (hal0/brain, itself falling back to "
            "the agent slot); set to a virtual slot like 'hal0/npu' to pin "
            "the steward there. Whatever this resolves to must be loaded "
            "with >= 8k context, or the chat fails outright."
        ),
    )
    # Route tool-calling ROUNDS to a capable, tool-format-compatible model —
    # the escape hatch for boxes whose ``model`` (the shipped ~1.1B brain slot)
    # can't emit tool calls the local runtime parses natively (it leaks/500s).
    # Point it at a model that tool-calls cleanly on this runtime (a capable
    # local slot like ``hal0/agent``, or the fallback provider). Default
    # "hal0/agent" per spec-p3-brain.final.md §5a + ADR-0023 (always-on anchor
    # every fallback chain ends in), resolved through the normal virtual-model
    # chain (:mod:`hal0.normalize.resolver`).
    #
    # PER ROUND, NOT PER CONVERSATION. Plain-chat rounds stay on ``model`` —
    # that is the whole point of a fast 1B steward — and only a round that
    # needs a tool reroutes here; once a turn is in a tool chain the tool model
    # finishes it (it, not the 1B, reads the ``role: tool`` payloads). An
    # explicit per-request ``model`` sets the CHAT model and does NOT suppress
    # the reroute: the dashboard sends one on every message, so treating it as
    # a whole-turn pin would disable this feature in the only UI that uses it.
    # Implementation + measured failure modes: :func:`hal0.brain.chat
    # ._tool_routing_llm`.
    #
    # EMPTY STRING IS NOT "DISABLED" — see _normalise_tool_model below. A live
    # box was found with `[brain_chat] tool_model = ""`, which silently
    # overrode this default; it is now coerced back to the default with a loud
    # warning. Opting OUT is spelled explicitly: "off" / "none" / "disabled".
    tool_model: str = Field(
        default=BRAIN_TOOL_MODEL_DEFAULT,
        description=(
            "Where a tool-calling ROUND reroutes when the chat model can't "
            "emit tool calls this runtime parses. Default 'hal0/agent' — the "
            "always-on fallback anchor — but that slot ships unbound on a "
            "fresh install, so the first tool turn has nowhere to go until "
            "an operator pulls a model into it. Per-round, not per-"
            "conversation: plain chat stays on 'model'. An empty string is "
            "coerced back to the default with a warning — to genuinely "
            "disable tool routing, set 'off', 'none', or 'disabled'."
        ),
    )
    max_rounds: int = Field(
        default=8,
        ge=1,
        le=100,
        description=(
            "Runaway backstop: max tool-calling rounds in one chat turn "
            "before the steward stops and returns whatever it has."
        ),
    )
    completion_timeout_s: float = Field(
        default=300.0,
        gt=0,
        description=(
            "Transport timeout (seconds) for each LLM round against the "
            "target slot. A round that exceeds this fails the turn with a "
            "timeout error instead of hanging indefinitely."
        ),
    )

    @field_validator("tool_model", mode="before")
    @classmethod
    def _normalise_tool_model(cls, v: Any) -> str:
        """Coerce an empty ``tool_model`` back to the default, loudly.

        The trap this closes: ``tool_model = ""`` in ``hal0.toml`` is
        indistinguishable, on disk, from "I never set this". It is *not*
        indistinguishable to pydantic — an explicit empty string overrides the
        ``"hal0/agent"`` default — so a config that looks unset silently
        removes the steward's tool-routing target. There is no error, no
        warning, and no observable symptom except that tool turns quietly stop
        going anywhere useful. That is the worst class of config bug: it looks
        like nothing happened.

        Somebody who genuinely wants no tool routing has to say so out loud,
        with one of :data:`BRAIN_TOOL_MODEL_DISABLED`, which normalises to the
        empty string internally. That keeps the "no reroute" behaviour
        reachable while making it impossible to arrive at by accident.
        """
        if v is None:
            return BRAIN_TOOL_MODEL_DEFAULT
        if not isinstance(v, str):
            return v  # let pydantic's own str validation produce the error
        text = v.strip()
        if not text:
            log.warning(
                "[brain_chat] tool_model is empty — an explicit empty string OVERRIDES the "
                "%r default and would leave the steward's tool turns with no target. "
                "Using %r. To genuinely disable tool routing, set tool_model to one of %s.",
                BRAIN_TOOL_MODEL_DEFAULT,
                BRAIN_TOOL_MODEL_DEFAULT,
                sorted(BRAIN_TOOL_MODEL_DISABLED),
            )
            return BRAIN_TOOL_MODEL_DEFAULT
        if text.lower() in BRAIN_TOOL_MODEL_DISABLED:
            return ""
        return text

    @field_serializer("tool_model")
    def _serialize_tool_model(self, value: str) -> str:
        """Persist "disabled" as a word, not the empty string (#1644).

        In-memory/runtime, "no tool routing" is represented as ``""`` — every
        consumer (:func:`hal0.brain.chat._tool_routing_llm` et al.) treats a
        falsy ``tool_model`` as "don't reroute". But :meth:`_normalise_tool_model`
        treats an on-disk ``tool_model = ""`` as "nobody set this" and coerces
        it back to :data:`BRAIN_TOOL_MODEL_DEFAULT`. Dumping ``""`` straight to
        ``hal0.toml`` for the disabled state collided with that rule: save
        wrote ``""``, the next load saw an "unset" empty string and silently
        re-enabled routing.

        Route around the collision by giving the disabled state a word on
        disk. ``"off"`` round-trips cleanly back through
        ``_normalise_tool_model`` to ``""`` on the next load, same as any
        other spelling in :data:`BRAIN_TOOL_MODEL_DISABLED`.
        """
        return "off" if value == "" else value


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

    trust_forwarded_for: bool | None = Field(
        default=None,
        description=(
            "Honour X-Forwarded-For for the login/rotate rate limiter and "
            "caller-IP attribution instead of the raw TCP peer. None = unset "
            "→ OFF (the raw peer is used — safe default, since a client can "
            "forge this header). Only enable when hal0 sits behind a "
            "reverse proxy known to strip/overwrite any client-supplied "
            "X-Forwarded-For (Traefik/nginx/Caddy do this by default) — "
            "otherwise every caller can pick its own rate-limit bucket by "
            "spoofing the header. Overridden at runtime by the "
            "HAL0_TRUST_FORWARDED_FOR env var."
        ),
    )


#: Default port per scheme — an Origin a browser sends never carries it.
_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}


#: One DNS label after IDNA encoding: letters, digits and inner hyphens.
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
#: Characters a stored public_url path may carry (RFC 3986 unreserved plus
#: "/" and "%"). Nothing a shell or an env file would read specially.
_URL_PATH = re.compile(r"^[A-Za-z0-9._~/%-]*$")


#: Characters IDNA2003 (Python's ``idna`` codec) maps differently from the
#: UTS #46 processing browsers use: ``faß.de`` encodes to ``fass.de`` here but
#: to ``xn--fa-hia.de`` in a browser. Storing the codec's answer would allow a
#: different host than the operator named, so these are refused outright.
_IDNA_DEVIATIONS = frozenset("\u00df\u03c2\u200c\u200d")
#: A last label the WHATWG URL parser reads as a number: the browser then
#: parses the whole host as IPv4 (``0x7f.1`` is ``127.0.0.1``) or rejects it.
_NUMERIC_LABEL = re.compile(r"^(?:[0-9]+|0x[0-9a-f]*)$")


def _ipv6_origin_host(addr: ipaddress.IPv6Address) -> str:
    """Serialise ``addr`` the way the WHATWG URL standard does, bracketed.

    Lower-case hex pieces with the first longest run of two or more zero
    pieces compressed to ``::``. Unlike :attr:`IPv6Address.compressed`, an
    IPv4-mapped address stays in hex (``[::ffff:c000:201]``), which is what a
    browser puts in ``Origin``.
    """
    value = int(addr)
    pieces = [(value >> (112 - 16 * i)) & 0xFFFF for i in range(8)]
    best_start, best_len, i = -1, 1, 0
    while i < 8:
        if pieces[i] != 0:
            i += 1
            continue
        j = i
        while j < 8 and pieces[j] == 0:
            j += 1
        if j - i > best_len:
            best_start, best_len = i, j - i
        i = j
    if best_start < 0:
        return "[" + ":".join(f"{p:x}" for p in pieces) + "]"
    head = ":".join(f"{p:x}" for p in pieces[:best_start])
    tail = ":".join(f"{p:x}" for p in pieces[best_start + best_len :])
    return f"[{head}::{tail}]"


def _canonical_host(host: str) -> str | None:
    """Return ``host`` the way a browser serialises it in ``Origin``, or None.

    IPv6 literals use the WHATWG serialisation (``[0:0::1]`` -> ``[::1]``),
    IPv4 literals must be dotted-quad, and DNS names are IDNA-encoded to
    punycode (``bücher.example`` -> ``xn--bcher-kva.example``) and then held
    to a strict letters/digits/hyphen charset. Anything a browser would
    serialise differently, and anything else (shell metacharacters, quotes,
    commas, backslashes, NUL, wildcards), is refused: a stored origin must
    only ever match the host the operator named, and these values reach the
    root-written api.env through the installer.
    """
    if ":" in host:  # urlsplit strips the brackets from an IPv6 literal
        if "%" in host:  # a zone id is local to this box; browsers never send one
            return None
        try:
            return _ipv6_origin_host(ipaddress.IPv6Address(host))
        except ValueError:
            return None
    if re.fullmatch(r"[0-9.]+", host):
        try:
            return str(ipaddress.IPv4Address(host))
        except ValueError:
            return None
    if any(c in _IDNA_DEVIATIONS for c in host):
        return None
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    # One trailing dot (a fully-qualified name) is kept: browsers send it as is.
    core = ascii_host[:-1] if ascii_host.endswith(".") else ascii_host
    labels = core.split(".")
    if len(core) > 253 or not all(_DNS_LABEL.match(label) for label in labels):
        return None
    if _NUMERIC_LABEL.match(labels[-1]):
        return None
    return ascii_host


def _split_http_url(value: str, *, what: str) -> tuple[str, str, SplitResult]:
    """Parse ``value`` as an absolute http(s) URL; return ``(scheme, netloc, parts)``.

    ``netloc`` comes back normalised the way a browser serialises an origin:
    canonical host (see :func:`_canonical_host`) and the scheme's default
    port dropped (``https://x:443`` is sent as ``Origin: https://x``).
    Raises ``ValueError`` with an operator-readable message for anything else.
    """
    raw = value.strip()
    if not raw:
        raise ValueError(f"{what} must not be empty")
    if "://" not in raw:
        raise ValueError(f"{what} {raw!r} must start with http:// or https://")
    try:
        parts = urlsplit(raw)
    except ValueError:
        raise ValueError(f"{what} {raw!r} is not a valid URL") from None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise ValueError(f"{what} {raw!r} must use http or https, not {parts.scheme!r}")
    if "@" in parts.netloc:
        raise ValueError(f"{what} {raw!r} must not contain a user name or password")
    if not parts.hostname:
        raise ValueError(f"{what} {raw!r} has no host")
    if any(c.isspace() for c in raw):
        raise ValueError(f"{what} {raw!r} must not contain whitespace")
    host = _canonical_host(parts.hostname)
    if host is None:
        raise ValueError(
            f"{what} {raw!r} is not a valid host: use a DNS name, an IPv4 address "
            "or a bracketed IPv6 address"
        )
    try:
        port = parts.port
    except ValueError:
        raise ValueError(f"{what} {raw!r} has an invalid port") from None
    if port == 0:
        raise ValueError(f"{what} {raw!r} has an invalid port")
    netloc = host if port is None or port == _DEFAULT_PORTS[scheme] else f"{host}:{port}"
    return scheme, netloc, parts


def normalise_origin(value: str) -> str:
    """Validate ``value`` as a browser origin and return its canonical form.

    An origin is ``scheme://host[:port]`` with ``http``/``https``, no path,
    query or fragment, and no trailing slash (one is tolerated and dropped).
    Used by :class:`NetworkConfig` and by the origin gates' allowlist merge
    (:func:`hal0.api.agents._auth.allowed_origins`), so the value compared
    against the ``Origin`` header is always the spelling a browser sends.
    """
    scheme, netloc, parts = _split_http_url(value, what="origin")
    if parts.path not in ("", "/") or "?" in value or "#" in value:
        raise ValueError(
            f"origin {value.strip()!r} must be scheme://host[:port] only, "
            "with no path, query or fragment"
        )
    return f"{scheme}://{netloc}"


class NetworkConfig(BaseModel):
    """``[network]`` — how browsers reach this box, beyond the installer's view (#2382).

    The Origin gate in ``hal0.api.auth`` (REST calls and WebSocket
    upgrades alike) accepts the
    ``HAL0_ALLOWED_ORIGINS`` list the installer derives into the managed
    ``api.env`` block. That block is rewritten on every installer run, so an
    origin hand-added there was lost on the next re-run. These keys are the
    persisted, operator-owned additions: :func:`allowed_origins` merges the
    origin of ``public_url`` and every ``extra_origins`` entry on top of the
    env list at runtime. The installer never copies them into the managed
    block, so removing one here revokes it on the next request.

    Out of scope on purpose: the bind host, port and hostname stay in the
    managed ``api.env`` block, because the systemd unit and avahi read them
    before Python runs.

    ``extra="forbid"`` (P3-schema Part C leaf-table policy, same as
    ``[security]``): a typo'd key must fail loudly, not silently leave an
    origin out of the allowlist.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    public_url: str | None = Field(
        default=None,
        description=(
            "The URL users reach hal0 at through a reverse proxy, e.g. "
            "https://hal0.example.com. Must be an absolute http or https URL. "
            "Its origin (scheme://host[:port]) is added to the browser-origin "
            "allowlist on top of HAL0_ALLOWED_ORIGINS. Applies on the next "
            "request, no restart. Unset = no reverse proxy."
        ),
    )

    extra_origins: list[str] = Field(
        default_factory=list,
        description=(
            "Other browser origins allowed to make state-changing requests and "
            "WebSocket upgrades, on top of HAL0_ALLOWED_ORIGINS: a second proxy, "
            "a MagicDNS name, http://box.lan:8080. Each entry is "
            "scheme://host[:port] with http or https and no path, stored without "
            "a trailing slash and deduplicated. Applies on the next request, no "
            "restart."
        ),
    )

    @field_validator("public_url", mode="before")
    @classmethod
    def _check_public_url(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("public_url must be a string")
        if not value.strip():
            # An emptied Settings field means "unset", same as the key missing.
            return None
        scheme, netloc, parts = _split_http_url(value, what="public_url")
        if "?" in value or "#" in value:
            raise ValueError(f"public_url {value.strip()!r} must not carry a query or fragment")
        path = parts.path.rstrip("/")
        if not _URL_PATH.match(path):
            raise ValueError(
                f"public_url {value.strip()!r} has a path with characters other than "
                "letters, digits and . _ ~ / % -"
            )
        return f"{scheme}://{netloc}{path}"

    @field_validator("extra_origins", mode="before")
    @classmethod
    def _check_extra_origins(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raise ValueError("extra_origins must be a list of origins, not a single string")
        if not isinstance(value, list | tuple):
            raise ValueError("extra_origins must be a list of origins")
        out: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(f"extra_origins entry {item!r} must be a string")
            origin = normalise_origin(item)
            if origin not in out:
                out.append(origin)
        return out


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

    enabled: bool = Field(
        default=True,
        description="Hard kill switch for the WS /v1/realtime voice endpoint.",
    )
    sample_rate: int = Field(
        default=24000,
        ge=8000,
        le=48000,
        description=(
            "Fixed pcm16 sample rate (Hz). 24 kHz matches the demo client and "
            "kokoro's native output — changing this does not resample either "
            "direction, so a mismatched client will hear distorted audio."
        ),
    )
    default_model: str = Field(
        default="",
        description="Fallback chat model for a realtime session when neither stt_model nor tts_model names one.",
    )
    stt_model: str = Field(
        default="",
        description=(
            "Loaded slot the gateway calls over loopback for speech-to-text. "
            "Empty falls back to the session's chat model."
        ),
    )
    tts_model: str = Field(
        default="kokoro",
        description=("Loaded slot the gateway calls over loopback for text-to-speech."),
    )
    tts_voice: str = Field(
        default="",
        description="Voice name passed to the tts slot. Empty lets the slot's own default apply.",
    )
    vad_energy_threshold: float = Field(
        default=0.02,
        ge=0.0,
        le=1.0,
        description=(
            "Normalized RMS (0-1) above which incoming audio counts as "
            "speech for the zero-dependency energy-VAD turn detector."
        ),
    )
    vad_silence_ms: int = Field(
        default=500,
        ge=50,
        le=10000,
        description="Trailing silence (ms) that ends a voice turn.",
    )
    vad_min_speech_ms: int = Field(
        default=200,
        ge=0,
        le=10000,
        description=(
            "Minimum voiced-audio duration (ms) to count as speech rather "
            "than noise; shorter segments never fire a turn."
        ),
    )
    vad_window_ms: int = Field(
        default=20,
        ge=5,
        le=100,
        description="Analysis window size (ms) the energy-VAD detector evaluates at a time.",
    )
    frame_ms: int = Field(
        default=20,
        ge=5,
        le=200,
        description="Output audio frame size (ms) — the response.output_audio.delta granularity.",
    )
    approval_wait_s: float = Field(
        default=20.0,
        gt=0,
        le=300.0,
        description=(
            "How long a gated steward tool may leave the voice session "
            "silent before the assistant speaks a 'still waiting' notice "
            "and ends the turn, instead of blocking up to 300s."
        ),
    )
    max_buffer_seconds: float = Field(
        default=30.0,
        gt=0,
        le=600.0,
        description="Cap on buffered audio (seconds) held for a single realtime turn.",
    )


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
    updates: UpdatesConfig = Field(default_factory=UpdatesConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    activity: ActivityConfig = Field(default_factory=ActivityConfig)
    brain_chat: BrainChatConfig = Field(default_factory=BrainChatConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
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
    "CURRENT_SCHEMA_VERSION",
    "DEFAULT_DEVICE",
    "DEVICE_DEFAULT_PROFILES",
    "DRAFT_BUNDLE",
    "FAMILY_DEFAULTS",
    "HERMES_COMMAND_APPROVALS_MODES",
    "MTP_DRAFT_BUNDLE",
    "NGRAM_BUNDLE",
    "PROFILE_BENCH",
    "PROFILE_SCHEMA_VERSION_CURRENT",
    "REMOVED_SLOT_KEYS",
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
    "HermesAgentSettings",
    "HermesCommandApprovalsLiteral",
    "ImageGenConfig",
    "MCPServerConfig",
    "MemoryConfig",
    "MemoryEmbeddingConfig",
    "MemoryGraphConfig",
    "MetaConfig",
    "ModelConfig",
    "ModelsConfig",
    "NPUInfo",
    "NetworkConfig",
    "NpuConfig",
    "ProfileConfig",
    "ProfilesConfig",
    "ProviderEntry",
    "ProvidersConfig",
    "SecurityConfig",
    "ServerConfig",
    "SlotConfig",
    "SlotsConfig",
    "SpeculationMode",
    "TelemetryConfig",
    "ToolPolicy",
    "UpdatesConfig",
    "UpstreamEntry",
    "UpstreamsConfig",
    "family_flags",
    "lane_device",
    "map_backend_to_device",
    "model_family",
    "normalise_origin",
    "resolve_chat_template",
]
