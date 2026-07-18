"""``Modality`` — the closed enum for what a model *does* (§7.1d / ML-6).

Before this module, "what does this model do" was answered by 9+
overlapping, hand-synced vocabularies (``model.labels``, ``Model.tags``,
``Model.capabilities``, ``CuratedModel.capability``, slot ``type``, the
capability-child tile system, FLM's own ``label`` field, …). This module
is the single closed enum plus the ONE ingest-time fold
(:func:`normalize_modality` / :func:`normalize_modalities`) that every
register/update path should route through so a new alias is added in
exactly one place.

:data:`hal0.model_meta.MODEL_CAPABILITIES` and
:data:`hal0.model_meta.CAPABILITY_ALIASES` now source from here so the
canonical spellings and the tolerated synonyms can never drift apart
from this enum.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any

log = logging.getLogger(__name__)


class Modality(StrEnum):
    """Closed set of things a model can do. See module docstring."""

    CHAT = "chat"
    VISION = "vision"
    EMBED = "embed"
    RERANK = "rerank"
    ASR = "asr"
    TTS = "tts"
    IMAGE = "image"
    VIDEO = "video"


#: Tolerated synonyms → canonical :class:`Modality` spelling. This is the
#: ONE ingest-folding table — a new alias is added here and nowhere else.
#: Mirrors the synonyms the codebase has historically accepted: FLM/STT
#: tooling spells transcription ``stt``, the slot-type-flavoured spellings
#: (``embedding``/``reranking``/``transcription``), and ``img`` for image.
MODALITY_ALIASES: dict[str, str] = {
    "stt": "asr",
    "transcription": "asr",
    "embedding": "embed",
    "embeddings": "embed",
    "reranking": "rerank",
    "img": "image",
}


def normalize_modality(value: str | None) -> Modality | None:
    """Fold one raw string into a canonical :class:`Modality`, or ``None``.

    Unknown values (including empty/None input) return ``None`` rather
    than raising — ingest paths should drop the value, not crash the
    register/update call, on an operator typo or a not-yet-supported
    modality string.
    """
    if value is None:
        return None
    v = str(value).strip().lower()
    if not v:
        return None
    folded = MODALITY_ALIASES.get(v, v)
    try:
        return Modality(folded)
    except ValueError:
        log.warning("modality.unknown_dropped", extra={"raw": value})
        return None


def normalize_modalities(values: Iterable[Any] | None) -> list[Modality]:
    """Fold + dedupe a raw iterable of modality-ish strings.

    Order is preserved (first-seen wins on duplicates); unknown entries
    are dropped (see :func:`normalize_modality`). ``None`` input yields
    an empty list.
    """
    if not values:
        return []
    seen: dict[Modality, None] = {}
    for raw in values:
        m = normalize_modality(raw if isinstance(raw, str) else str(raw))
        if m is not None and m not in seen:
            seen[m] = None
    return list(seen)


# ── slot type projection ─────────────────────────────────────────────────────

#: Dominant-modality → dispatcher slot ``type``. Single source replacing the
#: several hand-synced sibling maps (``capabilities/orchestrator._CHILD_TO_SLOT_TYPE``,
#: ``_CAPABILITY_TO_SLOT_TYPE``, ``api/routes/models._MODALITY_TO_SLOT_TYPE``).
MODALITY_TO_SLOT_TYPE: dict[Modality, str] = {
    Modality.CHAT: "llm",
    Modality.VISION: "llm",
    Modality.EMBED: "embedding",
    Modality.RERANK: "reranking",
    Modality.ASR: "transcription",
    Modality.TTS: "tts",
    Modality.IMAGE: "image",
    Modality.VIDEO: "image",  # video rides the image slot type for v1
}

#: Precedence when a model advertises several modalities — a dedicated
#: embedder that also lists ``chat`` should still resolve to the embedding
#: slot type. ``chat`` is the lowest-priority fallback.
_TYPE_PRIORITY: tuple[Modality, ...] = (
    Modality.RERANK,
    Modality.EMBED,
    Modality.ASR,
    Modality.TTS,
    Modality.IMAGE,
    Modality.VIDEO,
    Modality.VISION,
    Modality.CHAT,
)


def slot_type_for(modalities: Iterable[Modality]) -> str:
    """Return the dispatcher slot ``type`` implied by a modality set.

    Picks the highest-priority modality present (see
    :data:`_TYPE_PRIORITY`) and projects it through
    :data:`MODALITY_TO_SLOT_TYPE`. Falls back to ``"llm"`` (chat is the
    universal slot type) when ``modalities`` is empty or carries nothing
    recognised.
    """
    present = set(modalities)
    for m in _TYPE_PRIORITY:
        if m in present:
            return MODALITY_TO_SLOT_TYPE[m]
    return "llm"


# ── derivation from model facts ──────────────────────────────────────────────


def derive_modalities(
    *,
    mmproj: str | None = None,
    preferred_runner: str | None = None,
    pooling_type: int | None = None,
    model_id: str = "",
    explicit: Iterable[Any] | None = None,
) -> list[Modality]:
    """Derive a model's modality list from launch-time facts.

    Per §7.1d §2.5 — modalities are recomputed on pull/swap from facts,
    not hand-authored:

      * ``vision`` ⟸ ``mmproj`` presence.
      * ``embed``/``rerank`` ⟸ ``pooling_type`` (1 = embed, 4 = rerank)
        when the runner is ``llama-server``, with an id-substring
        fallback for ``rerank`` when pooling metadata is absent.
      * ``asr`` ⟸ runner in ``{"flm", "moonshine"}``.
      * ``tts`` ⟸ runner in ``{"kokoro", "qwen3tts"}``.
      * ``image`` ⟸ runner ``"comfyui"``.
      * ``chat`` is the default when nothing else is derived.

    ``explicit`` (usually ``Model.modalities_override``) is folded in as
    a union on top of the derived set — the operator escape hatch for
    facts the detector can't infer (e.g. a hand-curated ComfyUI workflow
    that also does video).
    """
    found: list[Modality] = []

    def _add(m: Modality) -> None:
        if m not in found:
            found.append(m)

    mid = model_id.lower()

    if pooling_type == 4 or "rerank" in mid:
        _add(Modality.RERANK)
    elif pooling_type is not None and pooling_type > 0:
        _add(Modality.EMBED)

    runner = (preferred_runner or "").strip().lower()
    if runner in {"flm", "moonshine"}:
        _add(Modality.ASR)
    if runner in {"kokoro", "qwen3tts"}:
        _add(Modality.TTS)
    if runner == "comfyui":
        _add(Modality.IMAGE)

    if not found:
        _add(Modality.CHAT)
    if mmproj:
        _add(Modality.VISION)

    for m in normalize_modalities(explicit):
        _add(m)

    return found


def derive_modalities_from_model_info(model_info: Mapping[str, Any]) -> list[Modality]:
    """Convenience wrapper of :func:`derive_modalities` over a registry dump.

    Accepts the shape :meth:`hal0.registry.model.Model.model_dump` (or the
    ``_resolve_model_info``-style dict SlotManager already builds):
    ``mmproj``, ``preferred_runner``, ``metadata.pooling_type``, ``id``,
    ``modalities_override``.
    """
    metadata = model_info.get("metadata") or {}
    pooling_type = metadata.get("pooling_type") if isinstance(metadata, Mapping) else None
    return derive_modalities(
        mmproj=model_info.get("mmproj"),
        preferred_runner=model_info.get("preferred_runner"),
        pooling_type=pooling_type,
        model_id=str(model_info.get("id") or model_info.get("_model_key") or ""),
        explicit=model_info.get("modalities_override"),
    )


__all__ = [
    "MODALITY_ALIASES",
    "MODALITY_TO_SLOT_TYPE",
    "Modality",
    "derive_modalities",
    "derive_modalities_from_model_info",
    "normalize_modalities",
    "normalize_modality",
    "slot_type_for",
]
