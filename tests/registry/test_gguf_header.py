"""Unit tests for hal0.registry.gguf_header.

We fabricate minimal GGUF headers in-process — no need to vendor real
model files. The fabrication helpers mirror the wire format described
in :mod:`hal0.registry.gguf_header`.
"""

from __future__ import annotations

import struct
from pathlib import Path

from hal0.registry.gguf_header import (
    _GGUF_TYPE_BOOL,
    _GGUF_TYPE_FLOAT32,
    _GGUF_TYPE_STRING,
    _GGUF_TYPE_UINT32,
    read_gguf_header,
)

# ── fixture builders ─────────────────────────────────────────────────────


def _enc_str(s: str) -> bytes:
    raw = s.encode("utf-8")
    return struct.pack("<Q", len(raw)) + raw


def _enc_kv(key: str, vtype: int, value_bytes: bytes) -> bytes:
    return _enc_str(key) + struct.pack("<I", vtype) + value_bytes


def _build_gguf(version: int, kvs: list[tuple[str, int, bytes]], tensor_count: int = 0) -> bytes:
    """Assemble a GGUF header with the given KV list.

    Tensor info / data sections are omitted — the parser stops after the
    KV block, so trailing bytes don't matter for these tests.
    """
    out = bytearray()
    out += b"GGUF"
    out += struct.pack("<I", version)
    out += struct.pack("<Q", tensor_count)
    out += struct.pack("<Q", len(kvs))
    for key, vtype, vbytes in kvs:
        out += _enc_kv(key, vtype, vbytes)
    return bytes(out)


def _write_fixture(tmp_path: Path, name: str, payload: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(payload)
    return p


# ── tests ────────────────────────────────────────────────────────────────


class TestMagicAndShape:
    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        assert read_gguf_header(tmp_path / "nope.gguf") is None

    def test_returns_none_for_non_gguf(self, tmp_path: Path) -> None:
        p = _write_fixture(tmp_path, "junk.bin", b"NOTGGUF" + b"\x00" * 64)
        assert read_gguf_header(p) is None

    def test_returns_none_for_too_small(self, tmp_path: Path) -> None:
        p = _write_fixture(tmp_path, "tiny.gguf", b"GGUF\x03")
        assert read_gguf_header(p) is None

    def test_extracts_version_and_tensor_count(self, tmp_path: Path) -> None:
        payload = _build_gguf(version=3, kvs=[], tensor_count=42)
        p = _write_fixture(tmp_path, "empty.gguf", payload)
        out = read_gguf_header(p)
        assert out is not None
        assert out["version"] == 3
        assert out["tensor_count"] == 42


class TestArchitectureAndContextLength:
    def test_llama_arch_with_context_length(self, tmp_path: Path) -> None:
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("llama")),
            ("llama.context_length", _GGUF_TYPE_UINT32, struct.pack("<I", 8192)),
            ("llama.embedding_length", _GGUF_TYPE_UINT32, struct.pack("<I", 4096)),
        ]
        p = _write_fixture(tmp_path, "llama.gguf", _build_gguf(3, kvs))
        out = read_gguf_header(p)
        assert out is not None
        assert out["general.architecture"] == "llama"
        assert out["llama.context_length"] == 8192
        assert out["context_length"] == 8192  # promoted alias

    def test_qwen_arch_promotes_alias(self, tmp_path: Path) -> None:
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("qwen3")),
            ("qwen3.context_length", _GGUF_TYPE_UINT32, struct.pack("<I", 32768)),
        ]
        p = _write_fixture(tmp_path, "qwen3.gguf", _build_gguf(3, kvs))
        out = read_gguf_header(p)
        assert out is not None
        assert out["context_length"] == 32768

    def test_pooling_type_promoted(self, tmp_path: Path) -> None:
        # bge embed model: pooling_type=2 (CLS).
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("bert")),
            ("bert.context_length", _GGUF_TYPE_UINT32, struct.pack("<I", 512)),
            ("bert.pooling_type", _GGUF_TYPE_UINT32, struct.pack("<I", 2)),
        ]
        p = _write_fixture(tmp_path, "bge.gguf", _build_gguf(3, kvs))
        out = read_gguf_header(p)
        assert out is not None
        assert out["pooling_type"] == 2
        assert out["context_length"] == 512

    def test_attention_causal_promoted_when_arch_comes_first(self, tmp_path: Path) -> None:
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("jina-bert-v2")),
            ("jina-bert-v2.attention.causal", _GGUF_TYPE_BOOL, struct.pack("<B", 0)),
        ]
        p = _write_fixture(tmp_path, "jina.gguf", _build_gguf(3, kvs))
        out = read_gguf_header(p)
        assert out is not None
        assert out["attention_causal"] is False

    def test_attention_causal_promoted_when_arch_comes_after(self, tmp_path: Path) -> None:
        """#1838 (codex review): GGUF doesn't guarantee KV ordering — a
        header whose <arch>.attention.causal entry precedes
        general.architecture must still get promoted, the same way
        <arch>.context_length already does via the speculative capture."""
        kvs = [
            ("jina-bert-v2.attention.causal", _GGUF_TYPE_BOOL, struct.pack("<B", 0)),
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("jina-bert-v2")),
        ]
        p = _write_fixture(tmp_path, "jina-reordered.gguf", _build_gguf(3, kvs))
        out = read_gguf_header(p)
        assert out is not None
        assert out["attention_causal"] is False

    def test_pooling_type_promoted_when_arch_comes_after(self, tmp_path: Path) -> None:
        """Same ordering gap as attention.causal, for pooling_type."""
        kvs = [
            ("bert.pooling_type", _GGUF_TYPE_UINT32, struct.pack("<I", 4)),
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("bert")),
        ]
        p = _write_fixture(tmp_path, "reranker-reordered.gguf", _build_gguf(3, kvs))
        out = read_gguf_header(p)
        assert out is not None
        assert out["pooling_type"] == 4


class TestSkipping:
    def test_skips_unwanted_string_kv(self, tmp_path: Path) -> None:
        kvs = [
            ("general.name", _GGUF_TYPE_STRING, _enc_str("Qwen3 4B Instruct")),
            ("general.license", _GGUF_TYPE_STRING, _enc_str("apache-2.0")),
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("qwen3")),
            ("qwen3.context_length", _GGUF_TYPE_UINT32, struct.pack("<I", 4096)),
        ]
        p = _write_fixture(tmp_path, "noisy.gguf", _build_gguf(4, kvs))
        out = read_gguf_header(p)
        assert out is not None
        # general.name is now collected (interest list expanded so the UI
        # can surface a human-readable model name).
        assert out["general.name"] == "Qwen3 4B Instruct"
        # general.license is NOT in the interest list — still skipped.
        assert "general.license" not in out
        assert out["context_length"] == 4096

    def test_skips_scalar_kv(self, tmp_path: Path) -> None:
        kvs = [
            ("general.architecture", _GGUF_TYPE_STRING, _enc_str("llama")),
            ("general.file_type", _GGUF_TYPE_UINT32, struct.pack("<I", 15)),
            ("general.quantization_version", _GGUF_TYPE_UINT32, struct.pack("<I", 2)),
            ("llama.context_length", _GGUF_TYPE_UINT32, struct.pack("<I", 2048)),
            ("llama.rope.freq_base", _GGUF_TYPE_FLOAT32, struct.pack("<f", 1000000.0)),
        ]
        p = _write_fixture(tmp_path, "scalars.gguf", _build_gguf(3, kvs))
        out = read_gguf_header(p)
        assert out is not None
        assert out["context_length"] == 2048


class TestMalformed:
    def test_truncated_after_kv_count(self, tmp_path: Path) -> None:
        """Header claims 5 KVs but file ends before any are present."""
        payload = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", 5)
        p = _write_fixture(tmp_path, "trunc.gguf", payload)
        out = read_gguf_header(p)
        # Magic + counts parsed; KV walk failed mid-way — we still return
        # a dict (partial-OK) carrying version + tensor_count.
        assert out is not None
        assert out["version"] == 3
        assert out["tensor_count"] == 0
        # No KV survived, so no context_length / arch promotion.
        assert "context_length" not in out

    def test_truncated_mid_kv_returns_partial(self, tmp_path: Path) -> None:
        """First KV parses, second is truncated."""
        good_kv = _enc_kv("general.architecture", _GGUF_TYPE_STRING, _enc_str("llama"))
        # Start a key but cut off the length bytes mid-stream.
        bad_partial = struct.pack("<Q", 99999)[:4]
        header = (
            b"GGUF"
            + struct.pack("<I", 3)
            + struct.pack("<Q", 0)
            + struct.pack("<Q", 2)
            + good_kv
            + bad_partial
        )
        p = _write_fixture(tmp_path, "partial.gguf", header)
        out = read_gguf_header(p)
        assert out is not None
        assert out["general.architecture"] == "llama"
