# build_per_slot

> 17 nodes

## Key Concepts

- **build_per_slot()** (17 connections) — `src/hal0/slots/capacity.py`
- **TestBuildPerSlotContainerPath** (9 connections) — `tests/slots/test_container_cgroup_mem.py`
- **._make_slot()** (7 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_container_slot_uses_cgroup_bytes()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_container_under_report_uses_estimate_floor()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_container_cgroup_wins_when_above_estimate()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_empty_cgroup_probe_falls_back_to_registry()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_npu_slot_skips_cgroup_probe()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **.test_offline_slot_omitted()** (4 connections) — `tests/slots/test_container_cgroup_mem.py`
- **Build the ``per_slot`` memory map for loaded slots.      For every slot in a res** (1 connections) — `src/hal0/slots/capacity.py`
- **Verify build_per_slot uses cgroup bytes for container slots     and falls back t** (1 connections) — `tests/slots/test_container_cgroup_mem.py`
- **When cgroup exceeds the (zero) estimate, build_per_slot uses the cgroup value.** (1 connections) — `tests/slots/test_container_cgroup_mem.py`
- **#672 regression: Strix Halo GTT weights not charged to cgroup.          When the** (1 connections) — `tests/slots/test_container_cgroup_mem.py`
- **When the cgroup DOES account for weights it exceeds the estimate and wins.** (1 connections) — `tests/slots/test_container_cgroup_mem.py`
- **When cgroup probe returns 0, build_per_slot uses registry file size.** (1 connections) — `tests/slots/test_container_cgroup_mem.py`
- **NPU/FLM slots use the FLM footprint path and never call the cgroup probe.** (1 connections) — `tests/slots/test_container_cgroup_mem.py`
- **Slots in non-resident states produce no row.** (1 connections) — `tests/slots/test_container_cgroup_mem.py`

## Relationships

- [capacity.py](capacity.py.md) (3 shared connections)
- [_container_cgroup_mem_bytes](_container_cgroup_mem_bytes.md) (2 shared connections)
- [hardware.py](hardware.py.md) (1 shared connections)
- [slots.py](slots.py.md) (1 shared connections)
- [.tick](tick.md) (1 shared connections)
- [flm.py](flm.py.md) (1 shared connections)
- [CapacityProbeError](CapacityProbeError.md) (1 shared connections)
- [SlotState](SlotState.md) (1 shared connections)

## Source Files

- `src/hal0/slots/capacity.py`
- `tests/slots/test_container_cgroup_mem.py`

## Audit Trail

- EXTRACTED: 48 (74%)
- INFERRED: 17 (26%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*