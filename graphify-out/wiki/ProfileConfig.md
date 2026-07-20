# ProfileConfig

> 91 nodes · cohesion 0.05

## Key Concepts

- **ProfileConfig** (73 connections) — `src/hal0/config/schema.py`
- **resolve_profile_flags()** (44 connections) — `src/hal0/config/schema.py`
- **_moe_profile()** (36 connections) — `tests/providers/test_container.py`
- **_render_llama()** (26 connections) — `tests/providers/test_container.py`
- **TestRenderUnit** (24 connections) — `tests/providers/test_container.py`
- **test_container.py** (21 connections) — `tests/providers/test_container.py`
- **TestProfileConfigValidation** (12 connections) — `tests/config/test_profiles.py`
- **_exec_line()** (12 connections) — `tests/providers/test_container.py`
- **TestResolveProfileFlags** (11 connections) — `tests/config/test_profiles.py`
- **resolve_gpu_group_ids()** (9 connections) — `src/hal0/providers/_gpu.py`
- **TestResolveProfileFlags** (7 connections) — `tests/providers/test_container.py`
- **TestUniformQuadletRender** (7 connections) — `tests/providers/test_container.py`
- **.test_ctx_size_in_exec()** (6 connections) — `tests/providers/test_container.py`
- **.test_json_extra_arg_preserves_quoting()** (6 connections) — `tests/providers/test_container.py`
- **.test_model_alias_in_exec()** (6 connections) — `tests/providers/test_container.py`
- **.test_numeric_group_add_present()** (6 connections) — `tests/providers/test_container.py`
- **.test_profile_flags_in_exec()** (6 connections) — `tests/providers/test_container.py`
- **.test_server_extra_args_appended()** (6 connections) — `tests/providers/test_container.py`
- **_mtp_profile()** (5 connections) — `tests/providers/test_container.py`
- **.test_device_passthrough()** (5 connections) — `tests/providers/test_container.py`
- **.test_explicit_device_nodes_emitted_no_bare_dri_dir()** (5 connections) — `tests/providers/test_container.py`
- **.test_healthcheck_targets_slot_port_not_image_default()** (5 connections) — `tests/providers/test_container.py`
- **.test_identical_path_mount_readonly()** (5 connections) — `tests/providers/test_container.py`
- **.test_image_and_exec_present()** (5 connections) — `tests/providers/test_container.py`
- **.test_loopback_port_publish()** (5 connections) — `tests/providers/test_container.py`
- *... and 66 more nodes in this community*

## Relationships

- [ContainerProvider](ContainerProvider.md) (11 shared connections)
- [Mount](Mount.md) (11 shared connections)
- [ProfileCatalog](ProfileCatalog.md) (10 shared connections)
- [TestContainerSpec](TestContainerSpec.md) (10 shared connections)
- [_resolve_llama_scalars](_resolve_llama_scalars.md) (9 shared connections)
- [test_mtp_override.py](test_mtp_override.py.md) (7 shared connections)
- [save_profiles_config](save_profiles_config.md) (5 shared connections)
- [TestFamilyDefaults](TestFamilyDefaults.md) (5 shared connections)
- [load_profiles_config](load_profiles_config.md) (4 shared connections)
- [_profile](_profile.md) (4 shared connections)
- [schema.py](schema.py.md) (3 shared connections)
- [test_slots_container_state.py](test_slots_container_state.py.md) (3 shared connections)

## Source Files

- `src/hal0/config/schema.py`
- `src/hal0/providers/_gpu.py`
- `tests/config/test_mtp_override.py`
- `tests/config/test_profiles.py`
- `tests/providers/test_container.py`

## Audit Trail

- EXTRACTED: 350 (70%)
- INFERRED: 148 (30%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*