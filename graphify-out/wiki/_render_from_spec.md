# _render_from_spec

> 32 nodes · cohesion 0.10

## Key Concepts

- **_render_from_spec()** (13 connections) — `tests/providers/test_container_npu.py`
- **test_container_npu.py** (12 connections) — `tests/providers/test_container_npu.py`
- **_flm_spec()** (12 connections) — `tests/providers/test_container_npu.py`
- **TestRenderUnitFromSpec** (12 connections) — `tests/providers/test_container_npu.py`
- **_exec()** (5 connections) — `tests/providers/test_container_npu.py`
- **test_health_delegates_to_flm_tier1_when_slot_is_npu()** (4 connections) — `tests/providers/test_container_npu.py`
- **.test_command_env_memlock()** (4 connections) — `tests/providers/test_container_npu.py`
- **.test_loopback_publish_derived_from_spec_port()** (4 connections) — `tests/providers/test_container_npu.py`
- **test_health_200_on_health_still_healthy()** (3 connections) — `tests/providers/test_container_npu.py`
- **test_health_connect_refused_stays_unhealthy()** (3 connections) — `tests/providers/test_container_npu.py`
- **test_health_falls_back_to_v1_models()** (3 connections) — `tests/providers/test_container_npu.py`
- **test_health_no_delegation_without_slot_cfg()** (3 connections) — `tests/providers/test_container_npu.py`
- **test_health_v1_models_also_fails_unhealthy()** (3 connections) — `tests/providers/test_container_npu.py`
- **.test_cap_add_rendered()** (3 connections) — `tests/providers/test_container_npu.py`
- **.test_devices_and_mounts_in_argv()** (3 connections) — `tests/providers/test_container_npu.py`
- **.test_group_add_included()** (3 connections) — `tests/providers/test_container_npu.py`
- **.test_network_mode_host_rendered()** (3 connections) — `tests/providers/test_container_npu.py`
- **.test_security_opts_included()** (3 connections) — `tests/providers/test_container_npu.py`
- **.test_unit_has_service_section()** (3 connections) — `tests/providers/test_container_npu.py`
- **.test_unit_name_matches_template()** (3 connections) — `tests/providers/test_container_npu.py`
- **ContainerSpec** (2 connections)
- **Any** (1 connections)
- **ContainerProvider NPU branch: spec-rendered units + FLM health fallback (Phase A** (1 connections) — `tests/providers/test_container_npu.py`
- **PublishPort is rendered declaratively from spec.port, not extra_args.** (1 connections) — `tests/providers/test_container_npu.py`
- **FLM has no /health; 404 on /health + 200 on /v1/models → healthy.** (1 connections) — `tests/providers/test_container_npu.py`
- *... and 7 more nodes in this community*

## Relationships

- [ContainerProvider](ContainerProvider.md) (10 shared connections)
- [FLMProvider](FLMProvider.md) (2 shared connections)
- [Mount](Mount.md) (1 shared connections)

## Source Files

- `tests/providers/test_container_npu.py`

## Audit Trail

- EXTRACTED: 105 (91%)
- INFERRED: 10 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*