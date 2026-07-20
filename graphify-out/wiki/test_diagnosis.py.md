# test_diagnosis.py

> 45 nodes · cohesion 0.07

## Key Concepts

- **test_diagnosis.py** (15 connections) — `tests/cli/test_diagnosis.py`
- **to_diagnosis()** (11 connections) — `src/hal0/cli/doctor_diagnosis.py`
- **Diagnosis** (10 connections) — `src/hal0/diagnostics.py`
- **Evidence** (10 connections) — `src/hal0/diagnostics.py`
- **render_json()** (9 connections) — `src/hal0/cli/doctor_diagnosis.py`
- **diagnostics.py** (9 connections) — `src/hal0/diagnostics.py`
- **exit_code_for()** (7 connections) — `src/hal0/cli/doctor_diagnosis.py`
- **NextStep** (6 connections) — `src/hal0/diagnostics.py`
- **doctor_diagnosis.py** (5 connections) — `src/hal0/cli/doctor_diagnosis.py`
- **test_diagnosis_to_dict_round_trip()** (4 connections) — `tests/cli/test_diagnosis.py`
- **test_to_diagnosis_covers_every_check_key()** (4 connections) — `tests/cli/test_diagnosis.py`
- **test_layering.py** (4 connections) — `tests/diagnostics/test_layering.py`
- **Diagnosis** (3 connections)
- **overall_verdict()** (3 connections) — `src/hal0/diagnostics.py`
- **Any** (3 connections)
- **test_evidence_and_next_step_are_frozen()** (3 connections) — `tests/cli/test_diagnosis.py`
- **test_overall_verdict_critical_wins()** (3 connections) — `tests/cli/test_diagnosis.py`
- **test_overall_verdict_ok_when_only_info()** (3 connections) — `tests/cli/test_diagnosis.py`
- **test_overall_verdict_warn_on_fail_or_warn()** (3 connections) — `tests/cli/test_diagnosis.py`
- **test_render_json_is_a_list_of_to_dict()** (3 connections) — `tests/cli/test_diagnosis.py`
- **test_to_diagnosis_maps_critical_fail()** (3 connections) — `tests/cli/test_diagnosis.py`
- **test_to_diagnosis_maps_noncritical_fail()** (3 connections) — `tests/cli/test_diagnosis.py`
- **test_to_diagnosis_maps_warn_and_pass()** (3 connections) — `tests/cli/test_diagnosis.py`
- **.to_dict()** (2 connections) — `src/hal0/diagnostics.py`
- **.to_dict()** (2 connections) — `src/hal0/diagnostics.py`
- *... and 20 more nodes in this community*

## Relationships

- [Check](Check.md) (7 shared connections)
- [test_doctor_json_diagnoses.py](test_doctor_json_diagnoses.py.md) (6 shared connections)
- [_write_diagnostics_section](_write_diagnostics_section.md) (2 shared connections)
- [doctor_commands.py](doctor_commands.py.md) (1 shared connections)
- [die](die.md) (1 shared connections)
- [test_doctor_models.py](test_doctor_models.py.md) (1 shared connections)
- [test_doctor_profiles.py](test_doctor_profiles.py.md) (1 shared connections)

## Source Files

- `src/hal0/cli/doctor_diagnosis.py`
- `src/hal0/diagnostics.py`
- `tests/cli/test_diagnosis.py`
- `tests/diagnostics/test_layering.py`

## Audit Trail

- EXTRACTED: 100 (65%)
- INFERRED: 55 (35%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*