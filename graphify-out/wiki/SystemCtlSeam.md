# SystemCtlSeam

> 60 nodes · cohesion 0.07

## Key Concepts

- **SystemCtlSeam** (30 connections) — `src/hal0/system/seam.py`
- **test_seam.py** (25 connections) — `tests/system/test_seam.py`
- **_recorder()** (20 connections) — `tests/system/test_seam.py`
- **Path** (11 connections)
- **._seam_argv()** (7 connections) — `src/hal0/system/seam.py`
- **seam.py** (6 connections) — `src/hal0/system/seam.py`
- **_slot_id_from_unit()** (5 connections) — `src/hal0/system/seam.py`
- **.remove_quadlet()** (5 connections) — `src/hal0/system/seam.py`
- **.remove_unit()** (5 connections) — `src/hal0/system/seam.py`
- **.systemctl()** (5 connections) — `src/hal0/system/seam.py`
- **.write_quadlet()** (5 connections) — `src/hal0/system/seam.py`
- **.write_unit()** (5 connections) — `src/hal0/system/seam.py`
- **Path** (4 connections)
- **_slot_id_from_quadlet()** (4 connections) — `src/hal0/system/seam.py`
- **.restart_self()** (4 connections) — `src/hal0/system/seam.py`
- **test_remove_quadlet_direct_when_not_hal0_user()** (4 connections) — `tests/system/test_seam.py`
- **test_remove_quadlet_routes_through_seam_when_hal0_user()** (4 connections) — `tests/system/test_seam.py`
- **test_remove_unit_direct_noop_when_absent()** (4 connections) — `tests/system/test_seam.py`
- **test_remove_unit_direct_when_not_hal0_user()** (4 connections) — `tests/system/test_seam.py`
- **test_remove_unit_routes_through_seam_when_hal0_user()** (4 connections) — `tests/system/test_seam.py`
- **test_systemctl_non_slot_unit_passes_through_even_as_hal0_user()** (4 connections) — `tests/system/test_seam.py`
- **test_systemctl_read_only_query_never_routed_even_as_hal0_user()** (4 connections) — `tests/system/test_seam.py`
- **test_write_quadlet_direct_when_not_hal0_user()** (4 connections) — `tests/system/test_seam.py`
- **test_write_quadlet_rejects_non_slot_name()** (4 connections) — `tests/system/test_seam.py`
- **test_write_quadlet_routes_through_seam_when_hal0_user()** (4 connections) — `tests/system/test_seam.py`
- *... and 35 more nodes in this community*

## Relationships

- [updater.py](updater.py.md) (1 shared connections)
- [ContainerProvider](ContainerProvider.md) (1 shared connections)

## Source Files

- `src/hal0/system/seam.py`
- `tests/system/test_seam.py`

## Audit Trail

- EXTRACTED: 210 (84%)
- INFERRED: 40 (16%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*