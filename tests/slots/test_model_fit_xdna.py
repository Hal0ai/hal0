"""xdna model fit: does a GGUF model take the fused SSM path or per-op.

``xdna_model_fit`` classifies a GGUF checkpoint for the optional ``xdna``
(ggml-xdna NPU) runner, per docs/adr (ggml-xdna optional NPU runner) and
docs/superpowers/plans/xdna-runner.md. ``fused`` requires all three of:
Qwen3.5 architecture, embedding_length 1024, and the ``blk.0.ssm_out.weight``
tensor requantized to Q5_K (the correctness-bearing tensor from the spike);
anything else is ``per-op``. A non-GGUF path is refused outright -- xdna fit
has nothing to say about it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.slots.model_fit import xdna_model_fit
from tests.registry.gguf_fixtures import write_gguf

_FUSED_KW = dict(
    uints={"qwen35.embedding_length": 1024},
    tensor_names=["blk.0.ffn_down.weight", "blk.0.ssm_out.weight"],
    tensor_types={"blk.0.ssm_out.weight": 13},  # Q5_K
)


def test_fused_when_arch_embed_len_and_tensor_type_all_match(tmp_path: Path) -> None:
    path = write_gguf(tmp_path / "m.gguf", "qwen35", **_FUSED_KW)
    fit = xdna_model_fit(path)
    assert fit.xdna_path == "fused"
    assert "qwen35" in fit.reason
    assert "Q5_K" in fit.reason


def test_per_op_when_architecture_is_not_qwen35(tmp_path: Path) -> None:
    kw = dict(_FUSED_KW)
    path = write_gguf(tmp_path / "m.gguf", "llama", **kw)
    fit = xdna_model_fit(path)
    assert fit.xdna_path == "per-op"
    assert "llama" in fit.reason


def test_per_op_when_embedding_length_is_not_1024(tmp_path: Path) -> None:
    kw = dict(_FUSED_KW)
    kw["uints"] = {"qwen35.embedding_length": 2048}
    path = write_gguf(tmp_path / "m.gguf", "qwen35", **kw)
    fit = xdna_model_fit(path)
    assert fit.xdna_path == "per-op"
    assert "2048" in fit.reason


def test_per_op_when_ssm_out_tensor_is_not_q5_k(tmp_path: Path) -> None:
    kw = dict(_FUSED_KW)
    kw["tensor_types"] = {"blk.0.ssm_out.weight": 12}  # Q4_K, not Q5_K
    path = write_gguf(tmp_path / "m.gguf", "qwen35", **kw)
    fit = xdna_model_fit(path)
    assert fit.xdna_path == "per-op"
    assert "ssm_out" in fit.reason


def test_per_op_when_ssm_out_tensor_is_missing(tmp_path: Path) -> None:
    kw = dict(_FUSED_KW)
    kw["tensor_names"] = ["blk.0.ffn_down.weight"]
    path = write_gguf(tmp_path / "m.gguf", "qwen35", **kw)
    fit = xdna_model_fit(path)
    assert fit.xdna_path == "per-op"
    assert "ssm_out" in fit.reason


def test_non_gguf_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "m.hgn"
    path.write_bytes(b"not a gguf file" * 4)
    with pytest.raises(ValueError, match="GGUF"):
        xdna_model_fit(path)
