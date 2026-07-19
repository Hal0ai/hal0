# SlotManager

> God node · 277 connections · `src/hal0/slots/manager.py`

**Community:** [SlotManager](SlotManager.md)

## Connections by Relation

### calls
- test_zero_window_from_toml_never_auto_restores() `INFERRED`
- test_registry_style_model_id_does_not_take_flm_path() `INFERRED`
- test_update_config_backend_invalidates_state_extras() `INFERRED`
- test_disable_hides_slot_from_routing() `INFERRED`
- test_flm_inference_gate_passes_assigned_model() `INFERRED`
- test_flm_inference_gate_runs_once_and_promotes_to_ready() `INFERRED`
- test_flm_inference_gate_wedged_npu_stays_warming() `INFERRED`
- test_status_rehydrates_backend_from_toml() `INFERRED`
- test_create_rejects_second_default_of_same_type() `INFERRED`
- test_default_slot_for_still_raises_on_two_disk_defaults() `INFERRED`
- test_update_config_rejects_flipping_default_when_one_exists() `INFERRED`
- test_await_ready_health_timeout_stays_non_dispatchable() `INFERRED`
- test_npu_container_slot_spawns_with_flm_tag() `INFERRED`
- test_npu_container_slot_spawns_with_toml_default() `INFERRED`
- test_hal0_backend_env_var_is_ignored() `INFERRED`
- test_update_config_model_merge_matches_shared_helper() `INFERRED`
- test_apply_preferred_runner_noop_when_already_adopted() `INFERRED`
- test_apply_preferred_runner_swaps_when_compatible() `INFERRED`
- test_create_adopts_compatible_preferred_runner() `INFERRED`
- test_migration_clears_only_crash_combo() `INFERRED`

### contains
- [manager.py](manager.py.md) `EXTRACTED`

### indirect_call
- _mgr() `INFERRED`
- test_no_false_drift_for_registry_id_model_and_alias() `INFERRED`
- test_real_model_drift_still_flagged_after_resolution() `INFERRED`
- patched_spawn() `INFERRED`
- mock_slot_manager() `INFERRED`

### method
- .create() `EXTRACTED`
- .update_config() `EXTRACTED`
- .load() `EXTRACTED`
- .status() `EXTRACTED`
- ._transition() `EXTRACTED`
- ._key() `EXTRACTED`
- .rename() `EXTRACTED`
- ._resolve_alias() `EXTRACTED`
- .state() `EXTRACTED`
- ._current_state() `EXTRACTED`
- .delete() `EXTRACTED`
- ._load_slot_config() `EXTRACTED`
- ._maybe_adopt_running_slot() `EXTRACTED`
- .unload() `EXTRACTED`
- ._ensure_known() `EXTRACTED`
- .swap() `EXTRACTED`
- ._await_ready() `EXTRACTED`
- ._config_file() `EXTRACTED`
- ._maybe_load_config() `EXTRACTED`
- .restart() `EXTRACTED`

### rationale_for
- Manages the lifecycle of all hal0 inference slots.      Each public method corre `EXTRACTED`

### uses
- [SlotState](SlotState.md) `INFERRED`
- [FLMProvider](FLMProvider.md) `INFERRED`
- [GpuArbiter](GpuArbiter.md) `INFERRED`
- [SlotConfigError](SlotConfigError.md) `INFERRED`
- SlotStateRecord `INFERRED`
- SlotReaper `INFERRED`
- SlotInterface `INFERRED`
- LoadedSlot `INFERRED`
- SlotWatchdog `INFERRED`
- IllegalSlotTransition `INFERRED`
- SlotAlreadyExists `INFERRED`
- SlotPinned `INFERRED`
- SlotNotFound `INFERRED`

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*