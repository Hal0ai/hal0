# test_perms.py

> 18 nodes

## Key Concepts

- **test_perms.py** (27 connections) — `tests/install/test_perms.py`
- **Path** (11 connections)
- **PermObservation** (8 connections) — `src/hal0/install/perms.py`
- **_obs()** (8 connections) — `tests/install/test_perms.py`
- **_me()** (6 connections) — `tests/install/test_perms.py`
- **_diff()** (6 connections) — `tests/install/test_perms.py`
- **test_audit_rows_status_vocabulary()** (6 connections) — `tests/install/test_perms.py`
- **test_commit_applies_only_drifted_and_records_calls()** (5 connections) — `tests/install/test_perms.py`
- **test_commit_rolls_back_on_failure()** (5 connections) — `tests/install/test_perms.py`
- **test_plan_is_noop_when_disk_matches_table()** (4 connections) — `tests/install/test_perms.py`
- **test_plan_detects_each_drift_axis()** (4 connections) — `tests/install/test_perms.py`
- **test_absent_path_is_not_changed()** (4 connections) — `tests/install/test_perms.py`
- **test_glob_row_expands_and_noops_on_self_owned_tree()** (4 connections) — `tests/install/test_perms.py`
- **test_nested_state_json_two_levels_deep_plans_as_drift_and_heals()** (4 connections) — `tests/install/test_perms.py`
- **test_ownership_table_builds_under_hal0_home()** (2 connections) — `tests/install/test_perms.py`
- **A path's current ownership snapshot — the analogue of ``FileState``.      ``exis** (1 connections) — `src/hal0/install/perms.py`
- **Unit tests for hal0.install.perms — the declarative ownership table.  Covers:** (1 connections) — `tests/install/test_perms.py`
- **A root-owned ``slots/<id>/state.json`` two levels deep is audited AND fixed.** (1 connections) — `tests/install/test_perms.py`

## Relationships

- [perms.py](perms.py.md) (8 shared connections)
- [_by_target](_by_target.md) (8 shared connections)
- [test_hermes_home_row_unchanged_by_recursion_feature](test_hermes_home_row_unchanged_by_recursion_feature.md) (1 shared connections)
- [test_lock_file_rows_unchanged_by_recursion_feature](test_lock_file_rows_unchanged_by_recursion_feature.md) (1 shared connections)
- [test_ownership_table_has_no_rootless_podman_home_rows](test_ownership_table_has_no_rootless_podman_home_rows.md) (1 shared connections)
- [test_recursive_glob_does_not_alter_non_recursive_rows](test_recursive_glob_does_not_alter_non_recursive_rows.md) (1 shared connections)
- [test_registry_files_get_explicit_rows_matching_each_writer](test_registry_files_get_explicit_rows_matching_each_writer.md) (1 shared connections)
- [test_runtime_slots_row_heals_root_owned_tree](test_runtime_slots_row_heals_root_owned_tree.md) (1 shared connections)
- [test_runtime_slots_row_is_recursive_with_distinct_file_mode](test_runtime_slots_row_is_recursive_with_distinct_file_mode.md) (1 shared connections)

## Source Files

- `src/hal0/install/perms.py`
- `tests/install/test_perms.py`

## Audit Trail

- EXTRACTED: 107 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*