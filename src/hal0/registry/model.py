"""Model — registry entry pydantic model.

The Model class is the typed representation of one row in the model
registry (stored as atomic TOML under /var/lib/hal0/registry/).

Port target: haloai lib/registry.py (adapted from the raw dict shape
to a pydantic v2 model).  See PLAN.md §3.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

# Capabilities that a model can advertise.
# Used by the Dispatcher and the slot config form's hardware-aware filtering.
# NOTE: revisit in Phase 1 — extend as providers surface new capabilities.
Capability = str  # e.g. "chat", "embed", "rerank", "vision", "asr", "tts"


class ModelDefaults(BaseModel):
    """Per-model default knobs surfaced as launcher defaults.

    All fields optional. ``extra_args`` is appended verbatim to the
    launcher arg list and is later merged with the slot's own
    ``[server].extra_args`` by :func:`hal0.launchers.flag_merge.merge_flags`.
    """

    model_config = {"populate_by_name": True, "str_strip_whitespace": True}

    context_size: int | None = Field(
        default=None,
        description="Default n_ctx the launcher should use when this model is bound.",
    )
    n_gpu_layers: int | None = Field(
        default=None,
        description="Default --n-gpu-layers; -1 = all, 0 = CPU only.",
    )
    rope_freq_base: float | None = Field(
        default=None,
        description="Default --rope-freq-base override.",
    )
    extra_args: str | None = Field(
        default=None,
        description="Freeform CLI flag string appended after merge with slot extra_args.",
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

    defaults: ModelDefaults | None = Field(
        default=None,
        description=(
            "Optional per-model launcher defaults. None means the slot config is used as-is."
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
