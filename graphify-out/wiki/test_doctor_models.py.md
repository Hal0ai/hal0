# test_doctor_models.py

> 28 nodes · cohesion 0.10

## Key Concepts

- **test_doctor_models.py** (16 connections) — `tests/cli/test_doctor_models.py`
- **pending_layout_migration()** (9 connections) — `src/hal0/cli/doctor_commands.py`
- **flm_mount_guard()** (8 connections) — `src/hal0/cli/doctor_commands.py`
- **flm_store_writability()** (8 connections) — `src/hal0/cli/doctor_commands.py`
- **flm_store_divergence()** (6 connections) — `src/hal0/cli/doctor_commands.py`
- **_Stat** (6 connections) — `tests/cli/test_doctor_models.py`
- **doctor_migrations()** (5 connections) — `src/hal0/cli/doctor_commands.py`
- **test_writability_fails_when_not_writable_and_carries_repair_target()** (3 connections) — `tests/cli/test_doctor_models.py`
- **test_writability_ok_when_group_writable()** (3 connections) — `tests/cli/test_doctor_models.py`
- **test_writability_ok_when_owned_by_container_uid()** (3 connections) — `tests/cli/test_doctor_models.py`
- **test_divergence_flags_conflicting_env_and_toml()** (2 connections) — `tests/cli/test_doctor_models.py`
- **test_divergence_ignores_trailing_slash_only_difference()** (2 connections) — `tests/cli/test_doctor_models.py`
- **test_divergence_none_when_either_side_unset()** (2 connections) — `tests/cli/test_doctor_models.py`
- **test_mount_guard_ignores_on_root_store()** (2 connections) — `tests/cli/test_doctor_models.py`
- **test_mount_guard_ok_when_external_path_is_mounted()** (2 connections) — `tests/cli/test_doctor_models.py`
- **test_mount_guard_warns_when_external_path_not_mounted()** (2 connections) — `tests/cli/test_doctor_models.py`
- **test_pending_migration_none_when_planner_raises()** (2 connections) — `tests/cli/test_doctor_models.py`
- **test_pending_migration_reports_create_counts()** (2 connections) — `tests/cli/test_doctor_models.py`
- **test_pending_migration_zero_on_current_layout()** (2 connections) — `tests/cli/test_doctor_models.py`
- **Warn when the env var and the TOML field name *different* FLM stores.      ``HAL** (1 connections) — `src/hal0/cli/doctor_commands.py`
- **Warn when the store lives under an external mount prefix that isn't mounted.** (1 connections) — `src/hal0/cli/doctor_commands.py`
- **Classify FLM-store writability for the container uid; ``None`` when fine.      T** (1 connections) — `src/hal0/cli/doctor_commands.py`
- **Dry-run the v0.1→v0.2 model-layout migration; return ``(create, overwrite)``.** (1 connections) — `src/hal0/cli/doctor_commands.py`
- **Surface a pending v0.1→v0.2 model-layout migration (read-only).      The canonic** (1 connections) — `src/hal0/cli/doctor_commands.py`
- **stat_result** (1 connections)
- *... and 3 more nodes in this community*

## Relationships

- [doctor_commands.py](doctor_commands.py.md) (10 shared connections)
- [die](die.md) (3 shared connections)
- [test_doctor_json_diagnoses.py](test_doctor_json_diagnoses.py.md) (1 shared connections)
- [test_diagnosis.py](test_diagnosis.py.md) (1 shared connections)
- [Check](Check.md) (1 shared connections)
- [_write_diagnostics_section](_write_diagnostics_section.md) (1 shared connections)
- [test_migrate_model_layout.py](test_migrate_model_layout.py.md) (1 shared connections)

## Source Files

- `src/hal0/cli/doctor_commands.py`
- `tests/cli/test_doctor_models.py`

## Audit Trail

- EXTRACTED: 66 (70%)
- INFERRED: 28 (30%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*