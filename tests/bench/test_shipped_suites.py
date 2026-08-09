"""test_shipped_suites.py — the suite TOMLs the installer actually ships must
load, use only runnable kinds, and select sanely. concurrency.toml went
unnoticed as fully non-functional because nothing ever loaded the real files."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from hal0.bench.planner import KNOWN_KINDS, plan
from hal0.bench.store import Store
from hal0.bench.suites import Suite, load_suites, suite_from_dict

SEED_DIR = Path(__file__).resolve().parents[2] / "installer" / "bench" / "suites"


def test_every_shipped_suite_loads():
    suites = load_suites(SEED_DIR)
    assert set(suites) == {"roster", "smoke", "lane-matrix"}


def test_suite_has_no_schedule_field():
    """Phase 4 (bench-overhaul): the never-implemented ``Suite.schedule`` hook
    is deleted outright, not stubbed — no systemd timer or worker ever
    consulted it (cadence lives in installer/systemd/hal0-bench.timer +
    window.toml's own politeness gate)."""
    names = {f.name for f in dataclasses.fields(Suite)}
    assert "schedule" not in names


def test_a_schedule_key_in_toml_is_silently_ignored_not_fatal():
    """A stray ``schedule =`` line (an operator's stale copy of an old suite
    file) must not break loading — suites.py's contract is "unknown keys are
    ignored, not fatal" (module docstring)."""
    suite = suite_from_dict(
        {
            "suite": {"id": "t", "schedule": "weekly"},
            "matrix": {"lanes": ["default"], "depths": [2048]},
        }
    )
    assert not hasattr(suite, "schedule")
    assert suite.id == "t"


def test_shipped_toml_files_carry_no_schedule_key():
    """Comments may still explain WHY the key is gone (they do); no shipped
    file may carry an actual ``schedule =`` assignment."""
    import re

    for path in sorted(SEED_DIR.glob("*.toml")):
        lines = [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("#")]
        assert not any(re.match(r"^\s*schedule\s*=", ln) for ln in lines), (
            f"{path} still assigns [suite].schedule"
        )


def test_every_shipped_kind_is_runnable():
    for suite in load_suites(SEED_DIR).values():
        unknown = [k for k in suite.cells.kinds if k not in KNOWN_KINDS]
        assert not unknown, f"suite {suite.id} ships unrunnable kind(s) {unknown}"


def test_lane_matrix_with_unset_include_selects_nothing(tmp_path, capsys):
    """The operator-curated suite must NOT fall back to every installed GGUF
    (2 lanes x 3 depths x the whole roster = an accidental multi-day job)."""
    suite = load_suites(SEED_DIR)["lane-matrix"]
    assert suite.selector.include_only
    registry = [
        {"id": f"m{i}", "installed": True, "caps": ["chat"], "gguf": f"/x/m{i}.gguf"}
        for i in range(10)
    ]
    assert plan(suite, registry, Store(tmp_path)) == []


def test_roster_plans_over_the_registry(tmp_path):
    suite = load_suites(SEED_DIR)["roster"]
    registry = [{"id": "m1", "installed": True, "capabilities": ["chat"], "gguf": "/x/m1.gguf"}]
    cells = plan(suite, registry, Store(tmp_path))
    assert cells, "roster suite should plan cells for an installed chat model"
    assert {c.kind for c in cells} <= KNOWN_KINDS
