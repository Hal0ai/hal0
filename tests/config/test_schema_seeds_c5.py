"""Tests for Phase C5 — rerank + utility seed TOMLs and reranker defaults."""

import tomllib
from pathlib import Path

from hal0.config.schema import MemoryEmbeddingConfig, SlotConfig

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEEDED_SLOTS_DIR = _REPO_ROOT / "installer" / "etc-hal0" / "slots"


def test_seed_rerank_toml_validates() -> None:
    raw = tomllib.loads((_SEEDED_SLOTS_DIR / "rerank.toml").read_text(encoding="utf-8"))
    slot = SlotConfig.model_validate(raw)
    assert slot.runtime == "container"
    assert slot.profile == "vulkan"
    assert slot.device == "gpu-vulkan"
    assert slot.port == 8083
    assert "--reranking" in (slot.server.extra_args or "")
    assert slot.model.default == "bge-reranker-v2-m3-q4_k_m"


def test_seed_utility_toml_validates() -> None:
    raw = tomllib.loads((_SEEDED_SLOTS_DIR / "utility.toml").read_text(encoding="utf-8"))
    slot = SlotConfig.model_validate(raw)
    assert slot.runtime == "container"
    assert slot.profile == "vulkan"
    assert slot.device == "gpu-vulkan"
    assert slot.port == 8081
    assert slot.model.default == "gemma-4-12b-it"
    assert slot.model.context_size == 65536


def test_rerank_defaults_are_hindsight_era() -> None:
    cfg = MemoryEmbeddingConfig()
    # Gateway = hal0-api's own OpenAI surface; the dispatcher routes
    # /v1/rerankings to the rerank slot.
    assert cfg.rerank_gateway_url == "http://127.0.0.1:8080"
    assert cfg.rerank_model == "builtin.jina-reranker-v1-tiny-en-q8"


def test_cognee_era_embedding_keys_are_dropped_silently() -> None:
    # extra="ignore": a stale hal0.toml carrying the removed cognee-era keys
    # must load cleanly and shed them (gone on next save) instead of failing
    # validation — same migration path ADR-0023 used for [memory.graph].
    cfg = MemoryEmbeddingConfig.model_validate(
        {
            "model": "BAAI/bge-small-en-v1.5",
            "rerank_enabled": True,
            "rerank_url": "http://127.0.0.1:8083",
            "rerank_over_fetch_factor": 5,
            "rerank_max_candidates": 500,
        }
    )
    dumped = cfg.model_dump()
    for dead in ("model", "rerank_enabled", "rerank_url", "rerank_over_fetch_factor"):
        assert dead not in dumped
