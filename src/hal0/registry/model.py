"""Model — registry entry pydantic model.

The Model class is the typed representation of one row in the model
registry (stored as atomic TOML under /var/lib/hal0/registry/).

Port target: haloai lib/registry.py (adapted from the raw dict shape
to a pydantic v2 model).  See PLAN.md §3.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from hal0.model_meta.modality import Modality, normalize_modalities

# Capabilities that a model can advertise.
# Used by the Dispatcher and the slot config form's hardware-aware filtering.
# NOTE: revisit in Phase 1 — extend as providers surface new capabilities.
Capability = str  # e.g. "chat", "embed", "rerank", "vision", "asr", "tts"


class ModelCapabilities(BaseModel):
    """Launch/runtime typed flags — §7.1d / ML-6.

    Distinct from :attr:`Model.capabilities` (the freeform modality-ish
    string list kept for TOML/JSON round-trip; see
    :attr:`Model.modalities` for the normalized reader) and from
    :attr:`Model.tags` (inert, freeform, drives nothing). This is the
    ONE typed-bool surface a runner-conditional toggle reads.

    Only ``tool_calling`` is wired up here. ``mtp``/``jinja`` are NOT
    added on this class yet — the §7.1a/b (ML-5) lane owns those two
    launch flags and lands them onto this same class (the SQLite
    ``model.mtp`` / ``model.jinja`` columns are already reserved for
    exactly this shape); adding them here ahead of that lane would be a
    double-add per the rework plan's "land ONCE" rule.
    """

    model_config = {"populate_by_name": True, "extra": "forbid"}

    tool_calling: bool | None = Field(
        default=None,
        description=(
            "Tri-state: True forces the omni-router tool-call gate on for "
            "this model, False forces it off, None means the routing "
            "decision falls back to the model's slot-config labels (the "
            "route predating this field, kept one release for TOML rows "
            "that have not been migrated yet — see "
            "registry/import_toml.py's labels fold)."
        ),
    )


class ModelDefaults(BaseModel):
    """Per-model default knobs surfaced as launcher defaults.

    All fields optional. ``extra_args`` is appended to the launcher arg
    list; cross-source duplicates (profile flags, slot ``[server].extra_args``)
    are collapsed last-wins by :func:`hal0.slots.argv.normalize_argv` at launch.
    """

    model_config = {"populate_by_name": True, "str_strip_whitespace": True}

    context_size: int | None = Field(
        default=None,
        description="Default n_ctx the launcher should use when this model is bound.",
    )
    rope_freq_base: float | None = Field(
        default=None,
        description=(
            "DEPRECATED: parsed and persisted but never emitted by the "
            "container launch path — put --rope-freq-base in extra_args "
            "or the profile flags instead."
        ),
    )
    extra_args: str | None = Field(
        default=None,
        description="Freeform CLI flag string appended after merge with slot extra_args.",
    )
    chat_template: str | None = Field(
        default=None,
        description="Chat template id from /api/chat-templates, or 'auto'/None for the GGUF-embedded template.",
    )
    profile: str | None = Field(
        default=None,
        description=(
            "Preferred runtime profile name (from the profiles catalog) this "
            "model wants loaded with it. Applied to a slot on create and on "
            "every model swap when it is compatible with the slot's device; the "
            "device-default profile is used as the fallback. None = no "
            "preference (slot keeps the device default)."
        ),
    )
    mtp: bool | None = Field(
        default=None,
        description=(
            "§7.1a / ML-5: tri-state MTP speculative-decoding capability. "
            "True/False is an explicit, unconditional curator override — it "
            "wins in EITHER direction over the registry 'mtp' tag (see "
            "hal0.model_meta.model_is_mtp_eligible / "
            "providers.container._effective_mtp). None = no explicit "
            "opinion; eligibility falls back to the registry 'mtp' tag, "
            "auto-gated by whether the launching runner supports MTP "
            "drafting (hal0.runners.RunnerSupports.mtp). Replaces the old "
            "filename/GGUF-name 'MTP' marker sniff, which is removed."
        ),
    )
    jinja: bool | None = Field(
        default=None,
        description=(
            "§7.1a / ML-5: tri-state --jinja capability. False explicitly "
            "suppresses --jinja (there is no --no-jinja negation flag, so "
            "this must be evaluated BEFORE argv assembly, not stripped "
            "after). None/True both mean 'use --jinja' when the launching "
            "runner supports it (hal0.runners.RunnerSupports.jinja is True "
            "for every current llama-server runner) — see "
            "providers.container._resolve_llama_scalars."
        ),
    )
    enable_thinking: bool | None = Field(
        default=None,
        description=(
            "spec-hw-slot-ownership §1: tri-state reasoning default — the MODEL "
            "is the single owner (a slot-level ``enable_thinking`` override no "
            "longer exists; SlotConfig rejects the key). True → requests routed "
            "to a slot bound to this model default to thinking ON; False → OFF; "
            "None → global suppression. Always overridable per request via the "
            "top-level ``enable_thinking`` / ``chat_template_kwargs`` — see "
            "normalize/thinking.py and api.routes.v1._slot_thinking_default."
        ),
    )
    vision: bool | None = Field(
        default=None,
        description=(
            "spec-hw-slot-ownership §1: tri-state vision-projector override — "
            "the MODEL is the single owner now (the former per-slot ``vision`` "
            "toggle (#901) is gone; SlotConfig rejects the key). None (default) "
            "= AUTO: the mmproj sidecar loads whenever the model carries one "
            "(registry ``Model.mmproj`` presence). False force-suppresses it "
            "(no --mmproj) even when the model ships a sidecar, e.g. to save "
            "the ~0.9 GB resident projector on a memory-tight host. True is an "
            "explicit no-op affirmation (mmproj already loads under AUTO when "
            "present) kept for symmetry with mtp/jinja/enable_thinking. See "
            "providers.container._resolve_llama_scalars."
        ),
    )


class Model(BaseModel):
    """A model entry in the hal0 registry.

    All fields are optional at construction (to allow partial updates via
    ModelRegistry.update()), except for `id` and `path` which are always
    required.

    Schema is intentionally flat: the registry TOML uses one file per model
    keyed by model id.  Nested structures are avoided so human editing remains
    practical.
    """

    model_config = {"populate_by_name": True, "str_strip_whitespace": True}

    id: str = Field(..., description="Unique model identifier, e.g. 'qwen3-4b-q4_k_m'.")

    name: str = Field(
        default="",
        description="Human-readable display name, e.g. 'Qwen3 4B (Q4_K_M)'.",
    )

    path: str = Field(
        ...,
        description=(
            "Absolute path to the model file or directory on this host.  "
            "May be under /var/lib/hal0/models/ or a symlink to /mnt/ai-models/."
        ),
    )

    size_bytes: int = Field(
        default=0,
        description="Total size of model files in bytes.  0 means unknown.",
    )

    quant: str | None = Field(
        default=None,
        description=(
            "Quantisation label, e.g. 'Q4_K_M', 'IQ2_XS', 'F16'. Derived at "
            "registration from the GGUF header (general.file_type) with a "
            "filename-token fallback (registry/detect.py, WS-13). None = "
            "unknown / not applicable; serialisation lazily backfills from "
            "the filename so pre-existing registries surface it without "
            "re-registration."
        ),
    )

    license: str = Field(
        default="unknown",
        description="SPDX license identifier or short name, e.g. 'Apache-2.0', 'Llama-3'.",
    )

    capabilities: list[Capability] = Field(
        default_factory=list,
        description=(
            "List of capability strings this model supports.  "
            "Valid values: 'chat', 'embed', 'rerank', 'vision', 'asr', 'tts'."
        ),
    )

    hf_repo: str = Field(
        default="",
        description="HuggingFace repo id, e.g. 'Qwen/Qwen3-4B-GGUF'. Empty if not from HF.",
    )

    hf_filename: str = Field(
        default="",
        description="Filename within the HF repo, e.g. 'qwen3-4b-q4_k_m.gguf'.",
    )

    tags: list[str] = Field(
        default_factory=list,
        description="Freeform tags, e.g. ['curated', 'vision'].",
    )

    backends: list[str] = Field(
        default_factory=list,
        description=(
            "Slot backend names this model can run under. "
            "GGUF → ['vulkan','rocm','cuda','cpu']; moonshine → ['moonshine']; "
            "kokoro → ['kokoro']. Empty = unknown / not yet detected."
        ),
    )

    mmproj: str | None = Field(
        default=None,
        description=(
            "Absolute path to a multimodal projector (mmproj) GGUF sidecar that "
            "sits beside this model, or None. Surfaced verbatim to the "
            "llama-server provider as --mmproj to enable vision; the sidecar is "
            "never registered as a standalone routable model."
        ),
    )

    defaults: ModelDefaults | None = Field(
        default=None,
        description=(
            "Optional per-model launcher defaults. None means the slot config is used as-is."
        ),
    )

    default: bool = Field(
        default=False,
        description=(
            "Per-type default marker: True marks this model as THE default "
            "model for its dispatcher type (llm/embedding/reranking/… — the "
            "axis derived from id+capabilities by "
            "hal0.services.models_service.dispatch_type). At most ONE model per "
            "type may carry this; the single-holder invariant is enforced "
            "server-side by "
            "hal0.services.models_service.set_model_type_default (the one "
            "chokepoint that promotes/demotes), NOT by this field's validator. "
            "Distinct from the SLOT-level `default` flag (SC-4, "
            "config_write.check_default_uniqueness): that picks the default "
            "SLOT of a type for routing; this picks the default MODEL. Persists "
            "in the registry row's ``extra`` JSON blob (repository "
            "_DEFAULT_EXTRA_KEY) — no schema migration, same fold as "
            "capability_flags/modalities_override."
        ),
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Provider-specific or user-defined extra metadata. "
            "Reserved keys: 'context_length' (int, GGUF arch max), "
            "'upstream_url' (str, dispatcher route hint)."
        ),
    )

    architecture: str | None = Field(
        default=None,
        description=(
            "Model architecture id (e.g. 'llama', 'qwen2', 'gemma3', "
            "'gpt-oss', 'qwen3next', 'mamba'). Replaces the dead 'moe' "
            "curated tag — drives FAMILY_DEFAULTS keying + dense/moe "
            "context sizing (hardware/recommend.is_moe)."
        ),
    )

    modalities_override: list[Modality] | None = Field(
        default=None,
        description=(
            "Operator escape hatch: when set, unions with the derived "
            "modality list (see hal0.model_meta.modality.derive_modalities) "
            "instead of replacing it. Rare — for hand-curated workflows the "
            "detector can't infer (e.g. a ComfyUI graph that also does "
            "video)."
        ),
    )

    capability_flags: ModelCapabilities = Field(
        default_factory=ModelCapabilities,
        description=(
            "Typed launch/runtime bools (currently just tool_calling). See "
            "ModelCapabilities docstring for why this is a separate field "
            "from the freeform 'capabilities' modality list."
        ),
    )

    @property
    def modalities(self) -> list[Modality]:
        """``self.capabilities`` folded through :func:`normalize_modality`.

        Read-only convenience accessor — ``capabilities`` stays the field
        name persisted to TOML/JSON (renaming it is a wide-blast-radius
        change deferred to a follow-up; see spec-taxonomy-7-1d PART 6.2).
        New code should read modalities through here rather than adding a
        fresh direct read of the raw ``capabilities`` strings, so alias
        folding (stt→asr, embedding→embed, …) happens in exactly one
        place.
        """
        return normalize_modalities(self.capabilities)

    @field_validator("id")
    @classmethod
    def id_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("model id must not be empty")
        return v

    @field_validator("path")
    @classmethod
    def path_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("model path must not be empty")
        return v


# ── Namespace derivation ─────────────────────────────────────────────────────
#
# The dashboard surfaces a two-bucket split — "blessed" (curated /
# pre-baked artifacts laid out under
# ``/var/lib/hal0/models/<recipe>/<capability>/``) vs "pulled" (anything
# we downloaded into the registry's pull tree or that the operator
# hand-registered). The rule is path-shape only — see issue #220 for
# the locked decision — so a single source of truth for the derivation
# keeps every consumer in sync.

_BLESSED_PREFIX = "/var/lib/hal0/models/"


def _derive_ns(model: Model) -> str:
    """Return ``"blessed"`` if ``model.path`` sits under a recipe/capability
    directory inside the blessed model root, else ``"pulled"``.

    Rule (issue #220 — do not relitigate): a path is blessed iff it
    begins with ``/var/lib/hal0/models/<recipe>/<capability>/`` — i.e.
    after the blessed root there are at least two more directory
    components before the file. The pull tree layout
    (``/var/lib/hal0/models/<id>/<file>``) only has one component after
    the root and is therefore "pulled".
    """
    path = (model.path or "").strip()
    if not path or not path.startswith(_BLESSED_PREFIX):
        return "pulled"
    tail = path[len(_BLESSED_PREFIX) :]
    # Need: <recipe>/<capability>/<rest>. That's 2 separators with
    # non-empty leading segments.
    parts = tail.split("/")
    if len(parts) < 3:
        return "pulled"
    recipe, capability = parts[0], parts[1]
    if not recipe or not capability:
        return "pulled"
    return "blessed"
