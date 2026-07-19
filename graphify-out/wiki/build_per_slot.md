# build_per_slot

> 55 nodes · cohesion 0.06

## Key Concepts

- **build_per_slot()** (17 connections) — `src/hal0/slots/capacity.py`
- **_container_cgroup_mem_bytes()** (11 connections) — `src/hal0/slots/capacity.py`
- **TestContainerCgroupMemBytes** (10 connections) — `tests/slots/test_container_cgroup_mem.py`
- **capacity.py** (9 connections) — `src/hal0/slots/capacity.py`
- **_make_proc()** (9 connections) — `tests/slots/test_container_cgroup_mem.py`
- **TestBuildPerSlotContainerPath** (9 connections) — `tests/slots/test_container_cgroup_mem.py`
- **._make_slot()** (7 connections) — `tests/slots/test_container_cgroup_mem.py`
- **CapacitySnapshot** (6 connections) — `src/hal0/slots/capacity.py`
- **.probe()** (6 connections) — `src/hal0/slots/capacity.py`
- **_ctx_tokens_for()** (4 connections) — `src/hal0/slots/capacity.py`
- **Any** (4 connections)
- **test_container_cgroup_mem.py** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_container_cgroup_wins_when_above_estimate()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_container_slot_uses_cgroup_bytes()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_container_under_report_uses_estimate_floor()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_empty_cgroup_probe_falls_back_to_registry()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_npu_slot_skips_cgroup_probe()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_offline_slot_omitted()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_docker_fallback_when_podman_absent()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_returns_bytes_on_happy_path()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_returns_zero_on_cgroup_v1()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_returns_zero_on_inspect_timeout()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_returns_zero_when_container_not_found()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_returns_zero_when_memory_current_unreadable()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_returns_zero_when_pid_is_zero()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- *... and 30 more nodes in this community*

## Relationships

- [ReaperHost](ReaperHost.md) (5 shared connections)
- [hardware.py](hardware.py.md) (1 shared connections)
- [slots.py](slots.py.md) (1 shared connections)
- [_probe_power](_probe_power.md) (1 shared connections)
- [flm.py](flm.py.md) (1 shared connections)
- [SlotManager](SlotManager.md) (1 shared connections)
- [SlotConfigError](SlotConfigError.md) (1 shared connections)
- [HardwareInfo](HardwareInfo.md) (1 shared connections)

## Source Files

- `src/hal0/slots/capacity.py`
- `tests/slots/test_container_cgroup_mem.py`

## Audit Trail

- EXTRACTED: 149 (81%)
- INFERRED: 35 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*