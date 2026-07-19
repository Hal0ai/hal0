# test_orchestrate.py

> 54 nodes · cohesion 0.06

## Key Concepts

- **test_orchestrate.py** (36 connections) — `tests/install/test_orchestrate.py`
- **Selections** (28 connections) — `src/hal0/install/orchestrate.py`
- **_strix_hw()** (16 connections) — `tests/install/test_orchestrate.py`
- **_FakeSlotManager** (12 connections) — `tests/install/test_orchestrate.py`
- **_RecordingSlotManager** (10 connections) — `tests/install/test_orchestrate.py`
- **test_apply_setup_colocates_flm_store()** (6 connections) — `tests/install/test_orchestrate.py`
- **test_apply_setup_empty_storage_dir_is_noop()** (6 connections) — `tests/install/test_orchestrate.py`
- **test_apply_setup_ignores_relative_storage_dir()** (6 connections) — `tests/install/test_orchestrate.py`
- **test_apply_setup_ignores_relative_storage_dir_for_flm_store_too()** (6 connections) — `tests/install/test_orchestrate.py`
- **test_apply_setup_persists_custom_store_dir()** (6 connections) — `tests/install/test_orchestrate.py`
- **_Job** (5 connections) — `tests/install/test_orchestrate.py`
- **test_apply_setup_creates_slot_disabled_and_plan_carries_slot()** (5 connections) — `tests/install/test_orchestrate.py`
- **test_apply_setup_scaffolds_modelless_slot_without_pull()** (5 connections) — `tests/install/test_orchestrate.py`
- **test_apply_setup_shared_model_enables_both_slots()** (5 connections) — `tests/install/test_orchestrate.py`
- **test_apply_setup_survives_sentinel_permission_error()** (5 connections) — `tests/install/test_orchestrate.py`
- **test_apply_setup_creates_chat_slot_and_plans_pull()** (4 connections) — `tests/install/test_orchestrate.py`
- **test_apply_setup_skips_uncurated_model()** (4 connections) — `tests/install/test_orchestrate.py`
- **test_clamp_context_size_passes_through_on_ample_hw()** (4 connections) — `tests/install/test_orchestrate.py`
- **test_run_pull_and_activate_marks_and_reraises_on_exception()** (4 connections) — `tests/install/test_orchestrate.py`
- **test_run_pull_and_activate_marks_disabled_on_failed_job()** (4 connections) — `tests/install/test_orchestrate.py`
- **test_run_pull_and_activate_enables_slot_on_success()** (3 connections) — `tests/install/test_orchestrate.py`
- **test_is_root_fs_false_for_tmp_path()** (2 connections) — `tests/install/test_orchestrate.py`
- **test_mark_first_run_done_warns_instead_of_raising_on_permission_error()** (2 connections) — `tests/install/test_orchestrate.py`
- **test_selections_roundtrip()** (2 connections) — `tests/install/test_orchestrate.py`
- **The full set of first-run choices to apply.** (1 connections) — `src/hal0/install/orchestrate.py`
- *... and 29 more nodes in this community*

## Relationships

- [load_hal0_config](load_hal0_config.md) (7 shared connections)
- [orchestrate.py](orchestrate.py.md) (6 shared connections)
- [build_auto_selections](build_auto_selections.md) (4 shared connections)
- [test_emit_answers.py](test_emit_answers.py.md) (4 shared connections)
- [installer.py](installer.py.md) (3 shared connections)
- [HardwareInfo](HardwareInfo.md) (3 shared connections)
- [load_answers](load_answers.md) (1 shared connections)
- [test_setup_install.py](test_setup_install.py.md) (1 shared connections)
- [test_probe.py](test_probe.py.md) (1 shared connections)

## Source Files

- `src/hal0/install/orchestrate.py`
- `tests/install/test_orchestrate.py`

## Audit Trail

- EXTRACTED: 183 (85%)
- INFERRED: 33 (15%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*