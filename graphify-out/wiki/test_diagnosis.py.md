# test_diagnosis.py

> 26 nodes

## Key Concepts

- **test_diagnosis.py** (15 connections) — `tests/cli/test_diagnosis.py`
- **to_diagnosis()** (11 connections) — `src/hal0/cli/doctor_diagnosis.py`
- **render_json()** (9 connections) — `src/hal0/cli/doctor_diagnosis.py`
- **exit_code_for()** (7 connections) — `src/hal0/cli/doctor_diagnosis.py`
- **doctor_diagnosis.py** (5 connections) — `src/hal0/cli/doctor_diagnosis.py`
- **test_to_diagnosis_covers_every_check_key()** (4 connections) — `tests/cli/test_diagnosis.py`
- **Diagnosis** (3 connections)
- **test_to_diagnosis_maps_critical_fail()** (3 connections) — `tests/cli/test_diagnosis.py`
- **test_to_diagnosis_maps_noncritical_fail()** (3 connections) — `tests/cli/test_diagnosis.py`
- **test_to_diagnosis_maps_warn_and_pass()** (3 connections) — `tests/cli/test_diagnosis.py`
- **test_render_json_is_a_list_of_to_dict()** (2 connections) — `tests/cli/test_diagnosis.py`
- **test_overall_verdict_ok_when_only_info()** (2 connections) — `tests/cli/test_diagnosis.py`
- **test_overall_verdict_warn_on_fail_or_warn()** (2 connections) — `tests/cli/test_diagnosis.py`
- **test_overall_verdict_critical_wins()** (2 connections) — `tests/cli/test_diagnosis.py`
- **test_diagnosis_id_taxonomy_snapshot()** (2 connections) — `tests/cli/test_diagnosis.py`
- **``hal0 doctor``'s CLI-side ``Diagnosis`` re-export + adapters (§21.4).  The data** (1 connections) — `src/hal0/cli/doctor_diagnosis.py`
- **Map one ``doctor_verify.Check`` row to a ``Diagnosis`` row.      ``status`` -> `** (1 connections) — `src/hal0/cli/doctor_diagnosis.py`
- **The stable ``--json`` shape every doctor subcommand prints.      ``json.dumps(..** (1 connections) — `src/hal0/cli/doctor_diagnosis.py`
- **The §4.2 generic ``--json`` exit-code translation: critical->2,     fail/warn->1** (1 connections) — `src/hal0/cli/doctor_diagnosis.py`
- **test_diagnosis_is_frozen()** (1 connections) — `tests/cli/test_diagnosis.py`
- **test_evidence_and_next_step_are_frozen()** (1 connections) — `tests/cli/test_diagnosis.py`
- **test_diagnosis_to_dict_round_trip()** (1 connections) — `tests/cli/test_diagnosis.py`
- **test_every_taxonomy_id_follows_the_shape()** (1 connections) — `tests/cli/test_diagnosis.py`
- **Tests for the §21.4 ``Diagnosis`` retrofit backbone.  Covers: dataclass immutabi** (1 connections) — `tests/cli/test_diagnosis.py`
- **Every key doctor_verify.build_checks() can emit has an id mapping.** (1 connections) — `tests/cli/test_diagnosis.py`
- *... and 1 more nodes in this community*

## Relationships

- [Check](Check.md) (7 shared connections)
- [diagnostics.py](diagnostics.py.md) (2 shared connections)
- [_write_diagnostics_section](_write_diagnostics_section.md) (2 shared connections)
- [test_doctor_json_diagnoses.py](test_doctor_json_diagnoses.py.md) (1 shared connections)
- [doctor_commands.py](doctor_commands.py.md) (1 shared connections)
- [die](die.md) (1 shared connections)
- [test_doctor_models.py](test_doctor_models.py.md) (1 shared connections)
- [test_doctor_profiles.py](test_doctor_profiles.py.md) (1 shared connections)

## Source Files

- `src/hal0/cli/doctor_diagnosis.py`
- `tests/cli/test_diagnosis.py`

## Audit Trail

- EXTRACTED: 55 (65%)
- INFERRED: 29 (35%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*