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
    # Clean seed (WS-E, #1107): no model pin — boots grey, no surprise download.
    assert slot.model.default == ""
    assert slot.enabled is False


def test_seed_utility_toml_validates() -> None:
    raw = tomllib.loads((_SEEDED_SLOTS_DIR / "utility.toml").read_text(encoding="utf-8"))
    slot = SlotConfig.model_validate(raw)
    assert slot.runtime == "container"
    assert slot.profile == "vulkan"
    assert slot.device == "gpu-vulkan"
    # 8090: 8081 was reclaimed as the `agent` seed's canonical primary port
    # (ADR-0023 LLM anchor); utility moved off it on new installs.
    assert slot.port == 8090
    # Clean seed (WS-E, #1107): the ghost id `gemma-4-12b-it` pin is gone — the
    # slot boots grey (no crash-loop, no surprise download). context_size is a
    # tuning default that applies once the operator assigns a model.
    assert slot.model.default == ""
    assert slot.model.context_size == 65536
    assert slot.enabled is False


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
