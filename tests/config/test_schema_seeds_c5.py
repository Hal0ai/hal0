"""Tests for Phase C5 — rerank + utility seed TOMLs and rerank_url default."""

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


def test_rerank_url_default_is_rerank_slot() -> None:
    assert MemoryEmbeddingConfig().rerank_url == "http://127.0.0.1:8083"
