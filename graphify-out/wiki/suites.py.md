# suites.py

> 40 nodes · cohesion 0.10

## Key Concepts

- **suites.py** (13 connections) — `src/hal0/bench/suites.py`
- **test_planner.py** (13 connections) — `tests/bench/test_planner.py`
- **Suite** (12 connections) — `src/hal0/bench/suites.py`
- **suite_from_dict()** (12 connections) — `src/hal0/bench/suites.py`
- **_load_suite()** (9 connections) — `src/hal0/bench/cli.py`
- **_worklist_suite()** (9 connections) — `src/hal0/bench/cli.py`
- **load_suites()** (9 connections) — `src/hal0/bench/suites.py`
- **_registry()** (9 connections) — `tests/bench/test_planner.py`
- **load_suite_file()** (8 connections) — `src/hal0/bench/suites.py`
- **_suite()** (8 connections) — `tests/bench/test_planner.py`
- **_ok_record_for()** (7 connections) — `tests/bench/test_planner.py`
- **_normalize_configs()** (6 connections) — `src/hal0/bench/suites.py`
- **Cells** (5 connections) — `src/hal0/bench/suites.py`
- **Matrix** (5 connections) — `src/hal0/bench/suites.py`
- **Selector** (5 connections) — `src/hal0/bench/suites.py`
- **Staleness** (5 connections) — `src/hal0/bench/suites.py`
- **test_aged_record_is_stale_again()** (4 connections) — `tests/bench/test_planner.py`
- **test_failed_record_does_not_clear_staleness()** (4 connections) — `tests/bench/test_planner.py`
- **test_ok_record_clears_staleness()** (4 connections) — `tests/bench/test_planner.py`
- **test_provenance_drift_reintroduces_staleness()** (4 connections) — `tests/bench/test_planner.py`
- **test_config_matrix_expands_to_distinct_cells()** (3 connections) — `tests/bench/test_planner.py`
- **test_config_non_whitelisted_flag_dropped()** (3 connections) — `tests/bench/test_planner.py`
- **test_never_measured_is_stale()** (3 connections) — `tests/bench/test_planner.py`
- **test_selector_excludes_uninstalled()** (3 connections) — `tests/bench/test_planner.py`
- **_default_configs()** (2 connections) — `src/hal0/bench/suites.py`
- *... and 15 more nodes in this community*

## Relationships

- [cli.py](cli.py.md) (15 shared connections)
- [planner.py](planner.py.md) (5 shared connections)
- [cmd_worker](cmd_worker.md) (2 shared connections)
- [benchmarks.py](benchmarks.py.md) (2 shared connections)
- [runner.py](runner.py.md) (2 shared connections)

## Source Files

- `src/hal0/bench/cli.py`
- `src/hal0/bench/suites.py`
- `tests/bench/test_planner.py`

## Audit Trail

- EXTRACTED: 171 (94%)
- INFERRED: 11 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*