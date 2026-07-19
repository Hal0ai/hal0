# FLMProvider

> 101 nodes

## Key Concepts

- **FLMProvider** (70 connections) — `src/hal0/providers/flm.py`
- **test_flm.py** (44 connections) — `tests/providers/test_flm.py`
- **test_flm_container_spec.py** (17 connections) — `tests/providers/test_flm_container_spec.py`
- **_slot_cfg()** (16 connections) — `tests/providers/test_flm_container_spec.py`
- **_model_info()** (16 connections) — `tests/providers/test_flm_container_spec.py`
- **Any** (12 connections)
- **test_chat_off_drops_positional_tag()** (5 connections) — `tests/providers/test_flm_container_spec.py`
- **test_no_per_role_model_flags()** (5 connections) — `tests/providers/test_flm_container_spec.py`
- **test_start_cmd_matches_container_role_args()** (5 connections) — `tests/providers/test_flm_container_spec.py`
- **test_default_models_dir_is_flm_cache()** (5 connections) — `tests/providers/test_flm_container_spec.py`
- **test_models_flm_store_config_drives_mount()** (5 connections) — `tests/providers/test_flm_container_spec.py`
- **test_spec_build_creates_missing_store_dir()** (5 connections) — `tests/providers/test_flm_container_spec.py`
- **test_model_table_context_size_drives_ctx_len()** (5 connections) — `tests/providers/test_flm_container_spec.py`
- **test_build_env_multiplex_flags()** (4 connections) — `tests/providers/test_flm.py`
- **test_container_spec_does_not_bind_mount_host_flm_tree()** (4 connections) — `tests/providers/test_flm.py`
- **test_container_spec_command_does_not_prefix_binary_path()** (4 connections) — `tests/providers/test_flm.py`
- **test_container_spec_ld_library_path_includes_xrt()** (4 connections) — `tests/providers/test_flm.py`
- **test_npu_table_drives_trio_flags()** (4 connections) — `tests/providers/test_flm_container_spec.py`
- **test_npu_table_off_means_chat_only()** (4 connections) — `tests/providers/test_flm_container_spec.py`
- **test_legacy_defaults_load_asr_still_honoured()** (4 connections) — `tests/providers/test_flm_container_spec.py`
- **test_npu_table_overrides_legacy_defaults()** (4 connections) — `tests/providers/test_flm_container_spec.py`
- **test_chat_on_keeps_positional_tag()** (4 connections) — `tests/providers/test_flm_container_spec.py`
- **test_env_var_overrides_flm_models_dir()** (4 connections) — `tests/providers/test_flm_container_spec.py`
- **test_legacy_ctx_size_still_wins_when_model_table_absent()** (4 connections) — `tests/providers/test_flm_container_spec.py`
- **test_build_env_renames_to_hal0_namespace()** (3 connections) — `tests/providers/test_flm.py`
- *... and 76 more nodes in this community*

## Relationships

- [flm.py](flm.py.md) (10 shared connections)
- [_spec_provider_for](_spec_provider_for.md) (6 shared connections)
- [ContainerProvider](ContainerProvider.md) (5 shared connections)
- [Mount](Mount.md) (3 shared connections)
- [test_manager_npu_container.py](test_manager_npu_container.py.md) (3 shared connections)
- [SlotConfigError](SlotConfigError.md) (2 shared connections)
- [Provider](Provider.md) (1 shared connections)
- [get_runner](get_runner.md) (1 shared connections)
- [SlotManager](SlotManager.md) (1 shared connections)
- [SlotState](SlotState.md) (1 shared connections)

## Source Files

- `src/hal0/providers/flm.py`
- `tests/providers/test_flm.py`
- `tests/providers/test_flm_container_spec.py`

## Audit Trail

- EXTRACTED: 333 (87%)
- INFERRED: 50 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*