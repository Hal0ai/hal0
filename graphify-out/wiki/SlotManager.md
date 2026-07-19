# SlotManager

> 150 nodes

## Key Concepts

- **SlotManager** (277 connections) — `src/hal0/slots/manager.py`
- **test_manager.py** (63 connections) — `tests/slots/test_manager.py`
- **Path** (45 connections)
- **FakeContainerProvider** (30 connections)
- **_write_typed_slot()** (10 connections) — `tests/slots/test_manager.py`
- **test_update_config_backend_invalidates_state_extras()** (8 connections) — `tests/slots/test_manager.py`
- **test_status_rehydrates_backend_from_toml()** (7 connections) — `tests/slots/test_manager.py`
- **MonkeyPatch** (6 connections)
- **test_hal0_backend_env_var_is_ignored()** (6 connections) — `tests/slots/test_manager.py`
- **test_update_config_model_merge_matches_shared_helper()** (6 connections) — `tests/slots/test_manager.py`
- **_write_img_slot()** (6 connections) — `tests/slots/test_manager.py`
- **.list()** (5 connections) — `src/hal0/slots/manager.py`
- **test_default_slot_for_raises_when_two_defaults()** (5 connections) — `tests/slots/test_manager.py`
- **test_seeded_slot_deletable_with_force()** (5 connections) — `tests/slots/test_manager.py`
- **test_load_propagates_spawn_error_as_slot_error()** (5 connections) — `tests/slots/test_manager.py`
- **test_is_active_reflects_unit_state()** (5 connections) — `tests/slots/test_manager.py`
- **test_status_reconciles_drift()** (5 connections) — `tests/slots/test_manager.py`
- **test_status_adopts_running_slot_when_unit_active()** (5 connections) — `tests/slots/test_manager.py`
- **test_status_adopts_active_but_unhealthy_slot_as_warming()** (5 connections) — `tests/slots/test_manager.py`
- **test_status_flags_running_container_config_drift()** (5 connections) — `tests/slots/test_manager.py`
- **test_status_omits_config_drift_when_model_paths_resolve_to_same_file()** (5 connections) — `tests/slots/test_manager.py`
- **test_list_does_not_compute_config_drift_on_poll_path()** (5 connections) — `tests/slots/test_manager.py`
- **test_status_unloaded_slot_uses_toml_backend()** (5 connections) — `tests/slots/test_manager.py`
- **test_illegal_transition_blocked()** (5 connections) — `tests/slots/test_manager.py`
- **test_delete_removes_files_and_protects_seeded()** (5 connections) — `tests/slots/test_manager.py`
- *... and 125 more nodes in this community*

## Relationships

- [SlotState](SlotState.md) (68 shared connections)
- [SlotConfigError](SlotConfigError.md) (36 shared connections)
- [test_pulling_serving_idle.py](test_pulling_serving_idle.py.md) (16 shared connections)
- [_write_slot](_write_slot.md) (11 shared connections)
- [get_runner](get_runner.md) (9 shared connections)
- [test_mtp_defuse.py](test_mtp_defuse.py.md) (8 shared connections)
- [Path](Path.md) (8 shared connections)
- [test_manager_npu_container.py](test_manager_npu_container.py.md) (7 shared connections)
- [test_npu_exclusivity.py](test_npu_exclusivity.py.md) (7 shared connections)
- [test_device_profile_coherence.py](test_device_profile_coherence.py.md) (6 shared connections)
- [test_adopted_slot_eviction.py](test_adopted_slot_eviction.py.md) (5 shared connections)
- [compute_config_drift](compute_config_drift.md) (5 shared connections)

## Source Files

- `src/hal0/slots/manager.py`
- `tests/slots/test_manager.py`

## Audit Trail

- EXTRACTED: 558 (69%)
- INFERRED: 246 (31%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*