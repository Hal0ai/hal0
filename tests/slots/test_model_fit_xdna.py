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

from hal0.slots.model_fit import xdna_fit_cache_clear, xdna_model_fit, xdna_model_fit_cached
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
    assert fit.reason == "blk.0.ssm_out.weight is Q4_K, not Q5_K"


def test_per_op_names_q8_0_rather_than_its_type_id(tmp_path: Path) -> None:
    kw = dict(_FUSED_KW)
    kw["tensor_types"] = {"blk.0.ssm_out.weight": 8}  # Q8_0
    path = write_gguf(tmp_path / "m.gguf", "qwen35", **kw)
    assert xdna_model_fit(path).reason == "blk.0.ssm_out.weight is Q8_0, not Q5_K"


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


def test_cached_fit_reads_the_header_once_per_file_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from hal0.registry import gguf_header

    xdna_fit_cache_clear()
    path = write_gguf(tmp_path / "m.gguf", "qwen35", **_FUSED_KW)
    real = gguf_header.read_gguf_header
    calls = {"n": 0}

    def counting(p):
        calls["n"] += 1
        return real(p)

    monkeypatch.setattr(gguf_header, "read_gguf_header", counting)
    assert xdna_model_fit_cached(path).xdna_path == "fused"
    assert xdna_model_fit_cached(path).xdna_path == "fused"
    assert calls["n"] == 1
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    xdna_model_fit_cached(path)
    assert calls["n"] == 2
    xdna_fit_cache_clear()


def test_cached_fit_still_raises_for_non_gguf(tmp_path: Path) -> None:
    xdna_fit_cache_clear()
    path = tmp_path / "m.hgn"
    path.write_bytes(b"not a gguf file" * 4)
    with pytest.raises(ValueError, match="GGUF"):
        xdna_model_fit_cached(path)


# ── advisory npu_ok / npu_note (ADR-0057 amendment) ────────────────────────

_GIB = 1024**3


def _sparse(path: Path, size: int) -> Path:
    with open(path, "r+b") as f:
        f.truncate(size)
    return path


def test_small_text_model_is_npu_ok(tmp_path: Path) -> None:
    fit = xdna_model_fit(write_gguf(tmp_path / "m.gguf", "lfm2"))
    assert fit.npu_ok is True
    assert fit.npu_note is None


def test_fused_model_is_npu_ok(tmp_path: Path) -> None:
    fit = xdna_model_fit(write_gguf(tmp_path / "m.gguf", "qwen35", **_FUSED_KW))
    assert (fit.xdna_path, fit.npu_ok, fit.npu_note) == ("fused", True, None)


def test_at_the_ceiling_is_ok_and_above_is_not(tmp_path: Path) -> None:
    at = _sparse(write_gguf(tmp_path / "at.gguf", "llama"), 6 * _GIB)
    over = _sparse(write_gguf(tmp_path / "over.gguf", "llama"), 6 * _GIB + 1)
    assert xdna_model_fit(at).npu_ok is True
    fit = xdna_model_fit(over)
    assert fit.npu_ok is False
    assert "advisory" in (fit.npu_note or "")
    # advisory only: the path classification is unchanged, never refused.
    assert fit.xdna_path == "per-op"


def test_large_moe_is_not_npu_ok_by_total_file_size(tmp_path: Path) -> None:
    path = _sparse(write_gguf(tmp_path / "moe.gguf", "qwen35moe"), 22 * _GIB)
    fit = xdna_model_fit(path)
    assert fit.npu_ok is False
    assert fit.xdna_path == "per-op"


@pytest.mark.parametrize("arch", ["bert", "nomic-bert", "jina-bert-v2", "modern-bert"])
def test_encoder_architectures_are_not_npu_ok(tmp_path: Path, arch: str) -> None:
    fit = xdna_model_fit(write_gguf(tmp_path / "m.gguf", arch))
    assert fit.npu_ok is False
    assert "not a text-generation" in (fit.npu_note or "")
    assert fit.xdna_path == "per-op"


def test_pooled_decoder_embedding_model_is_not_npu_ok(tmp_path: Path) -> None:
    path = write_gguf(tmp_path / "m.gguf", "qwen3", uints={"qwen3.pooling_type": 3})
    assert xdna_model_fit(path).npu_ok is False


def test_pooling_none_is_still_text_generation(tmp_path: Path) -> None:
    path = write_gguf(tmp_path / "m.gguf", "llama", uints={"llama.pooling_type": 0})
    assert xdna_model_fit(path).npu_ok is True


# ── architectures the runner's llama.cpp build cannot load ─────────────────


def test_unknown_architecture_cannot_load(tmp_path: Path) -> None:
    fit = xdna_model_fit(write_gguf(tmp_path / "m.gguf", "lfm3"))
    assert fit.xdna_path is None
    assert fit.reason == "the xdna runner's llama.cpp build has no 'lfm3' architecture"
    assert fit.npu_ok is False
    assert fit.npu_note == fit.reason


def test_lfm2_still_loads_per_op(tmp_path: Path) -> None:
    fit = xdna_model_fit(write_gguf(tmp_path / "m.gguf", "lfm2"))
    assert fit.xdna_path == "per-op"
    assert fit.npu_ok is True


def test_fused_unchanged_by_arch_check(tmp_path: Path) -> None:
    fit = xdna_model_fit(write_gguf(tmp_path / "m.gguf", "qwen35", **_FUSED_KW))
    assert fit.xdna_path == "fused"


def test_known_arch_table_is_the_pinned_fork_set() -> None:
    from hal0.slots.xdna_archs import XDNA_KNOWN_ARCHS

    # Regenerate with scripts/gen_xdna_archs.py when the runner pin moves.
    assert {"llama", "qwen35", "lfm2", "lfm2moe"} <= XDNA_KNOWN_ARCHS
    assert "lfm3" not in XDNA_KNOWN_ARCHS
    assert "(unknown)" not in XDNA_KNOWN_ARCHS


def test_slot_guard_refuses_unknown_arch_on_xdna_slot_only(tmp_path: Path) -> None:
    from hal0.slots.model_fit import xdna_slot_model_unloadable

    registry = {"lfm3-x": {"path": str(write_gguf(tmp_path / "m.gguf", "lfm3"))}}

    class Reg:
        def get(self, mid: str):
            return registry.get(mid)

    xdna = {"binary": "xdna", "device": "npu"}
    msg = xdna_slot_model_unloadable("npu-ggml", xdna, "lfm3-x", Reg())
    assert msg is not None
    assert "lfm3-x" in msg and "'lfm3'" in msg
    # Not an xdna slot, unknown model, unreadable file, known arch: never judged.
    assert xdna_slot_model_unloadable("chat", {"device": "gpu-rocm"}, "lfm3-x", Reg()) is None
    assert xdna_slot_model_unloadable("npu-ggml", xdna, "nope", Reg()) is None
    bad = tmp_path / "bad.gguf"
    bad.write_bytes(b"junk" * 8)
    registry["bad"] = {"path": str(bad)}
    assert xdna_slot_model_unloadable("npu-ggml", xdna, "bad", Reg()) is None
    registry["ok"] = {"path": str(write_gguf(tmp_path / "ok.gguf", "lfm2"))}
    assert xdna_slot_model_unloadable("npu-ggml", xdna, "ok", Reg()) is None
