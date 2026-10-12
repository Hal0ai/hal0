"""Bench cells launch under the slot's profile (ADR-0029).

A model row holds file facts only, so the tune a cell is measured under is the
profile of the slot that serves the model. ``attach_slot_profiles`` puts it on
the registry dicts the planner reads, through the launch path's own resolvers;
the bench CLI and ``GET /api/benchmarks/plan`` both call it, so a real cell
carries the profile's flags and window instead of an empty argv.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest

from hal0.bench import cli
from hal0.bench.planner import attach_slot_profiles, plan
from hal0.bench.store import Store
from hal0.bench.suites import suite_from_dict

PROFILE_FLAGS = "-b 1024 -ub 256 -ctk q8_0 -ctv q8_0"


def _suite():
    return suite_from_dict(
        {
            "suite": {"id": "roster", "priority": 50},
            "selector": {"installed": True},
            "matrix": {"lanes": ["default"], "depths": [2048], "samplers": ["greedy"], "reps": 3},
            "cells": {"kinds": ["tg"]},
            "staleness": {"max_age_days": 30},
        }
    )


def _registry() -> list[dict[str, Any]]:
    """What GET /api/models returns: facts only, no tune of any kind."""
    return [
        {
            "id": "m1",
            "installed": True,
            "caps": ["chat"],
            "sha256": "abc",
            "default_lane": "rocm",
            "metadata": {"context_length": 131072},
            "defaults": None,
        },
        {
            "id": "unserved",
            "installed": True,
            "caps": ["chat"],
            "sha256": "def",
            "default_lane": "rocm",
            "metadata": {"context_length": 131072},
            "defaults": None,
        },
    ]


@pytest.fixture
def box(tmp_hal0_home: str) -> Path:
    """A box with one custom profile and one slot serving ``m1`` under it."""
    etc = Path(tmp_hal0_home) / "etc" / "hal0"
    (etc / "slots").mkdir(parents=True, exist_ok=True)
    (etc / "profiles.toml").write_text(
        "[profile.bench-tuned]\n"
        f'flags = "{PROFILE_FLAGS}"\n'
        "ctx_size = 65536\n"
        "enable_thinking = true\n"
        'vision = "off"\n'
        'mtp = "off"\n',
        encoding="utf-8",
    )
    (etc / "slots" / "chat.toml").write_text(
        'name = "chat"\nport = 8081\nprofile = "bench-tuned"\n[model]\ndefault = "m1"\n',
        encoding="utf-8",
    )
    return etc


def _m1(models: list[dict[str, Any]]) -> dict[str, Any]:
    return next(m for m in models if m["id"] == "m1")


def test_attach_carries_every_profile_tune(box: Path) -> None:
    models = attach_slot_profiles(_registry())

    profile = _m1(models)["profile"]
    assert profile["slot"] == "chat"
    assert profile["name"] == "bench-tuned"
    assert profile["flags"] == PROFILE_FLAGS
    assert profile["ctx_size"] == 65536
    assert profile["ctx_source"] == "profile"
    assert profile["enable_thinking"] is True
    assert profile["vision"] == "off"
    assert profile["mtp"] == "off"


def test_attach_leaves_an_unserved_model_and_the_input_alone(box: Path) -> None:
    registry = _registry()
    models = attach_slot_profiles(registry)

    assert "profile" not in next(m for m in models if m["id"] == "unserved")
    assert all("profile" not in m for m in registry)  # the input is never mutated


def test_attach_keeps_a_profile_the_entry_already_carries(box: Path) -> None:
    registry = _registry()
    registry[0]["profile"] = {"argv": ["-b", "512"], "ctx": 8192}

    assert _m1(attach_slot_profiles(registry))["profile"] == {"argv": ["-b", "512"], "ctx": 8192}


def test_attach_uses_the_effective_window_when_the_profile_sets_none(tmp_hal0_home: str) -> None:
    """No ``ctx_size`` on the profile: the cell sizes the window the slot
    launches with — the native one, dense-capped at 32768."""
    models = attach_slot_profiles(
        _registry(), slots=[{"name": "chat", "type": "llm", "model": {"default": "m1"}}]
    )

    profile = _m1(models)["profile"]
    assert profile["ctx_size"] == 32768
    assert profile["ctx_source"] == "native"
    assert profile["name"] == "chat"  # an unnamed profile is the chat seed


def test_attach_skips_a_slot_whose_profile_does_not_exist(tmp_hal0_home: str) -> None:
    models = attach_slot_profiles(
        _registry(),
        slots=[
            {"name": "a", "model": {"default": "m1"}, "profile": "no-such-profile"},
            {"name": "b", "model": {"default": "m1"}},
        ],
    )

    assert _m1(models)["profile"]["slot"] == "b"


def test_attach_never_raises_on_an_unreadable_config() -> None:
    registry = _registry()
    assert attach_slot_profiles(registry, slots=[object()]) == registry  # type: ignore[list-item]


def test_a_planned_cell_carries_the_profile_flags_and_window(box: Path, tmp_path: Path) -> None:
    cells = plan(_suite(), attach_slot_profiles(_registry()), Store(tmp_path))

    cell = next(c for c in cells if c.model_id == "m1")
    cfg = cell.identity.config
    assert cfg.argv == PROFILE_FLAGS.split()
    assert cfg.ctx == 65536
    assert cfg.spec == {"profile": {"enable_thinking": True, "vision": "off", "mtp": "off"}}
    assert "reasoning" in cell.identity.model.caps

    bare = next(c for c in cells if c.model_id == "unserved")
    assert bare.identity.config.argv == []
    assert bare.identity.config.spec is None
    assert bare.cell_key != cell.cell_key


def test_a_profile_tune_change_moves_the_cell_key(box: Path, tmp_path: Path) -> None:
    store = Store(tmp_path)
    before = next(c for c in plan(_suite(), attach_slot_profiles(_registry()), store))
    (box / "profiles.toml").write_text(
        f'[profile.bench-tuned]\nflags = "{PROFILE_FLAGS}"\nctx_size = 65536\n'
        'enable_thinking = true\nvision = "off"\nmtp = "auto"\n',
        encoding="utf-8",
    )
    after = next(c for c in plan(_suite(), attach_slot_profiles(_registry()), store))

    assert before.cell_key != after.cell_key


# ── the real entry points ────────────────────────────────────────────────────
# (the API one lives in tests/api/test_benchmarks_plan_profiles.py)


def test_cli_plan_cells_carry_the_profile_flags(
    box: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``hal0-bench plan`` builds its candidate models through the CLI's own
    fetch, so the cells it plans launch under the slot's profile."""
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "fetch_registry_models", lambda api: _registry())
    monkeypatch.setattr(cli, "_load_suite", lambda ref: _suite())
    captured: list[Any] = []
    real_plan = cli.plan

    def spy(suite, models, store):
        cells = real_plan(suite, models, store)
        captured.extend(cells)
        return cells

    monkeypatch.setattr(cli, "plan", spy)

    rc = cli.cmd_plan(
        types.SimpleNamespace(suite="roster", json=True, no_registry=False, api="http://x")
    )

    assert rc == 0
    cell = next(c for c in captured if c.model_id == "m1")
    assert cell.identity.config.argv == PROFILE_FLAGS.split()
    assert cell.identity.config.ctx == 65536
