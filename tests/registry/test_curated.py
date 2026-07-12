"""Tests for the curated model catalogue."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.registry.curated import (
    CURATED_BY_ID,
    CURATED_MODELS,
    CuratedModel,
    get_curated,
)


def test_catalogue_has_named_picks() -> None:
    """The wizard contract names these three — they must always be present."""
    ids = {m.id for m in CURATED_MODELS}
    assert {"qwen3-4b", "llama32-3b", "phi3-mini"}.issubset(ids)


def test_catalogue_entries_have_hf_coordinates() -> None:
    """Every entry must carry hf_repo + hf_file (the pull layer's input).

    Allowed file extensions: .gguf for chat (llama.cpp), .safetensors /
    .ckpt for image-gen (ComfyUI). Anything else trips this so a typo
    doesn't make it into a release.

    ``bundle_only`` entries (#500) are exempt from the extension check:
    they are bundled-stock models loaded via their own recipe
    (whispercpp/kokoro/sd-cpp) rather than hal0's hf pull layer, so they
    legitimately carry .bin / .onnx coordinates. hf_repo/hf_file are still
    required (informational), but the extension allowlist does not apply.
    """
    # .bin covers whisper.cpp ggml weights (#514 — whisper-large-v3-turbo is a
    # visible STT default loaded via the whispercpp recipe).
    allowed_suffixes = (".gguf", ".safetensors", ".ckpt", ".bin")
    for m in CURATED_MODELS:
        if m.tags and "npu" in m.tags:
            # NPU models are served via FLM, not GGUF — no hf_repo pull. But
            # they must still be deployable: assert the FLM slot coordinate
            # instead of a bare skip (guards against undeployable entries).
            assert m.recommended_slot == "flm", (
                f"{m.id}: npu-tagged entry must set recommended_slot='flm'"
            )
            continue
        assert m.hf_repo, f"{m.id}: hf_repo is required"
        assert m.hf_file, f"{m.id}: hf_file is required"
        if m.bundle_only:
            continue
        assert m.hf_file.endswith(allowed_suffixes), (
            f"{m.id}: hf_file {m.hf_file!r} not in allowed extensions {allowed_suffixes}"
        )


def test_get_curated_hit_and_miss() -> None:
    assert get_curated("qwen3-4b") is not None
    assert get_curated("not-a-real-id") is None


def test_curated_model_validates_required_fields() -> None:
    """The Pydantic model rejects missing required fields."""
    with pytest.raises(ValidationError):
        CuratedModel(id="test")  # type: ignore[call-arg]


def test_lookup_index_matches_list() -> None:
    """CURATED_BY_ID is the same set as the list."""
    assert set(CURATED_BY_ID.keys()) == {m.id for m in CURATED_MODELS}


def test_memory_pipeline_default_embed_model_is_pullable() -> None:
    """The memory pipeline's default embed id must resolve to a real curated pull source (#F28).

    ``hal0.memory.honcho_env.DEFAULT_FEATURE_MODELS["embedding"]`` (and
    ``HonchoLLMConfig.embedding_dimensions``' default) both point at this
    id — without a curated ``hf_repo``/``hf_file`` for it, `hal0 model pull
    qwen3-embedding-0-6b-q8-0` 422s and the memory pipeline (Hindsight
    retain + the Honcho deriver) has no embedding model to actually pull.
    """
    from hal0.memory.honcho_env import DEFAULT_FEATURE_MODELS

    embed_id = DEFAULT_FEATURE_MODELS["embedding"]
    model = get_curated(embed_id)
    assert model is not None, (
        f"{embed_id!r} (memory pipeline's default embed model) has no curated catalogue entry"
    )
    assert model.hf_repo and model.hf_file
    assert model.capability == "embed"
