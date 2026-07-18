"""Tests for hal0.model_meta.modality — §7.1d / ML-6.

Covers the closed ``Modality`` enum, the single alias-folding ingest
point (:func:`normalize_modality` / :func:`normalize_modalities`), the
slot-type projection, and the fact-based derivation rules.
"""

from __future__ import annotations

from hal0.model_meta.modality import (
    MODALITY_ALIASES,
    Modality,
    derive_modalities,
    derive_modalities_from_model_info,
    normalize_modalities,
    normalize_modality,
    slot_type_for,
)


def test_normalize_modality_canonical_round_trip() -> None:
    for m in Modality:
        assert normalize_modality(m.value) is m


def test_normalize_modality_folds_every_alias() -> None:
    for raw, canonical in MODALITY_ALIASES.items():
        assert normalize_modality(raw) is Modality(canonical)


def test_normalize_modality_is_case_and_whitespace_insensitive() -> None:
    assert normalize_modality("  STT ") is Modality.ASR
    assert normalize_modality("Embedding") is Modality.EMBED


def test_normalize_modality_unknown_drops_without_raising() -> None:
    assert normalize_modality("not-a-real-modality") is None
    assert normalize_modality(None) is None
    assert normalize_modality("") is None


def test_normalize_modalities_dedupes_and_preserves_order() -> None:
    out = normalize_modalities(["chat", "vision", "chat", "stt"])
    assert out == [Modality.CHAT, Modality.VISION, Modality.ASR]


def test_normalize_modalities_none_input_is_empty() -> None:
    assert normalize_modalities(None) == []
    assert normalize_modalities([]) == []


def test_slot_type_for_priority_rerank_over_chat() -> None:
    assert slot_type_for([Modality.CHAT, Modality.RERANK]) == "reranking"


def test_slot_type_for_vision_is_llm() -> None:
    assert slot_type_for([Modality.VISION]) == "llm"


def test_slot_type_for_empty_falls_back_to_llm() -> None:
    assert slot_type_for([]) == "llm"


def test_slot_type_for_each_modality() -> None:
    expected = {
        Modality.CHAT: "llm",
        Modality.VISION: "llm",
        Modality.EMBED: "embedding",
        Modality.RERANK: "reranking",
        Modality.ASR: "transcription",
        Modality.TTS: "tts",
        Modality.IMAGE: "image",
        Modality.VIDEO: "image",
    }
    for modality, slot_type in expected.items():
        assert slot_type_for([modality]) == slot_type


def test_derive_modalities_vision_from_mmproj() -> None:
    result = derive_modalities(mmproj="/models/x/mmproj.gguf", preferred_runner="llama-server")
    assert set(result) == {Modality.CHAT, Modality.VISION}


def test_derive_modalities_embed_from_pooling() -> None:
    result = derive_modalities(pooling_type=1, model_id="bge-m3")
    assert result == [Modality.EMBED]


def test_derive_modalities_rerank_from_pooling_type_4() -> None:
    result = derive_modalities(pooling_type=4, model_id="bge-reranker")
    assert result == [Modality.RERANK]


def test_derive_modalities_asr_from_runner() -> None:
    assert derive_modalities(preferred_runner="moonshine") == [Modality.ASR]
    assert derive_modalities(preferred_runner="flm") == [Modality.ASR]


def test_derive_modalities_tts_from_runner() -> None:
    assert derive_modalities(preferred_runner="kokoro") == [Modality.TTS]
    assert derive_modalities(preferred_runner="qwen3tts") == [Modality.TTS]


def test_derive_modalities_image_from_comfyui_runner() -> None:
    assert derive_modalities(preferred_runner="comfyui") == [Modality.IMAGE]


def test_derive_modalities_defaults_to_chat() -> None:
    assert derive_modalities() == [Modality.CHAT]


def test_derive_modalities_override_unions_in() -> None:
    result = derive_modalities(preferred_runner="comfyui", explicit=["video"])
    assert set(result) == {Modality.IMAGE, Modality.VIDEO}


def test_derive_modalities_from_model_info_reads_metadata_pooling() -> None:
    model_info = {
        "id": "bge-m3",
        "mmproj": None,
        "preferred_runner": "llama-server",
        "metadata": {"pooling_type": 1},
    }
    assert derive_modalities_from_model_info(model_info) == [Modality.EMBED]
