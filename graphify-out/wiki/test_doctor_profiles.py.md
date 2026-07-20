# test_doctor_profiles.py

> 26 nodes · cohesion 0.11

## Key Concepts

- **test_doctor_profiles.py** (13 connections) — `tests/cli/test_doctor_profiles.py`
- **check_profile_images_present()** (10 connections) — `src/hal0/cli/doctor_commands.py`
- **doctor_profiles()** (10 connections) — `src/hal0/cli/doctor_commands.py`
- **check_slot_profile_refs()** (7 connections) — `src/hal0/cli/doctor_commands.py`
- **_profile()** (6 connections) — `tests/cli/test_doctor_profiles.py`
- **_local_image_repos()** (4 connections) — `src/hal0/cli/doctor_commands.py`
- **_image_repo()** (3 connections) — `src/hal0/cli/doctor_commands.py`
- **_render_profiles()** (3 connections) — `src/hal0/cli/doctor_commands.py`
- **test_images_ignore_unused_profiles()** (3 connections) — `tests/cli/test_doctor_profiles.py`
- **test_images_ok_when_repo_present_regardless_of_tag()** (3 connections) — `tests/cli/test_doctor_profiles.py`
- **test_images_skipped_entirely_when_podman_unavailable()** (3 connections) — `tests/cli/test_doctor_profiles.py`
- **test_images_warn_when_in_use_image_not_pulled()** (3 connections) — `tests/cli/test_doctor_profiles.py`
- **test_refs_drift_when_profile_missing()** (2 connections) — `tests/cli/test_doctor_profiles.py`
- **test_refs_ok_when_slot_profile_exists()** (2 connections) — `tests/cli/test_doctor_profiles.py`
- **test_refs_skip_base_image_slots()** (2 connections) — `tests/cli/test_doctor_profiles.py`
- **Flag slots whose ``profile = "..."`` names a profile not in the catalog.      ``** (1 connections) — `src/hal0/cli/doctor_commands.py`
- **Strip the tag/digest → the bare ``registry/repo`` of an image ref.** (1 connections) — `src/hal0/cli/doctor_commands.py`
- **Warn when an *in-use* profile's image repo isn't present locally.      ``local_r** (1 connections) — `src/hal0/cli/doctor_commands.py`
- **Query ``podman images`` for the set of local ``registry/repo`` strings.      Ret** (1 connections) — `src/hal0/cli/doctor_commands.py`
- **Print profile audit rows with an ok/warn/drift badge, drift/warn detailed.** (1 connections) — `src/hal0/cli/doctor_commands.py`
- **Audit the slot↔profile layer: dangling references + un-pulled images.      Two c** (1 connections) — `src/hal0/cli/doctor_commands.py`
- **Tests for the ``hal0 doctor profiles`` slot↔profile audit classifiers.  Pure fun** (1 connections) — `tests/cli/test_doctor_profiles.py`
- **Build a ResolvedProfile-shaped stand-in (only the read attrs matter).** (1 connections) — `tests/cli/test_doctor_profiles.py`
- **test_image_repo_keeps_host_port()** (1 connections) — `tests/cli/test_doctor_profiles.py`
- **test_image_repo_strips_digest()** (1 connections) — `tests/cli/test_doctor_profiles.py`
- *... and 1 more nodes in this community*

## Relationships

- [doctor_commands.py](doctor_commands.py.md) (7 shared connections)
- [_write_diagnostics_section](_write_diagnostics_section.md) (3 shared connections)
- [test_doctor_json_diagnoses.py](test_doctor_json_diagnoses.py.md) (1 shared connections)
- [test_diagnosis.py](test_diagnosis.py.md) (1 shared connections)
- [load_slot_config](load_slot_config.md) (1 shared connections)
- [ProfileCatalog](ProfileCatalog.md) (1 shared connections)
- [types.py](types.py.md) (1 shared connections)

## Source Files

- `src/hal0/cli/doctor_commands.py`
- `tests/cli/test_doctor_profiles.py`

## Audit Trail

- EXTRACTED: 65 (76%)
- INFERRED: 20 (24%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*