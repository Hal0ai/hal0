"""test_planner.py — the staleness set-difference against a temp store (DESIGN §6).

Exercises both clauses: (1) a cell with no ok record is stale; once an ok record
with that exact cell_key is appended it is NOT stale; and (2) a cell whose newest
ok record is older than max_age_days is stale again. Also checks that a
provenance change (different resolved argv, expressed via the registry profile)
re-introduces staleness because the cell_key moved.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hal0.bench.planner import plan
from hal0.bench.schema import Host, Outcome, Record
from hal0.bench.store import Store
from hal0.bench.suites import suite_from_dict


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


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


def _registry():
    # one installed model with a default lane hint and a resolved profile
    return [
        {
            "id": "m1",
            "installed": True,
            "caps": ["chat"],
            "sha256": "abc",
            "default_lane": "rocm",
            "profile": {"argv": ["-b", "512"], "kv": {"main_k": "q8_0"}, "ctx": 32768},
            "n_gen": 256,
        }
    ]


def _ok_record_for(cell, run_id: str) -> Record:
    return Record(
        run_id=run_id,
        suite="roster",
        trigger="manual",
        identity=cell.identity,
        host=Host(hal0_version="0.9.0"),
        outcome=Outcome.OK,
    )


def test_never_measured_is_stale(store):
    cells = plan(_suite(), _registry(), store)
    assert len(cells) == 1
    assert cells[0].reason == "never-measured"
    assert cells[0].model_id == "m1"
    assert cells[0].kind == "tg"


def test_ok_record_clears_staleness(store):
    cells = plan(_suite(), _registry(), store)
    # append a fresh ok record for exactly that cell_key
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.append_record(_ok_record_for(cells[0], f"{now}-aaa"))
    # re-plan: the set-difference now removes it
    assert plan(_suite(), _registry(), store) == []


def test_aged_record_is_stale_again(store):
    cells = plan(_suite(), _registry(), store)
    old_dt = (datetime.now(UTC) - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.append_record(_ok_record_for(cells[0], f"{old_dt}-old"))
    replanned = plan(_suite(), _registry(), store)
    assert len(replanned) == 1
    assert replanned[0].reason.startswith("stale:")


def test_failed_record_does_not_clear_staleness(store):
    cells = plan(_suite(), _registry(), store)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = _ok_record_for(cells[0], f"{now}-fail")
    rec.outcome = Outcome.FAILED  # only ok records count (DESIGN §6)
    store.append_record(rec)
    # store keys current cells off ok records only, so still stale
    assert len(plan(_suite(), _registry(), store)) == 1


def test_provenance_drift_reintroduces_staleness(store):
    cells = plan(_suite(), _registry(), store)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.append_record(_ok_record_for(cells[0], f"{now}-aaa"))
    assert plan(_suite(), _registry(), store) == []

    # a merged flag PR changes resolved argv -> different cell_key -> stale again
    drifted = _registry()
    drifted[0]["profile"]["argv"] = ["-b", "1024"]
    replanned = plan(_suite(), drifted, store)
    assert len(replanned) == 1
    assert replanned[0].reason == "never-measured"
    assert replanned[0].cell_key != cells[0].cell_key


def test_config_matrix_expands_to_distinct_cells(store):
    # each config variant with distinct flags is its own cell (its flags feed
    # cell_key), so a suite can A/B tuning flags and compare them.
    suite = suite_from_dict(
        {
            "suite": {"id": "cfg"},
            "selector": {"installed": True},
            "matrix": {
                "lanes": ["default"],
                "depths": [2048],
                "reps": 1,
                "configs": [
                    {"label": "default", "flags": {}},
                    {"label": "b1024", "flags": {"-b": 1024}},
                ],
            },
            "cells": {"kinds": ["tg"]},
        }
    )
    cells = plan(suite, _registry(), store)
    assert len(cells) == 2  # 1 model x 1 kind x 2 variants
    assert len({c.cell_key for c in cells}) == 2  # b1024 differs from default
    assert {c.config_label for c in cells} == {"default", "b1024"}
    b = next(c for c in cells if c.config_label == "b1024")
    assert b.flags == {"-b": "1024"}
    assert "-b" in b.identity.config.argv and "1024" in b.identity.config.argv


def test_config_non_whitelisted_flag_dropped(store):
    # a non-seam-whitelisted flag is dropped (the seam would reject it); the rest
    # of the variant's flags still apply.
    suite = suite_from_dict(
        {
            "suite": {"id": "cfg"},
            "selector": {"installed": True},
            "matrix": {
                "lanes": ["default"],
                "depths": [2048],
                "reps": 1,
                "configs": [{"label": "mix", "flags": {"--nope": 1, "-ctk": "q8_0"}}],
            },
            "cells": {"kinds": ["tg"]},
        }
    )
    cells = plan(suite, _registry(), store)
    assert len(cells) == 1
    assert cells[0].flags == {"-ctk": "q8_0"}  # --nope dropped, -ctk kept


def test_selector_excludes_uninstalled(store):
    reg = _registry()
    reg[0]["installed"] = False
    assert plan(_suite(), reg, store) == []
