"""Drafter classification and target-fit checks from GGUF header dicts (ADR-0031 §2)."""

from __future__ import annotations

from typing import Any

import pytest

from hal0.errors import BadRequest
from hal0.registry.drafters import (
    DrafterInfo,
    classify_drafter,
    draft_fit_errors,
    screen_draft_fit,
)

LILICORR: dict[str, Any] = {
    "general.architecture": "dflash",
    "dflash.embedding_length": 4096,
    "dflash.block_size": 8,
    "dflash.target_layers": [2, 19, 36],
    "lilicorr.candidate_topk": 4,
}

NEMOTRON: dict[str, Any] = {
    "general.architecture": "nemotron_h_moe",
    "nemotron_h_moe.embedding_length": 4096,
    "nemotron_h_moe.block_count": 90,
}

QWEN3: dict[str, Any] = {
    "general.architecture": "qwen3",
    "qwen3.embedding_length": 4096,
    "qwen3.block_count": 36,
    "qwen3.vocab_size": 151936,
    "tokenizer.ggml.model": "gpt2",
    "tokenizer.ggml.bos_token_id": 11,
    "tokenizer.ggml.eos_token_id": 12,
}

EAGLE3: dict[str, Any] = {
    "general.architecture": "eagle3",
    "eagle3.target_hidden_size": 4096,
    "eagle3.target_layers": [2, 18, 33],
    "eagle3.vocab_size": 151936,
}


def _info(header: dict[str, Any]) -> DrafterInfo:
    info = classify_drafter(header)
    assert info is not None
    return info


class TestClassify:
    def test_classify_lilicorr(self) -> None:
        info = _info(LILICORR)
        assert info.kind == "dflash"
        assert info.variant == "lilicorr"
        assert info.block_size == 8
        assert info.embedding_length == 4096
        assert info.target_layers == [2, 19, 36]
        assert info.architecture == "dflash"

    def test_classify_dflash2_and_dspark(self) -> None:
        base = {k: v for k, v in LILICORR.items() if k != "lilicorr.candidate_topk"}
        assert _info({**base, "dflash.selector_top_k": 16}).variant == "dflash2"
        dspark = {**base, "tensor_names_prefix": ["fc.weight", "markov_w1.weight"]}
        assert _info(dspark).variant == "dspark"
        plain = _info(base)
        assert (plain.kind, plain.variant) == ("dflash", "dflash")

    def test_classify_eagle3(self) -> None:
        info = _info(EAGLE3)
        assert info.kind == "eagle3"
        assert info.embedding_length == 4096
        assert info.target_layers == [2, 18, 33]
        assert info.vocab_size == 151936

    def test_classify_mtp_sidecar(self) -> None:
        header = {
            "general.architecture": "qwen35",
            "qwen35.nextn_predict_layers": 1,
            "tensor_names_prefix": ["blk.64.nextn.eh_proj.weight"],
        }
        assert _info(header).kind == "mtp"

    def test_full_model_with_nextn_layers_is_not_a_sidecar(self) -> None:
        header = {
            **QWEN3,
            "qwen3.nextn_predict_layers": 1,
            "tensor_names_prefix": ["token_embd.weight", "blk.0.attn_q.weight"],
        }
        assert _info(header).kind == "simple"

    def test_classify_simple(self) -> None:
        assert _info(QWEN3).kind == "simple"

    def test_classify_mmproj_is_none(self) -> None:
        assert classify_drafter({"general.architecture": "clip"}) is None

    def test_no_architecture_is_none(self) -> None:
        assert classify_drafter({}) is None

    def test_as_metadata_drops_none_keys(self) -> None:
        meta = _info(LILICORR).as_metadata()
        assert meta == {
            "variant": "lilicorr",
            "block_size": 8,
            "embedding_length": 4096,
            "target_layers": [2, 19, 36],
        }
        with_base = _info({**EAGLE3, "general.base_model.0.name": "Qwen3-8B"}).as_metadata()
        assert with_base["base_model"] == "Qwen3-8B"
        assert "block_size" not in with_base


class TestFit:
    def test_fit_dflash_ok(self) -> None:
        assert draft_fit_errors(NEMOTRON, _info(LILICORR)) == []

    def test_fit_dflash_embedding_mismatch(self) -> None:
        target = {**NEMOTRON, "nemotron_h_moe.embedding_length": 5120}
        errs = draft_fit_errors(target, _info(LILICORR))
        assert len(errs) == 1
        assert "embedding_length" in errs[0]

    def test_fit_dflash_target_layers_beyond_block_count(self) -> None:
        target = {**NEMOTRON, "nemotron_h_moe.block_count": 30}
        errs = draft_fit_errors(target, _info(LILICORR))
        assert len(errs) == 1
        assert "target_layers" in errs[0]

    def test_fit_eagle3_ok(self) -> None:
        assert draft_fit_errors(QWEN3, _info(EAGLE3)) == []

    def test_fit_eagle3_vocab_mismatch(self) -> None:
        target = {**QWEN3, "qwen3.vocab_size": 128256}
        errs = draft_fit_errors(target, _info(EAGLE3))
        assert len(errs) == 1
        assert "vocab_size" in errs[0]

    def test_fit_mtp_wrong_arch(self) -> None:
        sidecar = {
            "general.architecture": "qwen35",
            "qwen35.nextn_predict_layers": 1,
            "tensor_names_prefix": ["blk.64.nextn.eh_proj.weight"],
        }
        errs = draft_fit_errors(QWEN3, _info(sidecar))
        assert len(errs) == 1
        assert "architecture" in errs[0]
        target = {"general.architecture": "qwen35"}
        assert draft_fit_errors(target, _info(sidecar)) == []

    def test_fit_simple_ok(self) -> None:
        small = {**QWEN3, "qwen3.vocab_size": 151936 - 128}
        assert draft_fit_errors(QWEN3, _info(small)) == []

    def test_fit_simple_tokenizer_mismatch(self) -> None:
        other = {**QWEN3, "tokenizer.ggml.bos_token_id": 1}
        errs = draft_fit_errors(QWEN3, _info(other))
        assert len(errs) == 1
        assert "bos_token_id" in errs[0]

    def test_fit_simple_vocab_delta_over_128(self) -> None:
        other = {**QWEN3, "qwen3.vocab_size": 151936 - 129}
        errs = draft_fit_errors(QWEN3, _info(other))
        assert len(errs) == 1
        assert "vocab_size" in errs[0]


class TestScreen:
    def test_screen_returns_info_when_fit(self) -> None:
        info = screen_draft_fit(NEMOTRON, LILICORR)
        assert info.variant == "lilicorr"

    def test_classify_plain_llm_with_other_tokenizer_is_refused(self) -> None:
        llama_plain = {
            "general.architecture": "llama",
            "llama.vocab_size": 128256,
            "tokenizer.ggml.model": "llama",
            "tokenizer.ggml.bos_token_id": 1,
            "tokenizer.ggml.eos_token_id": 2,
        }
        with pytest.raises(BadRequest) as exc:
            screen_draft_fit(QWEN3, llama_plain)
        assert exc.value.code == "model.draft_mismatch"
        assert exc.value.details["reasons"]

    def test_screen_mmproj_is_not_a_drafter(self) -> None:
        with pytest.raises(BadRequest) as exc:
            screen_draft_fit(QWEN3, {"general.architecture": "clip"})
        assert exc.value.code == "model.draft_not_a_drafter"
