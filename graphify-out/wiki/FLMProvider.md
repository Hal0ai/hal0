# FLMProvider

> 28 nodes · cohesion 0.18

## Key Concepts

- **FLMProvider** (70 connections) — `src/hal0/providers/flm.py`
- **test_flm_container_spec.py** (17 connections) — `tests/providers/test_flm_container_spec.py`
- **_model_info()** (16 connections) — `tests/providers/test_flm_container_spec.py`
- **_slot_cfg()** (16 connections) — `tests/providers/test_flm_container_spec.py`
- **test_chat_off_drops_positional_tag()** (5 connections) — `tests/providers/test_flm_container_spec.py`
- **test_default_models_dir_is_flm_cache()** (5 connections) — `tests/providers/test_flm_container_spec.py`
- **test_model_table_context_size_drives_ctx_len()** (5 connections) — `tests/providers/test_flm_container_spec.py`
- **test_models_flm_store_config_drives_mount()** (5 connections) — `tests/providers/test_flm_container_spec.py`
- **test_no_per_role_model_flags()** (5 connections) — `tests/providers/test_flm_container_spec.py`
- **test_spec_build_creates_missing_store_dir()** (5 connections) — `tests/providers/test_flm_container_spec.py`
- **test_start_cmd_matches_container_role_args()** (5 connections) — `tests/providers/test_flm_container_spec.py`
- **test_chat_on_keeps_positional_tag()** (4 connections) — `tests/providers/test_flm_container_spec.py`
- **test_env_var_overrides_flm_models_dir()** (4 connections) — `tests/providers/test_flm_container_spec.py`
- **test_legacy_ctx_size_still_wins_when_model_table_absent()** (4 connections) — `tests/providers/test_flm_container_spec.py`
- **test_legacy_defaults_load_asr_still_honoured()** (4 connections) — `tests/providers/test_flm_container_spec.py`
- **test_npu_table_drives_trio_flags()** (4 connections) — `tests/providers/test_flm_container_spec.py`
- **test_npu_table_off_means_chat_only()** (4 connections) — `tests/providers/test_flm_container_spec.py`
- **test_npu_table_overrides_legacy_defaults()** (4 connections) — `tests/providers/test_flm_container_spec.py`
- **Any** (2 connections)
- **Provider for the AMD NPU FLM backend.      Two-tier readiness (Option A — NPU do** (1 connections) — `src/hal0/providers/flm.py`
- **FLM container_spec: [npu] toggles + model-cache default (Phase A).** (1 connections) — `tests/providers/test_flm_container_spec.py`
- **Isolated via ``tmp_hal0_home`` (tests/conftest.py): without it this     reads th** (1 connections) — `tests/providers/test_flm_container_spec.py`
- **[models].flm_store must reach the container mount without the env var.      Regr** (1 connections) — `tests/providers/test_flm_container_spec.py`
- **Spec build must mkdir the bind source so podman never sees ENOENT.** (1 connections) — `tests/providers/test_flm_container_spec.py`
- **[model].context_size (SlotConfig shape) must reach --ctx-len.      Regression: b** (1 connections) — `tests/providers/test_flm_container_spec.py`
- *... and 3 more nodes in this community*

## Relationships

- [test_flm.py](test_flm.py.md) (17 shared connections)
- [_spec_provider_for](_spec_provider_for.md) (6 shared connections)
- [flm.py](flm.py.md) (4 shared connections)
- [ContainerProvider](ContainerProvider.md) (3 shared connections)
- [.container_spec](container_spec.md) (3 shared connections)
- [SlotConfigError](SlotConfigError.md) (3 shared connections)
- [Mount](Mount.md) (3 shared connections)
- [test_manager_npu_container.py](test_manager_npu_container.py.md) (3 shared connections)
- [FLMInferError](FLMInferError.md) (2 shared connections)
- [_render_from_spec](_render_from_spec.md) (2 shared connections)
- [Provider](Provider.md) (1 shared connections)
- [get_runner](get_runner.md) (1 shared connections)

## Source Files

- `src/hal0/providers/flm.py`
- `tests/providers/test_flm_container_spec.py`

## Audit Trail

- EXTRACTED: 143 (74%)
- INFERRED: 50 (26%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*