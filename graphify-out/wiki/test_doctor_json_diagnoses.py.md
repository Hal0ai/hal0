# test_doctor_json_diagnoses.py

> 38 nodes

## Key Concepts

- **test_doctor_json_diagnoses.py** (24 connections) — `tests/cli/test_doctor_json_diagnoses.py`
- **_diagnose_models()** (17 connections) — `src/hal0/cli/doctor_commands.py`
- **_diagnose_audit_rows()** (12 connections) — `src/hal0/cli/doctor_commands.py`
- **_diagnose_profiles()** (11 connections) — `src/hal0/cli/doctor_commands.py`
- **_diagnose_migration()** (10 connections) — `src/hal0/cli/doctor_commands.py`
- **Evidence** (8 connections) — `src/hal0/diagnostics.py`
- **_models_outside_mount_roots()** (6 connections) — `src/hal0/cli/doctor_commands.py`
- **Diagnosis** (4 connections)
- **test_diagnose_models_unmounted_entry()** (3 connections) — `tests/cli/test_doctor_json_diagnoses.py`
- **test_diagnose_audit_rows_all_ok_emits_doctor_ok()** (2 connections) — `tests/cli/test_doctor_json_diagnoses.py`
- **test_diagnose_audit_rows_absent_only_still_ok()** (2 connections) — `tests/cli/test_doctor_json_diagnoses.py`
- **test_diagnose_audit_rows_drift_becomes_fail_diagnosis()** (2 connections) — `tests/cli/test_doctor_json_diagnoses.py`
- **test_diagnose_audit_rows_one_per_drift_row()** (2 connections) — `tests/cli/test_doctor_json_diagnoses.py`
- **test_diagnose_audit_rows_carries_next_steps()** (2 connections) — `tests/cli/test_doctor_json_diagnoses.py`
- **test_diagnose_models_clean_emits_doctor_ok()** (2 connections) — `tests/cli/test_doctor_json_diagnoses.py`
- **test_diagnose_models_dangling_entry()** (2 connections) — `tests/cli/test_doctor_json_diagnoses.py`
- **test_diagnose_models_store_missing()** (2 connections) — `tests/cli/test_doctor_json_diagnoses.py`
- **test_models_outside_mount_roots_flags_only_unreachable()** (2 connections) — `tests/cli/test_doctor_json_diagnoses.py`
- **test_models_outside_mount_roots_empty_roots_is_noop()** (2 connections) — `tests/cli/test_doctor_json_diagnoses.py`
- **test_diagnose_models_unregistered_files()** (2 connections) — `tests/cli/test_doctor_json_diagnoses.py`
- **test_diagnose_models_flm_divergence_not_fixable()** (2 connections) — `tests/cli/test_doctor_json_diagnoses.py`
- **test_diagnose_models_flm_unmounted()** (2 connections) — `tests/cli/test_doctor_json_diagnoses.py`
- **test_diagnose_models_flm_not_writable_is_fixable()** (2 connections) — `tests/cli/test_doctor_json_diagnoses.py`
- **test_diagnose_migration_none_is_skipped()** (2 connections) — `tests/cli/test_doctor_json_diagnoses.py`
- **test_diagnose_migration_nothing_pending_is_ok()** (2 connections) — `tests/cli/test_doctor_json_diagnoses.py`
- *... and 13 more nodes in this community*

## Relationships

- [doctor_commands.py](doctor_commands.py.md) (13 shared connections)
- [_write_diagnostics_section](_write_diagnostics_section.md) (4 shared connections)
- [diagnostics.py](diagnostics.py.md) (3 shared connections)
- [die](die.md) (2 shared connections)
- [test_doctor_models.py](test_doctor_models.py.md) (1 shared connections)
- [test_doctor_profiles.py](test_doctor_profiles.py.md) (1 shared connections)
- [test_diagnosis.py](test_diagnosis.py.md) (1 shared connections)

## Source Files

- `src/hal0/cli/doctor_commands.py`
- `src/hal0/diagnostics.py`
- `tests/cli/test_doctor_json_diagnoses.py`

## Audit Trail

- EXTRACTED: 88 (61%)
- INFERRED: 57 (39%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*