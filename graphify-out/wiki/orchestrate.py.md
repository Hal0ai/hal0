# orchestrate.py

> 42 nodes

## Key Concepts

- **orchestrate.py** (26 connections) — `src/hal0/install/orchestrate.py`
- **apply_setup()** (20 connections) — `src/hal0/install/orchestrate.py`
- **run_pull_and_activate()** (8 connections) — `src/hal0/install/orchestrate.py`
- **_persist_store_dir()** (8 connections) — `src/hal0/install/orchestrate.py`
- **_validate_store_mount()** (7 connections) — `src/hal0/install/orchestrate.py`
- **Path** (6 connections)
- **_nearest_existing_ancestor()** (5 connections) — `src/hal0/install/orchestrate.py`
- **PullPlan** (4 connections) — `src/hal0/install/orchestrate.py`
- **_build_slot_cfg()** (4 connections) — `src/hal0/install/orchestrate.py`
- **_sentinel_path()** (4 connections) — `src/hal0/install/orchestrate.py`
- **mark_first_run_done()** (4 connections) — `src/hal0/install/orchestrate.py`
- **_free_space_gib()** (4 connections) — `src/hal0/install/orchestrate.py`
- **_is_root_fs()** (4 connections) — `src/hal0/install/orchestrate.py`
- **_colocated_flm_store()** (4 connections) — `src/hal0/install/orchestrate.py`
- **install_extension()** (4 connections) — `src/hal0/install/orchestrate.py`
- **_install_extensions()** (4 connections) — `src/hal0/install/orchestrate.py`
- **SlotOutcome** (3 connections) — `src/hal0/install/orchestrate.py`
- **SetupResult** (3 connections) — `src/hal0/install/orchestrate.py`
- **_set_slot_enabled()** (3 connections) — `src/hal0/install/orchestrate.py`
- **_ensure_registry_entry()** (3 connections) — `src/hal0/install/orchestrate.py`
- **_is_writable()** (3 connections) — `src/hal0/install/orchestrate.py`
- **test_setup_result_shape()** (3 connections) — `tests/install/test_orchestrate.py`
- **SlotSelection** (2 connections) — `src/hal0/install/orchestrate.py`
- **test_build_slot_cfg_sets_device_profile_model()** (2 connections) — `tests/api/test_install_apply.py`
- **In-process orchestration for first-run setup (design D3, spec §6.6).  Lifted out** (1 connections) — `src/hal0/install/orchestrate.py`
- *... and 17 more nodes in this community*

## Relationships

- [HardwareInfo](HardwareInfo.md) (4 shared connections)
- [install_openwebui](install_openwebui.md) (3 shared connections)
- [test_orchestrate.py](test_orchestrate.py.md) (3 shared connections)
- [installer.py](installer.py.md) (3 shared connections)
- [test_setup_install.py](test_setup_install.py.md) (2 shared connections)
- [run_pull](run_pull.md) (2 shared connections)
- [SlotState](SlotState.md) (2 shared connections)
- [load_hal0_config](load_hal0_config.md) (2 shared connections)
- [test_profile_derive.py](test_profile_derive.py.md) (2 shared connections)
- [get_curated](get_curated.md) (1 shared connections)

## Source Files

- `src/hal0/install/orchestrate.py`
- `tests/api/test_install_apply.py`
- `tests/install/test_orchestrate.py`

## Audit Trail

- EXTRACTED: 137 (88%)
- INFERRED: 19 (12%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*