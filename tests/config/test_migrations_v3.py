"""v3 — lift a stored extraction cap that cannot clear the plugin's 12000-char chunks (#3326)."""

from __future__ import annotations

import pytest

from hal0.config.migrations import MIGRATIONS, latest_version, run_migrations
from hal0.config.migrations.v3 import PLUGIN_RETAIN_CHUNK_SIZE, RAISED_EXTRACTION_MAX_TOKENS
from hal0.config.schema import MemoryGraphConfig


def _graph(value: object) -> dict:
    return {
        "meta": {"schema_version": 2},
        "memory": {"graph": {"extraction_max_tokens": value, "extraction_slot": "utility"}},
    }


def test_v3_is_registered() -> None:
    assert 3 in MIGRATIONS
    assert latest_version() >= 3


def test_raised_value_is_the_schema_default() -> None:
    assert MemoryGraphConfig().extraction_max_tokens == RAISED_EXTRACTION_MAX_TOKENS


@pytest.mark.parametrize("stored", [3072, 4096, PLUGIN_RETAIN_CHUNK_SIZE])
def test_cap_that_cannot_clear_the_plugin_chunk_is_raised(stored: int) -> None:
    out, version = run_migrations(_graph(stored), target_version=3)
    assert version == 3
    assert out["meta"]["schema_version"] == 3
    assert out["memory"]["graph"]["extraction_max_tokens"] == RAISED_EXTRACTION_MAX_TOKENS
    # Siblings in the table are untouched.
    assert out["memory"]["graph"]["extraction_slot"] == "utility"


@pytest.mark.parametrize("stored", [PLUGIN_RETAIN_CHUNK_SIZE + 1, 14000, 16384, 32768])
def test_cap_that_clears_the_plugin_chunk_is_left_alone(stored: int) -> None:
    out, _ = run_migrations(_graph(stored), target_version=3)
    assert out["memory"]["graph"]["extraction_max_tokens"] == stored


def test_missing_key_or_table_is_not_written() -> None:
    out, _ = run_migrations(
        {"meta": {"schema_version": 2}, "memory": {"graph": {}}}, target_version=3
    )
    assert out["memory"]["graph"] == {}
    out, _ = run_migrations({"meta": {"schema_version": 2}}, target_version=3)
    assert "memory" not in out


@pytest.mark.parametrize("stored", [True, "4096", 4096.0, None])
def test_malformed_value_is_left_for_the_validator(stored: object) -> None:
    out, _ = run_migrations(_graph(stored), target_version=3)
    assert out["memory"]["graph"]["extraction_max_tokens"] == stored


def test_input_is_not_mutated() -> None:
    data = _graph(4096)
    run_migrations(data, target_version=3)
    assert data["memory"]["graph"]["extraction_max_tokens"] == 4096


def test_v1_box_walks_through_v3() -> None:
    data = {"memory": {"graph": {"extraction_max_tokens": 4096}}}
    out, version = run_migrations(data, target_version=3)
    assert version == 3
    assert out["memory"]["graph"]["extraction_max_tokens"] == RAISED_EXTRACTION_MAX_TOKENS
