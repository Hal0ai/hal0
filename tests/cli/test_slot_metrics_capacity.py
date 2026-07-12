"""Tests for `hal0 slot metrics [name]` / `hal0 slot capacity` (CLI
consolidation, 2026-07) — `/api/slots/metrics` and `/api/slots/capacity`
were already live endpoints (and hal0-admin MCP tools) with no CLI surface.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from hal0.cli import slot_commands

runner = CliRunner()


@pytest.fixture(autouse=True)
def _api_reachable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(slot_commands, "_api_unreachable", lambda _url: False)


def test_slot_metrics_all_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(path: str, **_kw: Any) -> Any:
        assert path == "/api/slots/metrics"
        return {
            "primary": {"tokens_per_sec": 42.5, "kv_cache_usage": 0.5, "mem_rss_mb": 1024},
            "embed": {"tokens_per_sec": 0, "mem_rss_mb": 512},
        }

    monkeypatch.setattr(slot_commands, "api_get", fake_get)
    result = runner.invoke(slot_commands.app, ["metrics"])
    assert result.exit_code == 0, result.output
    assert "primary" in result.output
    assert "embed" in result.output
    assert "42.5" in result.output


def test_slot_metrics_filters_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        slot_commands,
        "api_get",
        lambda path, **_kw: {
            "primary": {"tokens_per_sec": 42.5},
            "embed": {"tokens_per_sec": 0},
        },
    )
    result = runner.invoke(slot_commands.app, ["metrics", "primary", "--json"])
    assert result.exit_code == 0, result.output
    assert "primary" in result.output
    assert "embed" not in result.output


def test_slot_metrics_unknown_name_dies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slot_commands, "api_get", lambda path, **_kw: {"primary": {}})
    result = runner.invoke(slot_commands.app, ["metrics", "nope"])
    assert result.exit_code != 0
    assert "no metrics" in result.output.lower()


def test_slot_capacity_reports_budget_and_per_slot_table(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(path: str, **_kw: Any) -> Any:
        assert path == "/api/slots/capacity"
        return {
            "per_slot": {
                "primary": {
                    "state": "ready",
                    "model_id": "qwen3-4b",
                    "vram_mb": 4096,
                    "ram_mb": 512,
                    "mem_mb": 4608,
                }
            },
            "slot_budget": {"used_slots": 1, "max_slots": 0},
        }

    monkeypatch.setattr(slot_commands, "api_get", fake_get)
    result = runner.invoke(slot_commands.app, ["capacity"])
    assert result.exit_code == 0, result.output
    assert "unlimited" in result.output
    assert "qwen3-4b" in result.output


def test_slot_capacity_json_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        slot_commands,
        "api_get",
        lambda path, **_kw: {"per_slot": {}, "slot_budget": {"used_slots": 0, "max_slots": 4}},
    )
    result = runner.invoke(slot_commands.app, ["capacity", "--json"])
    assert result.exit_code == 0, result.output
    assert '"max_slots": 4' in result.output
