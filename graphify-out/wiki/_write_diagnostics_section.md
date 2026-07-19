# _write_diagnostics_section

> 40 nodes · cohesion 0.11

## Key Concepts

- **_write_diagnostics_section()** (23 connections) — `src/hal0/cli/doctor_bundle.py`
- **build_bundle()** (21 connections) — `src/hal0/cli/doctor_bundle.py`
- **doctor_bundle.py** (17 connections) — `src/hal0/cli/doctor_bundle.py`
- **Path** (11 connections)
- **test_doctor_bundle.py** (11 connections) — `tests/cli/test_doctor_bundle.py`
- **Path** (9 connections)
- **_run_one()** (6 connections) — `src/hal0/cli/doctor_bundle.py`
- **_write_config_section()** (6 connections) — `src/hal0/cli/doctor_bundle.py`
- **doctor_bundle_cmd()** (5 connections) — `src/hal0/cli/doctor_bundle.py`
- **_write_json()** (5 connections) — `src/hal0/cli/doctor_bundle.py`
- **_write_logs_section()** (5 connections) — `src/hal0/cli/doctor_bundle.py`
- **_write_redacted_env()** (5 connections) — `src/hal0/cli/doctor_bundle.py`
- **_write_redacted_toml()** (5 connections) — `src/hal0/cli/doctor_bundle.py`
- **_api_get_or_unavailable()** (4 connections) — `src/hal0/cli/doctor_bundle.py`
- **_default_out()** (3 connections) — `src/hal0/cli/doctor_bundle.py`
- **Any** (3 connections)
- **_redact_text()** (3 connections) — `src/hal0/cli/doctor_bundle.py`
- **_write_commands_tsv()** (3 connections) — `src/hal0/cli/doctor_bundle.py`
- **_no_live_api()** (3 connections) — `tests/cli/test_doctor_bundle.py`
- **test_bundle_diagnostics_section_writes_expected_files()** (3 connections) — `tests/cli/test_doctor_bundle.py`
- **test_bundle_layout_matches_spec()** (3 connections) — `tests/cli/test_doctor_bundle.py`
- **test_bundle_manifest_shape()** (3 connections) — `tests/cli/test_doctor_bundle.py`
- **test_bundle_no_logs_flag_skips_logs_dir()** (3 connections) — `tests/cli/test_doctor_bundle.py`
- **test_bundle_redacts_api_env_values_but_keeps_key_names()** (3 connections) — `tests/cli/test_doctor_bundle.py`
- **test_bundle_redacts_sensitive_toml_keys()** (3 connections) — `tests/cli/test_doctor_bundle.py`
- *... and 15 more nodes in this community*

## Relationships

- [doctor_commands.py](doctor_commands.py.md) (4 shared connections)
- [test_doctor_json_diagnoses.py](test_doctor_json_diagnoses.py.md) (4 shared connections)
- [Check](Check.md) (3 shared connections)
- [test_doctor_profiles.py](test_doctor_profiles.py.md) (3 shared connections)
- [test_diagnosis.py](test_diagnosis.py.md) (2 shared connections)
- [test_redact.py](test_redact.py.md) (2 shared connections)
- [socket](socket.md) (1 shared connections)
- [Typer](Typer.md) (1 shared connections)
- [die](die.md) (1 shared connections)
- [test_doctor_models.py](test_doctor_models.py.md) (1 shared connections)
- [load_slot_config](load_slot_config.md) (1 shared connections)
- [ProfileCatalog](ProfileCatalog.md) (1 shared connections)

## Source Files

- `src/hal0/cli/doctor_bundle.py`
- `tests/cli/test_doctor_bundle.py`

## Audit Trail

- EXTRACTED: 148 (79%)
- INFERRED: 40 (21%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*