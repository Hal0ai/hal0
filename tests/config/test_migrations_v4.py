"""v4 preserves extraction targets and opts existing installations out of priority."""

from copy import deepcopy

import pytest

from hal0.config.migrations import MIGRATIONS, run_migrations
from hal0.config.schema import Hal0Config


@pytest.mark.parametrize("memory", [{}, {"graph": {}}, {"priority": {"quiet_gap_s": 12}}])
def test_upgrade_writes_missing_values_without_enabling_priority(memory: dict) -> None:
    data = {"meta": {"schema_version": 3}, "memory": memory}
    original = deepcopy(data)
    out, version = run_migrations(data, target_version=4)
    assert version == 4
    assert out["memory"]["graph"]["extraction_slot"] == "utility"
    assert out["memory"]["priority"]["enabled"] is False
    assert out["memory"]["priority"].get("quiet_gap_s") == memory.get("priority", {}).get(
        "quiet_gap_s"
    )
    assert data == original
    assert Hal0Config.model_validate(out).memory.priority.enabled is False


def test_upgrade_with_no_memory_table() -> None:
    out, _ = run_migrations({"meta": {"schema_version": 3}}, target_version=4)
    assert out["memory"] == {
        "graph": {"extraction_slot": "utility"},
        "priority": {"enabled": False},
    }


@pytest.mark.parametrize("slot", ["agent", "memory", "utility", "custom"])
@pytest.mark.parametrize("enabled", [True, False])
def test_explicit_targets_and_priority_values_are_preserved(slot: str, enabled: bool) -> None:
    data = {
        "meta": {"schema_version": 3},
        "memory": {
            "graph": {
                "extraction_slot": slot,
                "extraction_model": "vendor/model",
                "llm_timeout_s": 600,
            },
            "priority": {"enabled": enabled, "quiet_gap_s": 45, "max_defer_s": 500},
        },
        "telemetry": {"enabled": False},
    }
    original = deepcopy(data)
    out, _ = run_migrations(data, target_version=4)
    assert out["memory"] == original["memory"]
    assert out["telemetry"] == original["telemetry"]
    assert data == original


def test_v4_transform_and_runner_are_idempotent() -> None:
    out, _ = run_migrations({}, target_version=4)
    assert out["meta"]["schema_version"] == 4
    assert MIGRATIONS[4](out) == out
    assert run_migrations(out, target_version=4) == (out, 4)
