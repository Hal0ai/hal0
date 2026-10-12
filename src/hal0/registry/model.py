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
            "route predating this field)."
        ),
    )


class ModelDefaults(BaseModel):
    """Facts about the model file that the launch path reads (ADR-0029).

    Only facts live here — every tune (context window, flags, reasoning,
    vision, MTP on/off) lives on the profile of the slot that runs the model.
    ``extra="forbid"``: a write carrying a tuning key is rejected, not
    silently dropped.
    """

    model_config = {"populate_by_name": True, "str_strip_whitespace": True, "extra": "forbid"}

    chat_template: str | None = Field(
        default=None,
        description=(
            "Chat template id from /api/chat-templates this file needs, or "
            "'auto'/None for the template embedded in the GGUF."
        ),
    )
    tokenizer_repo: str | None = Field(
        default=None,
        description=(
            "HF repo id a load-gen tool can resolve a tokenizer from for THIS "
            "model (#1773). The pull path defaults it from Model.hf_repo; an "
            "operator may override it. None = not resolvable."
        ),
    )
    mtp: bool | None = Field(
        default=None,
        description=(
            "Whether the file has MTP draft heads. None = the registry 'mtp' tag "
            "decides. Whether a slot drafts with them is its profile's `mtp` "
            "('auto' drafts when the heads are there and the runner supports it)."
        ),
    )
    jinja: bool | None = Field(
        default=None,
        description=(
            "False when this file's chat template does not work with --jinja; "
            "None/True = hal0 adds --jinja when the runner supports it (outside "
            "embedding/reranking mode)."
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
            "Facts about the model file the launch path reads (chat template, MTP heads, jinja). None = no facts recorded."
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
            "'context_length_detected' (bool, set by the pull path when it "
            "auto-stamped 'context_length' from the GGUF header/curated "
            "catalogue — absent means an operator set it explicitly, which "
            "outranks a later re-pull's own detection; see registry/pull.py "
            "_register_pulled), 'upstream_url' (str, dispatcher route hint)."
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

    provider: str | None = Field(
        default=None,
        description=(
            "Engine identity (a hal0.model_meta.RUNTIME_FAMILIES member). "
            "None means 'derive' — resolve from the stored backend tags via "
            "hal0.model_meta.derive_model_provider. Persists in the registry "
            "row's ``extra`` JSON blob (repository.py) — no schema migration, "
            "same fold as capability_flags/modalities_override."
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
