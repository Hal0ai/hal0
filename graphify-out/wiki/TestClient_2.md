# TestClient

> 36 nodes · cohesion 0.08

## Key Concepts

- **TestClient** (47 connections)
- **_seed_slot_toml()** (18 connections) — `tests/api/test_slots_routes.py`
- **test_agent_hermes_slot_name_resolves_to_agent()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_config_profile_change_drives_device_via_route()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_slots_coresident_group_uses_device_not_legacy_names()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_slots_emits_declared_backend_from_device()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_slots_emits_labels_for_tool_calling_gate()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_slots_enable_thinking_null_when_unset()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_slots_exposes_enable_thinking_and_n_gpu_layers()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_slots_exposes_idle_timeout_workers_llamacpp_args()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_slots_exposes_rope_freq_base()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_slots_llamacpp_args_none_when_server_table_absent()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_slots_no_coresident_group_when_npu_anchor_disabled()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_slots_omits_declared_backend_when_no_device()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_slots_rope_freq_base_null_when_absent()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_list_slots_skips_coresident_for_disabled_sibling()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_put_config_rope_freq_base_roundtrip()** (6 connections) — `tests/api/test_slots_routes.py`
- **test_disable_offline_slot_does_not_unload()** (5 connections) — `tests/api/test_slots_routes.py`
- **npu_trio_slot_root()** (4 connections) — `tests/api/test_slots_routes.py`
- **coresident_group must key off device==npu, not the legacy slot names.      Deplo** (1 connections) — `tests/api/test_slots_routes.py`
- **Disabled NPU LLM anchor → no trio markers on the sibling slots.** (1 connections) — `tests/api/test_slots_routes.py`
- **A disabled sibling slot doesn't claim coresident membership.** (1 connections) — `tests/api/test_slots_routes.py`
- **Lay down the NPU FLM trio (agent + stt-npu + embed-npu) on disk.** (1 connections) — `tests/api/test_slots_routes.py`
- **A slot's enable_thinking + [model].n_gpu_layers ride along in the payload.** (1 connections) — `tests/api/test_slots_routes.py`
- **No enable_thinking in TOML → payload reports it as null (effective OFF).** (1 connections) — `tests/api/test_slots_routes.py`
- *... and 11 more nodes in this community*

## Relationships

- [.json](json.md) (34 shared connections)
- [Any](Any.md) (22 shared connections)
- [test_slots_routes.py](test_slots_routes.py.md) (18 shared connections)
- [FastAPI](FastAPI.md) (9 shared connections)

## Source Files

- `tests/api/test_slots_routes.py`

## Audit Trail

- EXTRACTED: 181 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*