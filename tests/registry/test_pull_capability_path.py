"""Capability-grouped pull paths + meta.json sidecar (FirstRun v2, design D2)."""

from __future__ import annotations

import json

from hal0.registry import pull


def test_final_path_grouped_by_capability(tmp_path, monkeypatch):
    monkeypatch.setattr(pull, "_pull_root", lambda: tmp_path)
    # No capability → legacy flat layout (back-compat).
    flat = pull._final_path_for_entry("qwen3.6-27b", "Q4.gguf", None, None)
    assert flat == tmp_path / "qwen3.6-27b" / "Q4.gguf"
    # Capability set → grouped + canonical model.gguf filename.
    grouped = pull._final_path_for_entry("qwen3.6-27b", "Q4.gguf", None, "chat")
    assert grouped == tmp_path / "chat" / "qwen3.6-27b" / "model.gguf"


def test_write_model_meta_sidecar(tmp_path):
    dest = tmp_path / "chat" / "qwen3.6-27b" / "model.gguf"
    dest.parent.mkdir(parents=True)
    pull.write_model_meta(
        dest,
        curated_id="qwen3.6-27b",
        hf_repo="Qwen/Qwen3.6-27B-GGUF",
        hf_file="Q4.gguf",
        sha256="abc123",
        size_bytes=42,
        quant="Q4_K_M",
        capability="chat",
    )
    meta = json.loads((dest.parent / "meta.json").read_text())
    assert meta["curated_id"] == "qwen3.6-27b"
    assert meta["hf_repo"] == "Qwen/Qwen3.6-27B-GGUF"
    assert meta["sha256"] == "abc123"
    assert meta["capability"] == "chat"
