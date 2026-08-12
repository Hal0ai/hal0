"""Unit tests for hal0.registry.detect.detect()."""

from __future__ import annotations

import struct
from pathlib import Path

from hal0.registry.detect import detect
from hal0.registry.gguf_header import (
    _GGUF_TYPE_ARRAY,
    _GGUF_TYPE_BOOL,
    _GGUF_TYPE_STRING,
    _GGUF_TYPE_UINT32,
)
from tests.registry.test_gguf_header import _build_gguf, _enc_str, _write_fixture


class TestGgufChat:
    def test_chat_model_high_confidence(self, tmp_path: Path) -> None:
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("llama")),
            ("llama.context_length", _GGUF_TYPE_UINT32, struct.pack("<I", 8192)),
        ]
        p = _write_fixture(tmp_path, "llama-7b-q4_k_m.gguf", _build_gguf(3, kvs))
        r = detect(p)
        assert r.confidence == "high"
        assert r.suggested_capabilities == ["chat"]
        assert set(r.suggested_backends) == {"vulkan", "rocm", "cuda", "cpu"}
        assert r.context_length == 8192
        assert r.raw_hints["source"] == "gguf_header"
        assert r.raw_hints["architecture"] == "llama"

    def test_chat_model_pooling_zero_is_chat(self, tmp_path: Path) -> None:
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("llama")),
            ("llama.context_length", _GGUF_TYPE_UINT32, struct.pack("<I", 4096)),
            ("llama.pooling_type", _GGUF_TYPE_UINT32, struct.pack("<I", 0)),
        ]
        p = _write_fixture(tmp_path, "llama-chat.gguf", _build_gguf(3, kvs))
        r = detect(p)
        assert r.suggested_capabilities == ["chat"]

    def test_explicit_pooling_zero_not_overridden_by_tags_or_filename(self, tmp_path: Path) -> None:
        """#1838 (codex review): pooling_type=0 is an authoritative "chat"
        read — a stray general.tags entry or a rerank-sounding filename
        must not second-guess it."""
        tags_bytes = (
            struct.pack("<I", _GGUF_TYPE_STRING) + struct.pack("<Q", 1) + _enc_str("reranker")
        )
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("bert")),
            ("bert.pooling_type", _GGUF_TYPE_UINT32, struct.pack("<I", 0)),
            ("general.tags", _GGUF_TYPE_ARRAY, tags_bytes),
        ]
        p = _write_fixture(tmp_path, "jina-reranker-mislabeled-chat.gguf", _build_gguf(3, kvs))
        r = detect(p)
        assert r.suggested_capabilities == ["chat"]
        assert r.confidence == "high"
        assert r.raw_hints["capability_source"] == "pooling_type"


class TestGgufEmbed:
    def test_pooling_type_nonzero_means_embed(self, tmp_path: Path) -> None:
        # pooling_type=2 (CLS) → embed.
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("bert")),
            ("bert.context_length", _GGUF_TYPE_UINT32, struct.pack("<I", 512)),
            ("bert.pooling_type", _GGUF_TYPE_UINT32, struct.pack("<I", 2)),
        ]
        p = _write_fixture(tmp_path, "bge-small.gguf", _build_gguf(3, kvs))
        r = detect(p)
        assert r.suggested_capabilities == ["embed"]
        assert r.context_length == 512
        assert r.confidence == "high"

    def test_filename_fallback_for_embed_without_pooling(self, tmp_path: Path) -> None:
        # No pooling_type, but filename screams "embed".
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("bert")),
            ("bert.context_length", _GGUF_TYPE_UINT32, struct.pack("<I", 512)),
        ]
        p = _write_fixture(tmp_path, "bge-m3-embedding.gguf", _build_gguf(3, kvs))
        r = detect(p)
        assert r.suggested_capabilities == ["embed"]
        # #1838: a filename-only capability guess must NOT report the same
        # confidence as a header-derived read.
        assert r.confidence == "medium"
        assert r.raw_hints["capability_source"] == "filename"


class TestGgufRerank:
    """#1796: a reranker gguf must classify as rerank, not chat/embed —
    the bug that let a reranker register with ``capabilities: chat``."""

    def test_pooling_type_rank_means_rerank(self, tmp_path: Path) -> None:
        # llama.cpp pooling_type=4 (RANK) is the cross-encoder rerank signal.
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("bert")),
            ("bert.context_length", _GGUF_TYPE_UINT32, struct.pack("<I", 512)),
            ("bert.pooling_type", _GGUF_TYPE_UINT32, struct.pack("<I", 4)),
        ]
        p = _write_fixture(tmp_path, "jina-bert-implementation.gguf", _build_gguf(3, kvs))
        r = detect(p)
        assert r.suggested_capabilities == ["rerank"]
        assert r.confidence == "high"

    def test_filename_fallback_for_rerank_without_pooling(self, tmp_path: Path) -> None:
        # No pooling_type in the header at all — the exact live repro: the
        # header only carries the arch name, so the old code fell all the
        # way through to the "chat" default. The filename token must win.
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("bert")),
        ]
        p = _write_fixture(tmp_path, "jina-reranker-v2-base.gguf", _build_gguf(3, kvs))
        r = detect(p)
        assert r.suggested_capabilities == ["rerank"]
        # #1838: this is a filename guess (no pooling_type, no header
        # tags/causal tie-breaker) — confidence must reflect that.
        assert r.confidence == "medium"
        assert r.raw_hints["capability_source"] == "filename"

    def test_header_tags_tiebreaker_wins_over_ambiguous_filename(self, tmp_path: Path) -> None:
        """The live #1838 repro: two byte-identical files (no pooling_type),
        one with a filename token ("jina-reranker...") and one without
        ("zzscratchrr.gguf") must classify identically once the header
        carries general.tags / attention.causal — the filename must stop
        being the deciding factor."""
        tags_bytes = (
            struct.pack("<I", _GGUF_TYPE_STRING)
            + struct.pack("<Q", 2)
            + _enc_str("reranker")
            + _enc_str("cross-encoder")
        )
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("jina-bert-v2")),
            ("general.tags", _GGUF_TYPE_ARRAY, tags_bytes),
            ("jina-bert-v2.attention.causal", _GGUF_TYPE_BOOL, struct.pack("<B", 0)),
        ]
        payload = _build_gguf(3, kvs)

        # Filename carries no rerank/embed token at all.
        p_no_token = _write_fixture(tmp_path, "zzscratchrr.gguf", payload)
        r_no_token = detect(p_no_token)
        assert r_no_token.suggested_capabilities == ["rerank"]
        assert r_no_token.confidence == "high"
        assert r_no_token.raw_hints["capability_source"] == "header_tags"

        # Byte-identical file, filename DOES carry the "rerank" token —
        # must agree with the token-less copy, not merely happen to match.
        p_token = _write_fixture(tmp_path, "jina-reranker-v1-tiny-en.Q8_0.gguf", payload)
        r_token = detect(p_token)
        assert r_token.suggested_capabilities == ["rerank"]
        assert r_token.confidence == "high"
        assert r_token.raw_hints["capability_source"] == "header_tags"

    def test_causal_false_downgrades_chat_default_confidence(self, tmp_path: Path) -> None:
        """#1838 (codex review): no pooling_type, no tags, no filename token
        — but attention.causal=False rules out a causal chat model. "chat"
        is still returned (embed vs rerank is genuinely unknown) but must
        not claim the ordinary default's high confidence."""
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("jina-bert-v2")),
            ("jina-bert-v2.attention.causal", _GGUF_TYPE_BOOL, struct.pack("<B", 0)),
        ]
        p = _write_fixture(tmp_path, "zzscratchrr-no-tags.gguf", _build_gguf(3, kvs))
        r = detect(p)
        assert r.suggested_capabilities == ["chat"]
        assert r.confidence == "medium"
        assert r.raw_hints["capability_source"] == "causal_conflict"

    def test_filename_hint_used_for_symlink_capability_fallback(self, tmp_path: Path) -> None:
        """#1838 (codex review): the resolved path passed to detect() may be
        a HF hub-cache sha-named blob with no filename signal — the caller
        can pass the operator-typed name via filename_hint so a real
        capability token isn't hidden by symlink resolution."""
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("bert")),
        ]
        blob = _write_fixture(tmp_path, "a1b2c3d4e5f6", _build_gguf(3, kvs))
        # No hint: the blob's own name carries no rerank/embed token.
        r_no_hint = detect(blob)
        assert r_no_hint.suggested_capabilities == ["chat"]
        # With the operator's real filename supplied as a hint, the token
        # table sees "jina-reranker" and correctly classifies rerank.
        r_hinted = detect(blob, filename_hint="jina-reranker-v2-base.gguf")
        assert r_hinted.suggested_capabilities == ["rerank"]
        assert r_hinted.confidence == "medium"
        assert r_hinted.raw_hints["capability_source"] == "filename"


class TestGgufUnreadable:
    def test_bad_gguf_degrades_to_filename_heuristic(self, tmp_path: Path) -> None:
        p = tmp_path / "qwen3-4b.gguf"
        p.write_bytes(b"NOTGGUF" + b"\x00" * 100)
        r = detect(p)
        # .gguf extension → still seed backends, but low confidence.
        assert r.confidence == "low"
        assert r.suggested_capabilities == ["chat"]
        assert set(r.suggested_backends) == {"vulkan", "rocm", "cuda", "cpu"}
        assert r.raw_hints["source"] == "filename"
        assert r.raw_hints.get("gguf_header_read") == "failed"


class TestFilenameHeuristic:
    def test_moonshine_filename(self, tmp_path: Path) -> None:
        p = tmp_path / "moonshine-base.onnx"
        p.write_bytes(b"")
        r = detect(p)
        assert r.suggested_backends == ["moonshine"]
        assert r.suggested_capabilities == ["asr"]
        assert r.confidence == "low"

    def test_kokoro_filename(self, tmp_path: Path) -> None:
        p = tmp_path / "kokoro-82M.pth"
        p.write_bytes(b"")
        r = detect(p)
        assert r.suggested_backends == ["kokoro"]
        assert r.suggested_capabilities == ["tts"]

    def test_whisper_filename(self, tmp_path: Path) -> None:
        p = tmp_path / "whisper-small-q5.bin"
        p.write_bytes(b"")
        r = detect(p)
        assert r.suggested_capabilities == ["asr"]
        # An ASR filename routes to the moonshine provider — that's the
        # only ASR-capable backend hal0 ships.
        assert r.suggested_backends == ["moonshine"]
        assert r.kind == "moonshine"

    def test_bge_filename_embed(self, tmp_path: Path) -> None:
        p = tmp_path / "bge-large-en.safetensors"
        p.write_bytes(b"")
        r = detect(p)
        assert r.suggested_capabilities == ["embed"]

    def test_e5_filename_embed(self, tmp_path: Path) -> None:
        p = tmp_path / "e5-mistral-7b-instruct.safetensors"
        p.write_bytes(b"")
        r = detect(p)
        assert r.suggested_capabilities == ["embed"]

    def test_unknown_extension_returns_empty_caps(self, tmp_path: Path) -> None:
        p = tmp_path / "mystery.bin"
        p.write_bytes(b"")
        r = detect(p)
        assert r.suggested_capabilities == []
        assert r.suggested_backends == []
        assert r.confidence == "low"


class TestMr3ConsolidationPreservesDetectContract:
    """MR-3: routing detect's filename heuristic through the shared token
    table must not change detect's embed/asr/tts-only output vocabulary."""

    def test_chat_gguf_still_chat(self, tmp_path: Path) -> None:
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("llama")),
            ("llama.context_length", _GGUF_TYPE_UINT32, struct.pack("<I", 8192)),
        ]
        p = _write_fixture(tmp_path, "llama-3.1-8b.gguf", _build_gguf(3, kvs))
        assert detect(p).suggested_capabilities == ["chat"]

    def test_embed_gguf_still_embed(self, tmp_path: Path) -> None:
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("bert")),
            ("bert.context_length", _GGUF_TYPE_UINT32, struct.pack("<I", 512)),
            ("bert.pooling_type", _GGUF_TYPE_UINT32, struct.pack("<I", 2)),
        ]
        p = _write_fixture(tmp_path, "bge-small.gguf", _build_gguf(3, kvs))
        assert detect(p).suggested_capabilities == ["embed"]

    def test_reranker_is_not_emitted_by_detect(self, tmp_path: Path) -> None:
        """detect deliberately does NOT emit 'rerank' yet — a reranker gguf
        with no pooling_type is not forced to embed by the shared table (the
        'rerank' token is collapsed to None in detect's narrower contract)."""
        p = tmp_path / "bge-reranker-v2-m3.onnx"
        p.write_bytes(b"")
        # Non-gguf, rerank token → collapsed to None → unknown, empty caps.
        assert detect(p).suggested_capabilities == []

    def test_classify_multicap_rerank_preserved(self) -> None:
        from hal0.model_meta import classify

        assert classify("", capabilities=["chat", "rerank"]) == "rerank"


class TestMissingFile:
    def test_missing_path_returns_filename_heuristic(self, tmp_path: Path) -> None:
        # Detect on a path that doesn't exist; gguf extension → heuristic
        # still seeds gguf backends (file may show up by load time).
        p = tmp_path / "ghost-qwen3.gguf"
        r = detect(p)
        assert r.confidence == "low"
        assert set(r.suggested_backends) == {"vulkan", "rocm", "cuda", "cpu"}
        assert r.suggested_capabilities == ["chat"]


class TestRocmfpxQuant:
    """ROCmFPX-family quant detection (ciru-ai/ROCmFPX custom GGUF formats).

    These repacks carry a custom general.file_type code that is not an
    upstream LLAMA_FTYPE value, so quant must come from the filename's
    preset/family token and resolve to the canonical family label.
    """

    def test_pure_resolver_presets_and_families(self) -> None:
        from hal0.registry.detect import quant_from_rocmfpx_filename as q

        # Explicit GGUF presets → family label.
        assert q("model-Q4_0_ROCMFP4.gguf") == "ROCmFP4"
        assert q("model-Q4_0_ROCMFP4_FAST.gguf") == "ROCmFP4"
        assert q("model-Q3_0_ROCMFPX.gguf") == "ROCmFP3"
        assert q("model-Q6_0_ROCMFPX.gguf") == "ROCmFP6"
        assert q("model-Q8_0_ROCMFPX.gguf") == "ROCmFP8"
        # Bare family tokens (no explicit preset).
        assert q("CHADROCK3.6-27B-Coder-MTP-ROCmFP4-STRIX_LEAN.gguf") == "ROCmFP4"
        assert q("CHADROCK3.6-35B-A3B-Coder-MTP-ROCmFPX-MoEQuality-7.08BPW.gguf") == "ROCmFPX"
        # Non-ROCmFPX names → None (defer to the standard resolver).
        assert q("llama-7b-Q4_K_M.gguf") is None
        assert q("qwen3-embedding.gguf") is None

    def test_detect_prefers_family_over_unmapped_file_type(self, tmp_path: Path) -> None:
        # Custom/unknown file_type (1001) that quant_from_file_type can't map;
        # filename carries the ROCmFP4 family token → quant == "ROCmFP4".
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("qwen35")),
            ("general.file_type", _GGUF_TYPE_UINT32, struct.pack("<I", 1001)),
            ("qwen35.context_length", _GGUF_TYPE_UINT32, struct.pack("<I", 262144)),
        ]
        p = _write_fixture(
            tmp_path,
            "CHADROCK3.6-27B-Coder-MTP-ROCmFP4-STRIX_LEAN.gguf",
            _build_gguf(3, kvs),
        )
        r = detect(p)
        assert r.confidence == "high"
        assert r.quant == "ROCmFP4"

    def test_detect_moe_rocmfpx_umbrella(self, tmp_path: Path) -> None:
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("qwen35moe")),
            ("qwen35moe.context_length", _GGUF_TYPE_UINT32, struct.pack("<I", 65536)),
        ]
        p = _write_fixture(
            tmp_path,
            "CHADROCK3.6-35B-A3B-Coder-MTP-ROCmFPX-MoEQuality-7.08BPW.gguf",
            _build_gguf(3, kvs),
        )
        assert detect(p).quant == "ROCmFPX"
