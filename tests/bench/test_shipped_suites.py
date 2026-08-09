"""test_shipped_suites.py — the suite TOMLs the installer actually ships must
load, use only runnable kinds, and select sanely. concurrency.toml went
unnoticed as fully non-functional because nothing ever loaded the real files."""

from __future__ import annotations

from pathlib import Path

from hal0.bench.planner import KNOWN_KINDS, plan
from hal0.bench.store import Store
from hal0.bench.suites import load_suites

SEED_DIR = Path(__file__).resolve().parents[2] / "installer" / "bench" / "suites"


def test_every_shipped_suite_loads():
    suites = load_suites(SEED_DIR)
    assert set(suites) == {"roster", "smoke", "lane-matrix"}


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
