"""[memory.models] / [memory.decision] / [memory.lanes] / [memory.console] defaults.

These are the Hindsight 1.2 showcase config additions (PLAN.md §2) — default
values mirror the verified-working env captured on CT143
(``zz-showcase-1.2.conf``), and [memory.lanes] is the single rendered source
for the H0-98 global LLM concurrency cap.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.config.schema import MemoryConfig


def test_memory_config_defaults_include_showcase_sections() -> None:
    cfg = MemoryConfig()

    assert cfg.models.embeddings_provider == "local"
    assert cfg.models.embeddings_model == "hindsight-ai/hs-embed-1"
    assert cfg.models.reranker_model == "hindsight-ai/hs-rerank-1"
    assert cfg.models.reranker_bucket_batching is True

    assert cfg.decision.enabled is True
    # #3757: off until the opt-in memory-decision component is installed.
    assert cfg.decision.provider == "off"
    assert cfg.decision.model == "hs-decide-1-flash"
    assert cfg.decision.port == 9178

    assert cfg.lanes.llm_max_concurrent is None  # follows [memory.graph] (#3758)
    for op in ("retain", "consolidation", "reflect", "mental_model_refresh"):
        lane = getattr(cfg.lanes, op)
        assert lane.max_concurrent is None  # falls back to the global cap

    assert cfg.console.enabled is False
    assert cfg.console.port == 9999


def test_memory_decision_provider_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        MemoryConfig(decision={"provider": "bogus"})


def test_memory_lanes_per_op_override_round_trips() -> None:
    cfg = MemoryConfig(
        lanes={
            "llm_max_concurrent": 2,
            "reflect": {"max_concurrent": 1, "virtual_model": "hal0/utility"},
        }
    )
    assert cfg.lanes.llm_max_concurrent == 2
    assert cfg.lanes.reflect.max_concurrent == 1
    assert cfg.lanes.reflect.virtual_model == "hal0/utility"
    assert cfg.lanes.consolidation.max_concurrent is None


def test_memory_config_round_trips_through_model_dump() -> None:
    cfg = MemoryConfig()
    dumped = cfg.model_dump(mode="json")
    restored = MemoryConfig(**dumped)
    assert restored == cfg


def test_reflect_wall_timeout_default_and_bounds() -> None:
    assert MemoryConfig().lanes.reflect.wall_timeout_s is None  # resolves to 1800 at render
    assert (
        MemoryConfig(lanes={"reflect": {"wall_timeout_s": 7200}}).lanes.reflect.wall_timeout_s
        == 7200
    )
    for bad in (0, 59, 86401):
        with pytest.raises(ValidationError):
            MemoryConfig(lanes={"reflect": {"wall_timeout_s": bad}})


def test_consolidation_batch_size_default_and_bounds() -> None:
    assert MemoryConfig().lanes.consolidation.batch_size is None  # resolves to 4 at render
    assert (
        MemoryConfig(lanes={"consolidation": {"batch_size": 8}}).lanes.consolidation.batch_size == 8
    )
    for bad in (0, 33):
        with pytest.raises(ValidationError):
            MemoryConfig(lanes={"consolidation": {"batch_size": bad}})
