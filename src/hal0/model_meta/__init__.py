"""model_meta — the one home for model classification + device→backend resolution.

Issue #695. Before this module the same logic was copy-pasted across
five sites (``routes/models.py``, ``routes/slots.py``,
``capabilities/orchestrator.py``, ``omni_router/filter.py``,
``slots/manager.py``); a classification-rule change meant hunting every
copy. They all import from here now.

The module is **stateless** — no classes, no construction, just
importable functions. ``classify`` and ``device_to_backend`` are pure;
``is_resolvable`` takes the registry explicitly as an argument so the
module stays importable everywhere without threading a handle through.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


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
# Plan §4.1 + ADR-0008 §6 locked the four-way mapping; the result feeds
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
        device: One of ``gpu-rocm`` | ``gpu-vulkan`` | ``cpu`` | ``npu``.
                Empty / unknown values fall back to ``(None, None)``
                ("no opinion" — callers apply their own defaults).

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

    After ADR-0006 §7 both the slot TOML and the capabilities catalog
    speak the same enum (``gpu-rocm | gpu-vulkan | cpu | npu``), so this
    is a near-identity. It still tolerates a legacy ``backend``-style
    input (``vulkan|rocm|flm|moonshine|kokoro``) for forward
    compatibility with hand-edited slot TOMLs by routing through
    :func:`hal0.config.schema.map_backend_to_device`.

    Empty input means "no opinion" and returns ``""``.
    """
    from hal0.config.schema import _VALID_DEVICES, map_backend_to_device

    if not value:
        return ""
    if value in _VALID_DEVICES:
        return value
    return map_backend_to_device(value)


# NOTE(#695): this is deliberately NOT expressed through
# ``device_to_backend`` — the two sites disagreed on unknown input.
# ``device_to_backend`` maps unknown devices to ``(None, None)`` ("no
# opinion"), while the orchestrator's ``_slot_backend_for_catalog_id``
# passed unknown tokens through UNCHANGED so hand-edited values stay
# legible on downgrade. Both behaviours are preserved as-is.
_DEVICE_TO_LEGACY_BACKEND: dict[str, str] = {
    "gpu-vulkan": "vulkan",
    "gpu-rocm": "rocm",
    "npu": "flm",
    "cpu": "cpu",
}


def device_to_legacy_backend(device: str) -> str:
    """DEPRECATED namespace — translate a catalog ``device`` id to the legacy
    ``backend`` token.

    Still used by code paths that write the deprecated SlotConfig.backend
    field (kept until the ``backend`` field is excised for downgrade
    legibility). Unknown values pass through unchanged.
    """
    return _DEVICE_TO_LEGACY_BACKEND.get(device, device)


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
    "canonical_device",
    "capability_from_filename",
    "classify",
    "device_to_backend",
    "device_to_legacy_backend",
    "is_resolvable",
    "labels_of",
]
