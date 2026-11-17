"""Pure helpers for the per-model upstream catalog."""

from __future__ import annotations

from dataclasses import replace

from hal0.upstreams.catalog import (
    MODEL_TYPES,
    UpstreamModel,
    canonical_id,
    enabled_ids,
    split_ref,
)
from hal0.upstreams.registry import Upstream


def test_model_types_vocabulary() -> None:
    assert frozenset({"chat", "embed", "rerank", "tts", "stt", "image"}) == MODEL_TYPES


def test_canonical_id_joins_provider_and_model() -> None:
    assert canonical_id("openrouter", "qwen/qwen3-coder") == "openrouter/qwen/qwen3-coder"


def test_split_ref_first_slash_only() -> None:
    assert split_ref("openrouter/qwen/qwen3-coder") == ("openrouter", "qwen/qwen3-coder")
    assert split_ref("MiniMax-M2.5") is None
    assert split_ref("/x") is None and split_ref("x/") is None


def test_enabled_ids_only_for_curated() -> None:
    u = Upstream(
        name="mm",
        kind="remote",
        url="https://x/v1",
        curated=True,
        models=(UpstreamModel("a"), UpstreamModel("b", enabled=False)),
    )
    assert enabled_ids(u) == ("a",)
    assert enabled_ids(replace(u, curated=False)) == ()


def test_upstream_model_defaults() -> None:
    m = UpstreamModel("a")
    assert (m.enabled, m.type, m.context, m.price_in, m.price_out, m.price_unit) == (
        True,
        "chat",
        None,
        None,
        None,
        "Mtok",
    )
