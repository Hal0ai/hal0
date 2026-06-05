"""Unit tests for ``hal0.lemonade.server_models_gen``.

The generator turns hal0's registry.toml into Lemonade Server's
``server_models.json`` shape. Tests assert:

  * Each hal0 capability maps to the correct Lemonade label.
  * The bge-reranker-v2-m3-q4_k_m smoke case (the spike's smoking-gun
    rerank failure) gets ``labels=["reranking"]``.
  * Backend → recipe resolution covers chat (llamacpp), asr (whispercpp),
    tts (kokoro), image (sd-cpp), NPU (flm).
  * Checkpoint formatting handles HF coords + local-path fallback.
  * The atomic write doesn't leave half-written files on disk.
  * The CLI entry point round-trips registry → JSON file.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import tomli_w

from hal0.lemonade.server_models_gen import (
    STOCK_FALLBACK_IDS,
    cli_main,
    generate_server_models,
    write_server_models,
)
from hal0.registry.curated import CURATED_BY_ID

# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_registry(path: Path, models: dict[str, dict]) -> None:
    """Write a minimal registry.toml at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"models": models}
    with open(path, "wb") as f:
        tomli_w.dump(payload, f)


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    return tmp_path / "registry" / "registry.toml"


# ── Capability → label mapping ───────────────────────────────────────────────


class TestCapabilityMapping:
    """Each hal0 capability maps to the correct Lemonade label."""

    def test_chat_has_no_special_label(self, registry_path: Path) -> None:
        _write_registry(
            registry_path,
            {
                "hermes-4-14b-q5_k_m": {
                    "path": "/mnt/ai-models/local/hermes-4-14b-q5_k_m.gguf",
                    "capabilities": ["chat"],
                    "backends": ["vulkan", "rocm", "cpu"],
                    "hf_repo": "NousResearch/Hermes-4-14B-GGUF",
                    "hf_filename": "hermes-4-14b-q5_k_m.gguf",
                    "size_bytes": 11_500_000_000,
                },
            },
        )
        out = generate_server_models(registry_path)
        entry = out["hermes-4-14b-q5_k_m"]
        # No type label needed: Lemonade's classifier defaults to LLM.
        assert entry["labels"] == []
        assert entry["recipe"] == "llamacpp"
        assert entry["max_context_window"] == 8192  # default for llm

    def test_embed_maps_to_embeddings_label(self, registry_path: Path) -> None:
        _write_registry(
            registry_path,
            {
                "nomic-embed-text-v1": {
                    "path": "/mnt/ai-models/local/nomic-embed-text-v1.gguf",
                    "capabilities": ["embed"],
                    "backends": ["vulkan", "cpu"],
                    "hf_repo": "nomic-ai/nomic-embed-text-v1-GGUF",
                    "hf_filename": "nomic-embed-text-v1.Q4_K_M.gguf",
                },
            },
        )
        out = generate_server_models(registry_path)
        assert out["nomic-embed-text-v1"]["labels"] == ["embeddings"]
        # Embeddings don't get max_context_window (Lemonade reads it
        # from the GGUF arch header).
        assert "max_context_window" not in out["nomic-embed-text-v1"]

    def test_embedding_plural_alias_also_maps(self, registry_path: Path) -> None:
        """Brief uses the plural English form; hal0 uses singular.
        Generator accepts both."""
        _write_registry(
            registry_path,
            {
                "bge-base-embedding": {
                    "path": "/x.gguf",
                    "capabilities": ["embedding"],
                    "backends": ["llamacpp"],
                },
            },
        )
        out = generate_server_models(registry_path)
        assert out["bge-base-embedding"]["labels"] == ["embeddings"]

    def test_asr_maps_to_transcription_label(self, registry_path: Path) -> None:
        _write_registry(
            registry_path,
            {
                "whisper-base": {
                    "path": "/models/whisper-base.bin",
                    "capabilities": ["asr"],
                    "backends": ["whispercpp"],
                    "hf_repo": "ggerganov/whisper.cpp",
                    "hf_filename": "ggml-base.bin",
                },
            },
        )
        out = generate_server_models(registry_path)
        assert out["whisper-base"]["labels"] == ["transcription"]
        assert out["whisper-base"]["recipe"] == "whispercpp"

    def test_tts_maps_to_tts_label(self, registry_path: Path) -> None:
        _write_registry(
            registry_path,
            {
                "kokoro-v1": {
                    "path": "/models/kokoro-v1",
                    "capabilities": ["tts"],
                    "backends": ["kokoro"],
                    "hf_repo": "mikkoph/kokoro-onnx",
                },
            },
        )
        out = generate_server_models(registry_path)
        assert out["kokoro-v1"]["labels"] == ["tts"]
        assert out["kokoro-v1"]["recipe"] == "kokoro"

    def test_image_maps_to_image_label(self, registry_path: Path) -> None:
        _write_registry(
            registry_path,
            {
                "sd-turbo": {
                    "path": "/models/sd-turbo.safetensors",
                    "capabilities": ["image"],
                    "backends": ["sd-cpp"],
                    "hf_repo": "stabilityai/sd-turbo",
                    "hf_filename": "sd_turbo.safetensors",
                },
            },
        )
        out = generate_server_models(registry_path)
        assert out["sd-turbo"]["labels"] == ["image"]
        assert out["sd-turbo"]["recipe"] == "sd-cpp"

    def test_unknown_capability_falls_back_to_llm(self, registry_path: Path) -> None:
        _write_registry(
            registry_path,
            {
                "weird-model": {
                    "path": "/x.gguf",
                    "capabilities": ["bogus-cap"],
                    "backends": ["vulkan"],
                },
            },
        )
        out = generate_server_models(registry_path)
        # bogus capability ignored, defaults to llm (no labels).
        assert out["weird-model"]["labels"] == []
        assert out["weird-model"]["recipe"] == "llamacpp"
        assert out["weird-model"]["max_context_window"] == 8192


# ── Multi-capability resolution ──────────────────────────────────────────────


class TestMultiCapability:
    """When a model advertises multiple capabilities, the strongest wins.

    Rerank > Embed > Chat per the generator's strength ranking.
    """

    def test_rerank_wins_over_embed(self, registry_path: Path) -> None:
        _write_registry(
            registry_path,
            {
                "dual-rerank-embed": {
                    "path": "/x.gguf",
                    "capabilities": ["embed", "rerank"],
                    "backends": ["llamacpp"],
                },
            },
        )
        out = generate_server_models(registry_path)
        assert out["dual-rerank-embed"]["labels"] == ["reranking"]

    def test_embed_wins_over_chat(self, registry_path: Path) -> None:
        _write_registry(
            registry_path,
            {
                "dual-embed-chat": {
                    "path": "/x.gguf",
                    "capabilities": ["chat", "embed"],
                    "backends": ["llamacpp"],
                },
            },
        )
        out = generate_server_models(registry_path)
        assert out["dual-embed-chat"]["labels"] == ["embeddings"]

    def test_vision_chat_keeps_llm_with_vision_secondary(self, registry_path: Path) -> None:
        """Vision is a chat-indicator label in Lemonade; emitting it as a
        secondary label keeps the model classified as LLM even if the
        registry adds vision alongside chat."""
        _write_registry(
            registry_path,
            {
                "qwen2-vl-7b": {
                    "path": "/x.gguf",
                    "capabilities": ["chat", "vision"],
                    "backends": ["vulkan"],
                },
            },
        )
        out = generate_server_models(registry_path)
        labels = out["qwen2-vl-7b"]["labels"]
        assert "vision" in labels
        # No embedding/reranking label snuck in.
        assert "embeddings" not in labels
        assert "reranking" not in labels


# ── Smoking-gun case: bge-reranker-v2-m3-q4_k_m ──────────────────────────────


class TestBgeRerankerSmokeCase:
    """The 2026-05-22 spike's smoking-gun failure was that bge-reranker-v2-m3
    discovered via extra_models_dir loaded as type=LLM (label ``custom``),
    so the child llama-server never got ``--reranking`` and `/v1/reranking`
    returned 501. This test asserts our generator produces an entry that
    Lemonade's ``get_model_type_from_labels()`` will classify as RERANKING.
    """

    def test_bge_reranker_v2_m3_q4_k_m_has_reranking_label(self, registry_path: Path) -> None:
        _write_registry(
            registry_path,
            {
                "bge-reranker-v2-m3-q4_k_m": {
                    "name": "BGE Reranker v2 M3 (Q4_K_M)",
                    "path": "/mnt/ai-models/local/bge-reranker-v2-m3-Q4_K_M.gguf",
                    "size_bytes": 440_000_000,
                    "capabilities": ["rerank"],
                    "backends": ["llamacpp", "vulkan", "cpu"],
                    "hf_repo": "gpustack/bge-reranker-v2-m3-GGUF",
                    "hf_filename": "bge-reranker-v2-m3-Q4_K_M.gguf",
                    "metadata": {"context_length": 8192},
                },
            },
        )
        out = generate_server_models(registry_path)

        assert "bge-reranker-v2-m3-q4_k_m" in out
        entry = out["bge-reranker-v2-m3-q4_k_m"]

        # The smoke assertion: Lemonade infers RERANKING type from this label.
        assert entry["labels"] == ["reranking"]
        # And we route via llamacpp (which gets --reranking pushed).
        assert entry["recipe"] == "llamacpp"
        # HF checkpoint coords intact so Lemonade resolves the GGUF blob.
        assert (
            entry["checkpoint"] == "gpustack/bge-reranker-v2-m3-GGUF:bge-reranker-v2-m3-Q4_K_M.gguf"
        )


# ── Backend → recipe resolution ──────────────────────────────────────────────


class TestBackendRecipeResolution:
    def test_vulkan_rocm_cpu_all_map_to_llamacpp(self, registry_path: Path) -> None:
        for backend in ("vulkan", "rocm", "cuda", "cpu"):
            _write_registry(
                registry_path,
                {
                    "m": {
                        "path": "/x.gguf",
                        "capabilities": ["chat"],
                        "backends": [backend],
                    }
                },
            )
            out = generate_server_models(registry_path)
            assert out["m"]["recipe"] == "llamacpp", f"backend={backend}"

    def test_flm_backend_wins_over_other_backends(self, registry_path: Path) -> None:
        """NPU is exclusive — flm presence forces flm recipe."""
        _write_registry(
            registry_path,
            {
                "amd-olmo-1b-hybrid": {
                    "path": "/x.onnx",
                    "capabilities": ["chat"],
                    "backends": ["flm", "vulkan"],  # flm even alongside others
                    "hf_repo": "amd/AMD-OLMo-1B-SFT-DPO-onnx-ryzenai-1.7-hybrid",
                },
            },
        )
        out = generate_server_models(registry_path)
        assert out["amd-olmo-1b-hybrid"]["recipe"] == "flm"

    def test_moonshine_backend_maps_to_whispercpp(self, registry_path: Path) -> None:
        """Lemonade dropped moonshine; we route hal0 moonshine entries
        through whispercpp."""
        _write_registry(
            registry_path,
            {
                "moonshine-small": {
                    "path": "/models/moonshine-small",
                    "capabilities": ["asr"],
                    "backends": ["moonshine"],
                },
            },
        )
        out = generate_server_models(registry_path)
        assert out["moonshine-small"]["recipe"] == "whispercpp"

    def test_empty_backends_falls_back_to_label_default(self, registry_path: Path) -> None:
        _write_registry(
            registry_path,
            {
                "stt-only": {
                    "path": "/x",
                    "capabilities": ["asr"],
                    "backends": [],
                },
            },
        )
        out = generate_server_models(registry_path)
        # asr → transcription label → whispercpp default
        assert out["stt-only"]["recipe"] == "whispercpp"


# ── Checkpoint formatting ────────────────────────────────────────────────────


class TestCheckpointFormatting:
    def test_hf_repo_plus_filename(self, registry_path: Path) -> None:
        _write_registry(
            registry_path,
            {
                "m": {
                    "path": "/x.gguf",
                    "capabilities": ["chat"],
                    "backends": ["vulkan"],
                    "hf_repo": "Qwen/Qwen3-4B-GGUF",
                    "hf_filename": "qwen3-4b-q4_k_m.gguf",
                },
            },
        )
        out = generate_server_models(registry_path)
        assert out["m"]["checkpoint"] == "Qwen/Qwen3-4B-GGUF:qwen3-4b-q4_k_m.gguf"

    def test_hf_repo_only(self, registry_path: Path) -> None:
        """Multi-file models (ONNX dirs etc.) use repo-only coords."""
        _write_registry(
            registry_path,
            {
                "amd-onnx-model": {
                    "path": "/models/amd-onnx",
                    "capabilities": ["chat"],
                    "backends": ["flm"],
                    "hf_repo": "amd/AMD-OLMo-1B-SFT-DPO-onnx-ryzenai-1.7-hybrid",
                    "hf_filename": "",
                },
            },
        )
        out = generate_server_models(registry_path)
        assert (
            out["amd-onnx-model"]["checkpoint"] == "amd/AMD-OLMo-1B-SFT-DPO-onnx-ryzenai-1.7-hybrid"
        )

    def test_local_path_fallback_when_no_hf(self, registry_path: Path) -> None:
        _write_registry(
            registry_path,
            {
                "local-only": {
                    "path": "/mnt/ai-models/local/private-finetune.gguf",
                    "capabilities": ["chat"],
                    "backends": ["vulkan"],
                },
            },
        )
        out = generate_server_models(registry_path)
        assert out["local-only"]["checkpoint"] == "/mnt/ai-models/local/private-finetune.gguf"


# ── Size + context window ────────────────────────────────────────────────────


class TestSizeAndContext:
    def test_size_bytes_converted_to_gb(self, registry_path: Path) -> None:
        # 11.5 GB ≈ 12_348_030_976 bytes
        _write_registry(
            registry_path,
            {
                "m": {
                    "path": "/x.gguf",
                    "capabilities": ["chat"],
                    "backends": ["vulkan"],
                    "size_bytes": 12_348_030_976,
                },
            },
        )
        out = generate_server_models(registry_path)
        # 12_348_030_976 / 1024**3 ≈ 11.5
        assert 11.4 < out["m"]["size"] < 11.6

    def test_size_zero_when_unknown(self, registry_path: Path) -> None:
        _write_registry(
            registry_path,
            {
                "m": {
                    "path": "/x.gguf",
                    "capabilities": ["chat"],
                    "backends": ["vulkan"],
                    "size_bytes": 0,
                },
            },
        )
        out = generate_server_models(registry_path)
        assert out["m"]["size"] == 0.0

    def test_context_size_from_defaults_wins(self, registry_path: Path) -> None:
        _write_registry(
            registry_path,
            {
                "m": {
                    "path": "/x.gguf",
                    "capabilities": ["chat"],
                    "backends": ["vulkan"],
                    "defaults": {"context_size": 32768},
                    "metadata": {"context_length": 8192},
                },
            },
        )
        out = generate_server_models(registry_path)
        assert out["m"]["max_context_window"] == 32768

    def test_context_length_from_metadata(self, registry_path: Path) -> None:
        _write_registry(
            registry_path,
            {
                "m": {
                    "path": "/x.gguf",
                    "capabilities": ["chat"],
                    "backends": ["vulkan"],
                    "metadata": {"context_length": 16384},
                },
            },
        )
        out = generate_server_models(registry_path)
        assert out["m"]["max_context_window"] == 16384

    def test_context_default_for_llm_with_no_signal(self, registry_path: Path) -> None:
        _write_registry(
            registry_path,
            {
                "m": {
                    "path": "/x.gguf",
                    "capabilities": ["chat"],
                    "backends": ["vulkan"],
                },
            },
        )
        out = generate_server_models(registry_path)
        # No regression: a model with no GGUF ctx signal still gets the
        # conservative 8192 fallback (#513 — small/unknown tiers stay safe).
        assert out["m"]["max_context_window"] == 8192

    def test_large_arch_ctx_from_metadata_is_preserved(self, registry_path: Path) -> None:
        """#513: a MoE primary advertising a 131072 arch max keeps it."""
        _write_registry(
            registry_path,
            {
                "moe-primary": {
                    "path": "/x.gguf",
                    "capabilities": ["chat"],
                    "backends": ["vulkan"],
                    "metadata": {"context_length": 131072},
                },
            },
        )
        out = generate_server_models(registry_path)
        assert out["moe-primary"]["max_context_window"] == 131072


# ── Snapshot ──────────────────────────────────────────────────────────────────


class TestSnapshot:
    """End-to-end snapshot of a representative multi-modality registry.

    Exercises a primary chat model, the bge-reranker case, an embed model,
    an ASR model, a TTS model, and an image model in one fixture.
    """

    def test_full_registry_snapshot(self, registry_path: Path) -> None:
        _write_registry(
            registry_path,
            {
                "hermes-4-14b-q5_k_m": {
                    "path": "/mnt/ai-models/local/hermes-4-14b-q5_k_m.gguf",
                    "capabilities": ["chat"],
                    "backends": ["vulkan", "rocm", "cpu"],
                    "hf_repo": "NousResearch/Hermes-4-14B-GGUF",
                    "hf_filename": "hermes-4-14b-q5_k_m.gguf",
                    "size_bytes": 11_000_000_000,
                    "metadata": {"context_length": 131072},
                },
                "bge-reranker-v2-m3-q4_k_m": {
                    "path": "/mnt/ai-models/local/bge-reranker-v2-m3-Q4_K_M.gguf",
                    "capabilities": ["rerank"],
                    "backends": ["llamacpp", "vulkan", "cpu"],
                    "hf_repo": "gpustack/bge-reranker-v2-m3-GGUF",
                    "hf_filename": "bge-reranker-v2-m3-Q4_K_M.gguf",
                },
                "nomic-embed-text-v1": {
                    "path": "/mnt/ai-models/local/nomic-embed.gguf",
                    "capabilities": ["embed"],
                    "backends": ["llamacpp"],
                    "hf_repo": "nomic-ai/nomic-embed-text-v1-GGUF",
                    "hf_filename": "nomic-embed-text-v1.Q4_K_M.gguf",
                },
                "whisper-base": {
                    "path": "/models/whisper-base.bin",
                    "capabilities": ["asr"],
                    "backends": ["whispercpp"],
                    "hf_repo": "ggerganov/whisper.cpp",
                    "hf_filename": "ggml-base.bin",
                },
                "kokoro-v1": {
                    "path": "/models/kokoro-v1",
                    "capabilities": ["tts"],
                    "backends": ["kokoro"],
                    "hf_repo": "mikkoph/kokoro-onnx",
                },
                "sd-turbo": {
                    "path": "/models/sd-turbo.safetensors",
                    "capabilities": ["image"],
                    "backends": ["sd-cpp"],
                    "hf_repo": "stabilityai/sd-turbo",
                    "hf_filename": "sd_turbo.safetensors",
                },
            },
        )
        out = generate_server_models(registry_path)

        # Output is ordered by sorted model id for diff-stability.
        assert list(out.keys()) == sorted(out.keys())

        # Spot-check the modalities all serialize correctly.
        assert out["hermes-4-14b-q5_k_m"]["recipe"] == "llamacpp"
        assert out["hermes-4-14b-q5_k_m"]["labels"] == []
        assert out["hermes-4-14b-q5_k_m"]["max_context_window"] == 131072

        assert out["bge-reranker-v2-m3-q4_k_m"]["labels"] == ["reranking"]
        assert out["nomic-embed-text-v1"]["labels"] == ["embeddings"]
        assert out["whisper-base"]["labels"] == ["transcription"]
        assert out["kokoro-v1"]["labels"] == ["tts"]
        assert out["sd-turbo"]["labels"] == ["image"]

    def test_generator_is_deterministic(self, registry_path: Path) -> None:
        _write_registry(
            registry_path,
            {
                "b-model": {"path": "/b", "capabilities": ["chat"], "backends": ["vulkan"]},
                "a-model": {"path": "/a", "capabilities": ["chat"], "backends": ["vulkan"]},
            },
        )
        out1 = generate_server_models(registry_path)
        out2 = generate_server_models(registry_path)
        assert json.dumps(out1) == json.dumps(out2)
        # Keys come out sorted.
        assert list(out1.keys()) == ["a-model", "b-model"]


# ── Atomic write ─────────────────────────────────────────────────────────────


class TestAtomicWrite:
    def test_writes_valid_json_file(self, tmp_path: Path) -> None:
        registry = tmp_path / "registry.toml"
        output = tmp_path / "out" / "server_models.json"
        _write_registry(
            registry,
            {
                "m": {
                    "path": "/x.gguf",
                    "capabilities": ["embed"],
                    "backends": ["vulkan"],
                    "hf_repo": "org/repo",
                    "hf_filename": "x.gguf",
                },
            },
        )

        write_server_models(registry, output)

        assert output.exists()
        with open(output) as f:
            parsed = json.load(f)
        assert "m" in parsed
        assert parsed["m"]["labels"] == ["embeddings"]

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        """Output's parent doesn't have to exist beforehand."""
        registry = tmp_path / "registry.toml"
        output = tmp_path / "deep" / "nested" / "path" / "server_models.json"
        _write_registry(registry, {})
        write_server_models(registry, output)
        assert output.exists()

    def test_overwrite_is_atomic(self, tmp_path: Path) -> None:
        """The output path swaps in atomically (sentinel exists throughout)."""
        registry = tmp_path / "registry.toml"
        output = tmp_path / "server_models.json"

        # Pre-existing content.
        output.write_text('{"old": true}')
        original_inode = output.stat().st_ino

        _write_registry(
            registry,
            {
                "m": {
                    "path": "/x.gguf",
                    "capabilities": ["chat"],
                    "backends": ["vulkan"],
                },
            },
        )
        write_server_models(registry, output)

        # File still exists (no transient missing-file window) and the new
        # inode replaces the old one — proof of os.replace, not in-place write.
        assert output.exists()
        new_inode = output.stat().st_ino
        assert new_inode != original_inode

        with open(output) as f:
            parsed = json.load(f)
        assert "m" in parsed
        assert "old" not in parsed

    def test_no_tempfile_left_behind_on_success(self, tmp_path: Path) -> None:
        registry = tmp_path / "registry.toml"
        output = tmp_path / "server_models.json"
        _write_registry(
            registry, {"m": {"path": "/x.gguf", "capabilities": ["chat"], "backends": ["vulkan"]}}
        )
        write_server_models(registry, output)
        # No .server_models.json.*.tmp leftovers.
        leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".")]
        assert leftovers == []

    def test_failure_during_write_leaves_original_intact(self, tmp_path: Path) -> None:
        """If os.replace raises, the original file is unchanged and no
        tempfile is left behind."""
        registry = tmp_path / "registry.toml"
        output = tmp_path / "server_models.json"
        _write_registry(
            registry, {"m": {"path": "/x.gguf", "capabilities": ["chat"], "backends": ["vulkan"]}}
        )

        output.write_text('{"original": true}')
        original_bytes = output.read_bytes()

        with (
            patch(
                "hal0.lemonade.server_models_gen.os.replace",
                side_effect=OSError("simulated EXDEV"),
            ),
            pytest.raises(OSError, match="simulated EXDEV"),
        ):
            write_server_models(registry, output)

        # Original content preserved.
        assert output.read_bytes() == original_bytes
        # Tempfile cleaned up.
        leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".")]
        assert leftovers == []


# ── Missing registry handling ────────────────────────────────────────────────


class TestRegistryMissingOrMalformed:
    def test_missing_registry_yields_stock_fallback(self, tmp_path: Path) -> None:
        """A fresh install (no registry.toml) must NOT blank the catalog.

        Issue #210: install.sh writes this output over Lemonade's bundled
        ``server_models.json`` (180 stock entries). If we emit ``{}`` the
        daemon has nothing to load and the user can't chat until they
        manually pull. Fall back to the canonical curated stock set so a
        fresh box has loadable models out of the box.
        """
        out = generate_server_models(tmp_path / "does-not-exist.toml")
        assert out, "empty registry must fall back to a non-empty stock catalog"

    def test_malformed_registry_yields_stock_fallback(self, tmp_path: Path) -> None:
        registry = tmp_path / "registry.toml"
        registry.write_text("this is not valid toml [[[")
        out = generate_server_models(registry)
        assert out, "malformed registry must fall back to a non-empty stock catalog"

    def test_list_shape_registry_accepted(self, tmp_path: Path) -> None:
        """Mirror ModelRegistry's haloai backcompat: list-of-tables works."""
        registry = tmp_path / "registry.toml"
        registry.write_text(
            "[[models]]\n"
            'id = "a"\n'
            'path = "/a.gguf"\n'
            'capabilities = ["chat"]\n'
            'backends = ["vulkan"]\n'
        )
        out = generate_server_models(registry)
        assert "a" in out

    def test_empty_models_table_yields_stock_fallback(self, tmp_path: Path) -> None:
        """A registry that parses fine but lists zero models still falls back."""
        registry = tmp_path / "registry.toml"
        registry.write_text("")
        out = generate_server_models(registry)
        assert out, "registry with no models must fall back to stock catalog"


# ── Stock fallback on empty registry (issue #210) ────────────────────────────


class TestStockFallback:
    """When the registry yields no usable models, the generator emits a
    curated STOCK set instead of an empty catalog, so a fresh install has
    loadable models without manual ``hal0 model pull``."""

    def test_fallback_ids_are_canonical_curated_ids(self, tmp_path: Path) -> None:
        """Every stock-fallback id resolves in the canonical curated catalog
        (``CURATED_BY_ID``). Blocked-by #500 guarantees these ids exist and
        do not drift."""
        out = generate_server_models(tmp_path / "missing.toml")
        assert out  # non-empty
        for mid in out:
            assert mid in CURATED_BY_ID, f"stock id {mid!r} not a canonical curated id"

    def test_fallback_covers_core_chat_modality(self, tmp_path: Path) -> None:
        """A fresh box must at least have a loadable chat model so the user
        can reach a streamed token on first run."""
        # Type-driving labels Lemonade's classifier resolves to a non-LLM type.
        type_labels = {"embeddings", "reranking", "transcription", "tts", "image"}
        out = generate_server_models(tmp_path / "missing.toml")
        chat_ids = [
            mid
            for mid in out
            if CURATED_BY_ID[mid].capability == "chat"
            # A chat model carries no type-driving label, so Lemonade
            # classifies it as LLM (chat).
            and not (set(out[mid]["labels"]) & type_labels)
        ]
        assert chat_ids, "stock fallback must include at least one chat model"

    def test_fallback_entries_have_valid_lemonade_shape(self, tmp_path: Path) -> None:
        """Each stock entry has the keys Lemonade's loader needs."""
        out = generate_server_models(tmp_path / "missing.toml")
        for mid, entry in out.items():
            assert entry["checkpoint"], f"{mid} missing checkpoint"
            assert entry["recipe"], f"{mid} missing recipe"
            assert isinstance(entry["labels"], list)
            assert entry["suggested"] is False

    def test_fallback_set_matches_declared_ids(self, tmp_path: Path) -> None:
        """The output ids are exactly the declared STOCK_FALLBACK_IDS."""
        out = generate_server_models(tmp_path / "missing.toml")
        assert set(out.keys()) == set(STOCK_FALLBACK_IDS)

    def test_populated_registry_does_not_fall_back(self, tmp_path: Path) -> None:
        """When the registry IS populated, behaviour is unchanged — no stock
        ids leak in."""
        registry = tmp_path / "registry.toml"
        registry.parent.mkdir(parents=True, exist_ok=True)
        with open(registry, "wb") as f:
            tomli_w.dump(
                {
                    "models": {
                        "my-only-model": {
                            "path": "/x.gguf",
                            "capabilities": ["chat"],
                            "backends": ["vulkan"],
                        }
                    }
                },
                f,
            )
        out = generate_server_models(registry)
        assert set(out.keys()) == {"my-only-model"}


# ── CLI entry point ──────────────────────────────────────────────────────────


class TestCliMain:
    def test_dry_run_writes_to_stdout_only(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        registry = tmp_path / "registry.toml"
        output = tmp_path / "out.json"
        _write_registry(
            registry, {"m": {"path": "/x.gguf", "capabilities": ["chat"], "backends": ["vulkan"]}}
        )

        rc = cli_main(["--registry", str(registry), "--output", str(output), "--dry-run"])

        assert rc == 0
        assert not output.exists()  # dry-run didn't touch the file.
        cap = capsys.readouterr()
        parsed = json.loads(cap.out)
        assert "m" in parsed

    def test_writes_to_output_path(self, tmp_path: Path) -> None:
        registry = tmp_path / "registry.toml"
        output = tmp_path / "out.json"
        _write_registry(
            registry,
            {
                "bge-reranker-v2-m3-q4_k_m": {
                    "path": "/x.gguf",
                    "capabilities": ["rerank"],
                    "backends": ["llamacpp"],
                    "hf_repo": "gpustack/bge-reranker-v2-m3-GGUF",
                    "hf_filename": "bge-reranker-v2-m3-Q4_K_M.gguf",
                }
            },
        )

        rc = cli_main(["--registry", str(registry), "--output", str(output)])

        assert rc == 0
        with open(output) as f:
            parsed = json.load(f)
        assert parsed["bge-reranker-v2-m3-q4_k_m"]["labels"] == ["reranking"]
