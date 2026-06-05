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
    they are Lemonade-stock models loaded via their own recipe
    (whispercpp/kokoro/sd-cpp) rather than hal0's hf pull layer, so they
    legitimately carry .bin / .onnx coordinates. hf_repo/hf_file are still
    required (informational), but the extension allowlist does not apply.
    """
    # .bin covers whisper.cpp ggml weights (#514 — whisper-large-v3-turbo is a
    # visible STT default loaded via Lemonade's whispercpp recipe).
    allowed_suffixes = (".gguf", ".safetensors", ".ckpt", ".bin")
    for m in CURATED_MODELS:
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
