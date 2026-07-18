"""model_meta — the one home for model classification + device→backend resolution.

Issue #695. Before this module the same logic was copy-pasted across
five sites (``routes/models.py``, ``routes/slots.py``,
``capabilities/orchestrator.py``, ``omni_router/filter.py``,
``slots/manager.py``); a classification-rule change meant hunting every
copy. They all import from here now.

The module is **stateless** — no classes, no construction, just
importable functions and constants. ``classify`` and
``device_to_backend`` are pure; ``is_resolvable`` takes the registry
explicitly as an argument so the module stays importable everywhere
without threading a handle through.

Canonical vocabulary table (issue #695 follow-up — every identity
vocabulary the backend speaks, defined ONCE here; ``config.schema``
re-exports for compatibility, it no longer defines its own copies):

========================  ====================================================
Vocabulary                Purpose / members
========================  ====================================================
device                    v0.2 hardware-preference enum stored in slot TOMLs
                          and the capabilities catalog:
                          ``gpu-rocm | gpu-vulkan | gpu-cuda | cpu | npu``
                          (:data:`CANONICAL_DEVICES` carries per-device
                          metadata; :data:`VALID_DEVICES` is the id set).
legacy backend            DEPRECATED v0.1 ``SlotConfig.backend`` enum, kept
                          one release for TOML round-trip (plus ``cuda`` so
                          the gpu-cuda device write-back round-trips):
                          ``rocm | vulkan | cuda | cpu | flm | moonshine |
                          kokoro``
                          (:data:`LEGACY_BACKENDS`). It overloaded hardware
                          intent with provider identity — ``moonshine`` /
                          ``kokoro`` were CPU runtimes, hence they map to
                          ``cpu`` in :data:`BACKEND_TO_DEVICE`.
selectable backend        The runtime-switch tokens POST /api/slots/{name}/
                          backend accepts: ``rocm | vulkan | cpu | auto``
                          (:data:`SELECTABLE_BACKENDS`). flm/npu are excluded
                          — switching to NPU is a recipe change, not a
                          backend flip.
device_class              Coarse profile bucket driving container device
                          passthrough + card display:
                          ``gpu | cpu | npu | img`` (:data:`DEVICE_CLASSES`).
runtime family            Which server process a profile launches:
                          ``llama-server | flm | kokoro | qwen3tts | comfyui``
                          (:data:`RUNTIME_FAMILIES`; mirrors
                          ``hal0.profiles.RuntimeFamily``).
slot type                 Dispatcher slot vocabulary:
                          ``llm | embedding | reranking | transcription |
                          tts | image`` (:data:`SLOT_TYPES`; the source for
                          ``slots.manager._VALID_SLOT_TYPES`` and mirrored by
                          ``hal0.profiles.SlotType``).
model capability          Canonical registry ``model.capabilities`` spellings:
                          ``chat | vision | embed | rerank | asr | tts |
                          image | video`` (:data:`MODEL_CAPABILITIES`, with
                          the tolerated synonyms in
                          :data:`CAPABILITY_ALIASES`).
model backend             Valid ``model.backends`` values in the registry:
                          GGUF seeds vulkan/rocm/cuda/cpu (registry/detect
                          already lists cuda as *compatible* — slot configs
                          can't select it yet), plus the dedicated providers
                          flm/moonshine/kokoro/comfyui
                          (:data:`MODEL_BACKENDS`).
========================  ====================================================

Unknown-value policy — ONE documented rule per translation direction
(previously three sites disagreed silently):

* legacy backend → device (:func:`map_backend_to_device`, and
  :func:`canonical_device` which routes through it): unknown → **warn +
  ``"cpu"``**. Safety default — the runtime must load *somewhere*, and CPU
  always exists; crashing at config load over a typo would brick the API.
* device → runtime pair (:func:`device_to_backend`): unknown → **warn +
  ``(None, None)``** ("no opinion"). Callers already handle ``None`` by
  applying their own defaults, so inventing a backend tag here would
  override deliberate downstream fallbacks.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


# ── canonical device taxonomy ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DeviceMeta:
    """Everything the backend (and /api/meta/enums) knows about one device."""

    id: str
    label: str
    device_class: str
    default_profile: str
    legacy_backend: str
    recommended: bool
    description: str


#: The canonical devices (gpu-cuda added by the GPU
#: generalization wave), with per-device metadata.
#: Ordering is presentation order for pickers: recommended first, then the
#: fallbacks (Vulkan, then the experimental CUDA path), then the non-GPU
#: devices. Per CONTEXT.md (spike data): ``gpu-rocm`` is the recommended
#: default on Strix Halo; ``gpu-vulkan`` is the slower fallback.
CANONICAL_DEVICES: tuple[DeviceMeta, ...] = (
    DeviceMeta(
        id="gpu-rocm",
        label="GPU (ROCm)",
        device_class="gpu",
        default_profile="rocm",
        legacy_backend="rocm",
        recommended=True,
        description="Best throughput on Strix Halo — the recommended default.",
    ),
    DeviceMeta(
        id="gpu-vulkan",
        label="GPU (Vulkan)",
        device_class="gpu",
        default_profile="vulkan",
        legacy_backend="vulkan",
        recommended=False,
        description="Runs anywhere Mesa Vulkan does; slower than ROCm on Strix Halo — fallback.",
    ),
    DeviceMeta(
        id="gpu-cuda",
        label="GPU (CUDA)",
        device_class="gpu",
        default_profile="cuda",
        legacy_backend="cuda",
        recommended=False,
        description="NVIDIA GPUs via llama.cpp CUDA — experimental on hal0.",
    ),
    DeviceMeta(
        id="cpu",
        label="CPU",
        device_class="cpu",
        default_profile="cpu-llm",
        legacy_backend="cpu",
        recommended=False,
        description="CPU-only inference — always available, slow but correct.",
    ),
    DeviceMeta(
        id="npu",
        label="NPU (FLM)",
        device_class="npu",
        default_profile="flm",
        legacy_backend="flm",
        recommended=False,
        description="AMD XDNA NPU via FastFlowLM — chat/embed/ASR trio on one process.",
    ),
)

#: Canonical device id set — the single source ``config.schema._VALID_DEVICES``
#: now aliases.
VALID_DEVICES: frozenset[str] = frozenset(d.id for d in CANONICAL_DEVICES)

#: Default ``device`` for fresh installs (see the gpu-rocm entry above).
DEFAULT_DEVICE: str = "gpu-rocm"

#: DEPRECATED v0.1 ``SlotConfig.backend`` enum (whitelist source for
#: ``config.schema._VALID_BACKENDS``). Tuple so JSON surfaces are ordered.
#: ``cuda`` never existed in v0.1 but is accepted here so the gpu-cuda
#: device round-trips through the one-release ``backend`` write-back the
#: same way every other device does.
LEGACY_BACKENDS: tuple[str, ...] = ("rocm", "vulkan", "cuda", "cpu", "flm", "moonshine", "kokoro")

#: Tokens POST /api/slots/{name}/backend accepts (``auto`` clears the device
#: so the load path falls back to its default). flm/npu are deliberately not
#: selectable — NPU needs a recipe/profile switch, not a backend flip.
SELECTABLE_BACKENDS: tuple[str, ...] = ("rocm", "vulkan", "cpu", "auto")

#: Coarse profile device buckets (``ProfileConfig.device_class``).
DEVICE_CLASSES: tuple[str, ...] = ("gpu", "cpu", "npu", "img")

#: Profile runtime families. MUST stay in sync with the
#: ``hal0.profiles.RuntimeFamily`` Literal (a Literal can't be built from a
#: runtime tuple; tests/model_meta assert the two match).
RUNTIME_FAMILIES: tuple[str, ...] = ("llama-server", "flm", "kokoro", "qwen3tts", "comfyui")

#: Legacy ``backend`` token → canonical ``device``. Used by the SlotConfig
#: and CapabilitySelection promote-then-drop shims (auto-promote a legacy
#: on-disk ``backend`` key, then pop it, on load), and the capabilities
#: v1→v2 migration. Keep aligned with the canonical device enum above.
#: moonshine/kokoro map to ``cpu`` because those toolboxes were always CPU
#: runtimes — the legacy enum overloaded ``backend`` with provider identity.
#: Canonical device ids are included idempotently so a value already in the
#: new namespace round-trips through this map as an identity.
BACKEND_TO_DEVICE: dict[str, str] = {
    **{d.legacy_backend: d.id for d in CANONICAL_DEVICES},
    "moonshine": "cpu",
    "kokoro": "cpu",
    **{d.id: d.id for d in CANONICAL_DEVICES},
}

#: Device → seed profile that best represents it (create-modal preselect,
#: legacy-slot migration defaults, stack apply's fresh-slot creation, and the
#: installer's primary-slot recommendation). ``cpu`` maps to ``cpu-llm`` —
#: a fresh slot with ``profile=""`` fails to load ("profile '' not found"),
#: so an empty default is never correct here.
DEVICE_TO_DEFAULT_PROFILE: dict[str, str] = {d.id: d.default_profile for d in CANONICAL_DEVICES}


def map_backend_to_device(backend: str | None) -> str:
    """Map a legacy ``backend`` value to the v0.2 ``device`` enum.

    Unknown values (e.g. an operator hand-edited a slot TOML with a
    bespoke backend tag) fall back to ``cpu`` so the runtime has a safe
    default rather than crashing at load (module unknown-value policy,
    direction 1). A warning is logged so the operator notices on the
    next ``hal0-api`` boot.

    Empty / None input is treated as "no opinion" and returns the
    package-level default ``DEFAULT_DEVICE``.

    Historically ``hal0.config.schema.map_backend_to_device`` — schema
    still re-exports it; the log event name is kept for grep continuity.
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


# ── canonical model vocabulary ───────────────────────────────────────────────

#: Dispatcher slot ``type`` vocabulary (plan §4.1). Single source for
#: ``slots.manager._VALID_SLOT_TYPES``; mirrored by ``hal0.profiles.SlotType``
#: (Literal — kept in sync by tests/model_meta).
SLOT_TYPES: tuple[str, ...] = ("llm", "embedding", "reranking", "transcription", "tts", "image")

#: Canonical ``model.capabilities`` spellings the registry stores:
#: registry/model.py documents chat/embed/rerank/vision/asr/tts;
#: registry/detect.py additionally emits ``image`` (ComfyUI tree) and the
#: shared filename table (:func:`capability_from_filename`) can yield
#: ``video`` via registry/discover (#940 diffusion hardening).
MODEL_CAPABILITIES: tuple[str, ...] = (
    "chat",
    "vision",
    "embed",
    "rerank",
    "asr",
    "tts",
    "image",
    "video",
)

#: Tolerated capability synonyms → canonical spelling. Matches what the code
#: actually accepts today: ``classify``'s ``_CAPABILITY_TO_TYPE`` folds
#: stt→asr-bucket and img→image; the layout migration
#: (cli/migrate_commands._CAPABILITY_TO_LEAF_CAP) additionally tolerates the
#: slot-type-flavoured embedding/embeddings/reranking/transcription spellings.
CAPABILITY_ALIASES: dict[str, str] = {
    "embedding": "embed",
    "embeddings": "embed",
    "reranking": "rerank",
    "transcription": "asr",
    "stt": "asr",
    "img": "image",
}

#: Valid ``model.backends`` values in the registry. The GGUF compatibility
#: seed is registry/detect._GGUF_BACKENDS (vulkan/rocm/cuda/cpu — ``cuda`` is
#: already listed there as *compatible*, though no slot config can select it
#: until Wave 3 lands CUDA support); the rest are the dedicated providers
#: detect assigns by filename/tree (moonshine, kokoro, comfyui) plus flm.
MODEL_BACKENDS: tuple[str, ...] = (
    "vulkan",
    "rocm",
    "cuda",
    "cpu",
    "flm",
    "moonshine",
    "kokoro",
    "comfyui",
)

#: Curated ``model.tags`` vocabulary served on /api/meta/enums (WS-13) so the
#: dashboard's tag chips stop hardcoding their own copies. Ordering is
#: presentation order: the behaviour-driving type tags first (mirrors the UI
#: edit pane's toggles — ui/src/dash/model-types.js MODEL_TYPE_TAGS), then
#: provenance, then the descriptive tags the curated catalogue seeds
#: (registry/curated.py). Tags remain freeform on ``Model.tags`` — this tuple
#: is the *curated* superset, not a validation whitelist.
#: tests/model_meta/test_curated_model_tags.py pins this against the curated
#: catalogue so a new seed tag can't silently drift out of the enums payload.
CURATED_MODEL_TAGS: tuple[str, ...] = (
    # behaviour-driving type tags (routing / slot-feature gates)
    "mtp",
    "moe",
    "tool-calling",
    "reasoning",
    "coder",
    "vision",
    # provenance
    "curated",
    "user-added",
    # curated-catalogue descriptive tags
    "chat",
    "code",
    "coding",
    "frontier",
    "long-context",
    "multilingual",
    "default",
    "rocmfp4",
    "balanced",
    "tiny",
    "lite-bundle",
    "smoke-test",
    "fast",
    "low-vram",
    "mit",
    "embed",
    "light",
    "medium",
    "rerank",
    "image",
    "sdxl",
    "sd-1.5",
    "lora",
    "upscale",
    "esrgan",
    "research-only",
    "stt",
    "transcription",
    "tts",
    "edit",
)


# ── classification ───────────────────────────────────────────────────────────

# Capability → coarse modality bucket the dashboard's Models view (and the
# endpoints widget W7) counts by. ``chat`` covers text/vision LLMs; the
# rest map a model's primary non-chat function so embed/rerank/voice/image
# models are counted instead of being lumped under "chat" or omitted.
_CAPABILITY_TO_TYPE: dict[str, str] = {
    "chat": "chat",
    "vision": "chat",
    "embed": "embed",
    "rerank": "rerank",
    "asr": "stt",
    "stt": "stt",
    "tts": "tts",
    "image": "img",
    "img": "img",
}

# Precedence when a model advertises several capabilities: a dedicated
# embedder that also lists "chat" should still classify as embed. chat is
# the lowest-priority fallback so genuinely non-chat models surface.
_TYPE_PRIORITY: tuple[str, ...] = ("rerank", "embed", "stt", "tts", "img", "chat")


def classify(model_id: str = "", capabilities: Any = None) -> str:
    """Return the primary modality bucket for a model.

    Reads the model's ``capabilities`` list (chat/embed/rerank/asr/tts/
    vision/image). Falls back to filename heuristics on the id so
    upstream-only rows (which carry no capabilities) still classify.
    Defaults to ``"chat"`` when nothing else matches.
    """
    found: set[str] = set()
    if isinstance(capabilities, (list, tuple)):
        for cap in capabilities:
            t = _CAPABILITY_TO_TYPE.get(str(cap).strip().lower())
            if t:
                found.add(t)
    if not found and model_id:
        mid = model_id.lower()
        if "rerank" in mid:
            found.add("rerank")
        elif "embed" in mid or "bge" in mid or "nomic" in mid:
            found.add("embed")
        elif "whisper" in mid or "moonshine" in mid or "-stt" in mid or "asr" in mid:
            found.add("stt")
        elif "tts" in mid or "kokoro" in mid or "vibevoice" in mid or "-voice" in mid:
            found.add("tts")
        elif "flux" in mid or "sdxl" in mid or "stable-diffusion" in mid or "-img" in mid:
            found.add("img")
    for t in _TYPE_PRIORITY:
        if t in found:
            return t
    return "chat"


# ── filename → capability token (MR-3) ───────────────────────────────────────
#
# ONE token table feeding both filename heuristics that used to drift:
# ``registry/discover._guess_capability`` (auto-scan) and
# ``registry/detect._filename_capability`` (single-file detect). Before this
# the same "guess capability from a filename" logic lived in three places with
# three different token sets, so a reranker gguf auto-scanned as ``chat`` in
# discover while ``classify`` already knew it was ``rerank``.
#
# This returns a CAPABILITY token — not a coarse modality bucket like
# :func:`classify` — or ``None`` when nothing matches. Each caller applies its
# own default for the ``None`` case (discover defaults to ``chat``; detect
# narrows to embed/asr/tts). Note: unifying on this shared table intentionally
# BROADENS discover's embed vocabulary — bge/gte-/e5-/jina-embed filenames that
# discover previously mislabeled as ``chat`` now correctly resolve to ``embed``
# (the MR-3 fix, of a piece with the reranker fix). See the token-table comment.
#
# Ordering is load-bearing: ``rerank`` is tested BEFORE ``embed`` so a
# "bge-reranker" filename (which also contains the embed token "bge")
# classifies as ``rerank`` rather than ``embed``.

_RERANK_NAME_TOKENS: tuple[str, ...] = ("rerank",)
# Embedder families. bge/gte-/jina-embed/nomic/e5- are INTENTIONALLY included
# here as part of the MR-3 unification: discover previously only knew
# ("embed","nomic") and mislabeled bge/gte/e5 embedders as ``chat`` (keeping
# them out of the correct embed slot). Tokens are anchored to avoid loose
# 2-char false positives — ``e5-`` (not bare "e5", which would match any chat
# filename containing "e5") and ``gte-`` (not bare "gte"). ``rerank`` is tested
# first (below) so "bge-reranker" resolves to rerank, not embed.
_EMBED_NAME_TOKENS: tuple[str, ...] = ("embed", "bge", "e5-", "nomic", "gte-", "jina-embed")
_VISION_NAME_TOKENS: tuple[str, ...] = ("vl", "vision", "vit")
_ASR_NAME_TOKENS: tuple[str, ...] = ("whisper", "moonshine", "-asr", "_asr", "asr", "stt")
# ``vibevoice`` is explicit; bare "voice" is deliberately excluded (too loose —
# would catch a chat model named "...voice..."). xtts-voice still resolves via
# the bare "tts" token.
_TTS_NAME_TOKENS: tuple[str, ...] = ("tts", "kokoro", "vibevoice")
# Clearly-diffusion media families (#940 hardening): classifying these as
# video/image instead of defaulting to ``chat`` keeps a 25GB video-diffusion
# gguf out of the chat fallback pool ``SlotManager._fallback_local_model``
# draws from. Conservative: only well-known families, matched as substrings.
_VIDEO_NAME_TOKENS: tuple[str, ...] = (
    "ltx",
    "wan",
    "hunyuan-video",
    "hunyuanvideo",
    "cogvideo",
    "svd",
)
_IMAGE_NAME_TOKENS: tuple[str, ...] = ("sdxl", "flux", "stable-diffusion", "stable_diffusion")


def capability_from_filename(name: str) -> str | None:
    """Best-effort capability token inferred from a model filename.

    Returns one of ``rerank | embed | vision | asr | tts | video | image``,
    or ``None`` when no token matches. Matching is a case-insensitive
    substring test against a single shared token table; ``rerank`` is checked
    before ``embed`` so cross-encoder rerankers (whose names carry an embed
    token such as "bge") don't misclassify as embedders.

    Callers apply their own default for the ``None`` case so their existing
    output vocabularies are preserved (see the module note above).
    """
    lower = name.lower()
    if any(tok in lower for tok in _RERANK_NAME_TOKENS):
        return "rerank"
    if any(tok in lower for tok in _EMBED_NAME_TOKENS):
        return "embed"
    if any(tok in lower for tok in _VISION_NAME_TOKENS):
        return "vision"
    if any(tok in lower for tok in _ASR_NAME_TOKENS):
        return "asr"
    if any(tok in lower for tok in _TTS_NAME_TOKENS):
        return "tts"
    if any(tok in lower for tok in _VIDEO_NAME_TOKENS):
        return "video"
    if any(tok in lower for tok in _IMAGE_NAME_TOKENS):
        return "image"
    return None


# ── device → recipe/backend mapping ──────────────────────────────────────────
#
# By design, the four-way mapping is locked; the result feeds
# container profile/argv derivation. ``gpu-*`` slots load through
# llama.cpp with an explicit backend flag; ``cpu`` is the same recipe
# with CPU-only inference; ``npu`` uses the FLM recipe (NPU FastFlowLM)
# and does not take a llamacpp_backend (FLM is its own backend).
#
# Returned tuple shape: ``(recipe, llamacpp_backend)``. ``recipe=None``
# means "default llama.cpp recipe"; ``llamacpp_backend`` is one of
# rocm | vulkan | cpu.


def device_to_backend(device: str | None) -> tuple[str | None, str | None]:
    """Map hal0's ``device`` enum onto the recipe+backend pair.

    Args:
        device: One of ``gpu-rocm`` | ``gpu-vulkan`` | ``gpu-cuda`` | ``cpu``
                | ``npu``. Empty / unknown values fall back to
                ``(None, None)`` ("no opinion" — callers apply their own
                defaults).

    Returns:
        ``(recipe, llamacpp_backend)``. Either may be ``None`` to mean
        "no opinion". The two are mutually exclusive in practice — NPU
        uses ``recipe="flm"`` with no llamacpp_backend; everything else
        uses ``recipe=None`` with a concrete llamacpp_backend that feeds
        container profile/argv derivation.
    """
    if not device:
        return (None, None)
    d = device.strip().lower()
    if d == "gpu-rocm":
        return (None, "rocm")
    if d == "gpu-vulkan":
        return (None, "vulkan")
    if d == "gpu-cuda":
        return (None, "cuda")
    if d == "cpu":
        return (None, "cpu")
    if d == "npu":
        # FLM recipe; ``llamacpp_backend`` is meaningless here — the NPU
        # path is served by the host FLM process.
        return ("flm", None)
    log.warning(
        "model_meta.unknown_device",
        extra={"device": device},
    )
    return (None, None)


# ── device-namespace normalisation (ex-orchestrator helpers) ─────────────────


def canonical_device(value: str) -> str:
    """Normalise a backend/device string to the canonical ``device`` enum.

    Both the slot TOML and the capabilities catalog
    speak the same enum (``gpu-rocm | gpu-vulkan | cpu | npu``), so this
    is a near-identity. It still tolerates a legacy ``backend``-style
    input (``vulkan|rocm|flm|moonshine|kokoro``) for forward
    compatibility with hand-edited slot TOMLs by routing through
    :func:`map_backend_to_device` (so a genuinely unknown token warns and
    falls back to ``"cpu"`` — unknown-value policy, direction 1).

    Empty input means "no opinion" and returns ``""``.
    """
    if not value:
        return ""
    if value in VALID_DEVICES:
        return value
    return map_backend_to_device(value)


# ── resolvability ────────────────────────────────────────────────────────────


def is_resolvable(model_id: str, registry: Any) -> bool:
    """True if ``model_id`` can actually be loaded onto a slot.

    The slot-apply guard used to require ``registry.has(model_id)``, but FLM
    models are FLM-owned tags and are never in hal0's registry (see the
    2026-06-07 shape audits) — yet they load fine via npu.toml's config path.
    So gate on *provider-resolvability*: registry-resident OR an installed FLM
    model.

    ``registry`` is passed explicitly (anything with a ``has(model_id)``
    method, or ``None``) so this module never grows registry state.
    """
    if registry is not None and registry.has(model_id):
        return True
    from hal0.providers.flm import is_installed_flm_id

    return is_installed_flm_id(model_id)


# ── MTP eligibility ───────────────────────────────────────────────────────────

#: Matches an ``MTP`` marker delimited by a separator (or string edge) so it
#: fires on ``…-MTP-…`` / ``…_mtp`` / ``….MTP`` filenames but not on an
#: incidental ``mtp`` inside a longer word.
_MTP_NAME_RE = re.compile(r"(?:^|[-_. ])mtp(?:[-_. ]|$)", re.IGNORECASE)


def model_is_mtp_eligible(model_info: Mapping[str, Any]) -> bool:
    """True when a model ships MTP / NextN speculative-decoding heads.

    Gates **auto** MTP (``SlotConfig.mtp is None``): an MTP-opting profile only
    speculates for an eligible model, so a non-MTP model on an MTP profile no
    longer launches with dead ``--spec-draft-*`` flags — and an explicit slot
    ``mtp=true`` still forces it regardless.  Eligibility is the registry
    ``mtp`` tag, or (for uncurated local pulls that carry no tags) an ``MTP``
    marker in the model id / GGUF name.  ``model_info`` is the registry
    ``model_dump`` dict the container provider already resolves per launch.
    """
    tags = model_info.get("tags") or ()
    if isinstance(tags, (list, tuple, set)) and any(
        str(tag).strip().lower() == "mtp" for tag in tags
    ):
        return True
    name = str(model_info.get("_model_key") or model_info.get("path") or "")
    return bool(_MTP_NAME_RE.search(name))


# ── label extraction ─────────────────────────────────────────────────────────


def labels_of(cfg: dict[str, Any]) -> set[str]:
    """Pull the ``model.labels`` list out of a slot config dict.

    Single source for both :func:`SlotManager.route_for_request` and the
    omni-router tool filter (``omni_router/filter.py``) so the filter's
    decision always matches what ``route_for_request`` will pick — they
    used to be two hand-synced copies.
    """
    model = cfg.get("model") or {}
    if isinstance(model, dict):
        raw = model.get("labels", ())
        if isinstance(raw, (list, tuple)):
            return {str(x) for x in raw}
    return set()


__all__ = [
    "BACKEND_TO_DEVICE",
    "CANONICAL_DEVICES",
    "CAPABILITY_ALIASES",
    "CURATED_MODEL_TAGS",
    "DEFAULT_DEVICE",
    "DEVICE_CLASSES",
    "DEVICE_TO_DEFAULT_PROFILE",
    "LEGACY_BACKENDS",
    "MODEL_BACKENDS",
    "MODEL_CAPABILITIES",
    "RUNTIME_FAMILIES",
    "SELECTABLE_BACKENDS",
    "SLOT_TYPES",
    "VALID_DEVICES",
    "DeviceMeta",
    "canonical_device",
    "capability_from_filename",
    "classify",
    "device_to_backend",
    "is_resolvable",
    "labels_of",
    "map_backend_to_device",
    "model_is_mtp_eligible",
]
