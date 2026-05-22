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

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

from hal0.config import paths

# ── Shared constants ───────────────────────────────────────────────────────────

# TIER1: surface-area for the backend whitelist. Typos like
# `backend = "vukan"` must raise at load time with the field path.
_VALID_BACKENDS = frozenset({"vulkan", "rocm", "flm", "moonshine", "kokoro", "cpu"})

# TIER1: valid provider names.  Maps to the Provider ABC implementations
# under hal0.providers.
_VALID_PROVIDERS = frozenset({"llama-server", "flm", "moonshine", "kokoro"})

# Slot port range.  8080 is the hal0 API; slots get 8081-8099.
_SLOT_PORT_MIN = 8081
_SLOT_PORT_MAX = 8099

# Schema version for migrations.  Bumped when a backwards-incompatible
# config-shape change lands.  See PLAN.md §5 Tier 3.
CURRENT_SCHEMA_VERSION = 1


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
        description="Backend type: 'vulkan' | 'rocm' | 'flm' | 'moonshine' | 'kokoro' | 'cpu'.",
    )
    provider: str = Field(
        default="llama-server",
        description="Provider name: 'llama-server' | 'flm' | 'moonshine' | 'kokoro'.",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this slot is started on hal0 startup.",
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
    probed_at: str = Field(
        default="",
        description="ISO-8601 UTC timestamp of the last probe run.",
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
            "Destination directory for HuggingFace pulls. Must be an absolute path. "
            "Tempfiles stage under <pull_root>/.tmp/ and finished downloads land at "
            "<pull_root>/<model_id>/<filename>. ComfyUI assets still route to "
            "<var_lib>/comfyui/models/<subdir>/. This directory is auto-included "
            "in the discovery scan so pulled files are immediately visible. "
            "Default tracks HAL0_HOME for dev installs."
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


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "DispatcherConfig",
    "GPUInfo",
    "Hal0Config",
    "HardwareInfo",
    "MetaConfig",
    "ModelConfig",
    "ModelsConfig",
    "NPUInfo",
    "ProviderEntry",
    "ProvidersConfig",
    "ServerConfig",
    "SlotConfig",
    "SlotsConfig",
    "TelemetryConfig",
    "UpstreamEntry",
    "UpstreamsConfig",
]
