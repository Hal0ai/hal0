# SystemCtlSeam

> 28 nodes

## Key Concepts

- **SystemCtlSeam** (30 connections) — `src/hal0/system/seam.py`
- **test_seam.py** (25 connections) — `tests/system/test_seam.py`
- **_recorder()** (20 connections) — `tests/system/test_seam.py`
- **Path** (11 connections)
- **test_write_unit_direct_when_not_hal0_user()** (4 connections) — `tests/system/test_seam.py`
- **test_write_unit_routes_through_seam_when_hal0_user()** (4 connections) — `tests/system/test_seam.py`
- **test_write_unit_rejects_non_slot_unit_name()** (4 connections) — `tests/system/test_seam.py`
- **test_write_quadlet_direct_when_not_hal0_user()** (4 connections) — `tests/system/test_seam.py`
- **test_write_quadlet_routes_through_seam_when_hal0_user()** (4 connections) — `tests/system/test_seam.py`
- **test_write_quadlet_rejects_non_slot_name()** (4 connections) — `tests/system/test_seam.py`
- **test_remove_quadlet_direct_when_not_hal0_user()** (4 connections) — `tests/system/test_seam.py`
- **test_remove_quadlet_routes_through_seam_when_hal0_user()** (4 connections) — `tests/system/test_seam.py`
- **test_remove_unit_direct_when_not_hal0_user()** (4 connections) — `tests/system/test_seam.py`
- **test_remove_unit_direct_noop_when_absent()** (4 connections) — `tests/system/test_seam.py`
- **test_remove_unit_routes_through_seam_when_hal0_user()** (4 connections) — `tests/system/test_seam.py`
- **test_systemctl_read_only_query_never_routed_even_as_hal0_user()** (4 connections) — `tests/system/test_seam.py`
- **test_systemctl_non_slot_unit_passes_through_even_as_hal0_user()** (4 connections) — `tests/system/test_seam.py`
- **test_systemctl_direct_when_not_hal0_user()** (3 connections) — `tests/system/test_seam.py`
- **test_systemctl_daemon_reload_routes_through_seam()** (3 connections) — `tests/system/test_seam.py`
- **test_systemctl_slot_unit_verbs_route_through_seam()** (3 connections) — `tests/system/test_seam.py`
- **test_systemctl_non_systemctl_argv_always_passes_through()** (3 connections) — `tests/system/test_seam.py`
- **test_restart_self_direct_when_not_hal0_user()** (3 connections) — `tests/system/test_seam.py`
- **test_restart_self_routes_through_seam_when_hal0_user()** (3 connections) — `tests/system/test_seam.py`
- **Direct systemd/file ops when not the hal0 service user; the     ``hal0-systemctl** (1 connections) — `src/hal0/system/seam.py`
- **_completed()** (1 connections) — `tests/system/test_seam.py`
- *... and 3 more nodes in this community*

## Relationships

- [._seam_argv](_seam_argv.md) (8 shared connections)
- [MonkeyPatch](MonkeyPatch.md) (3 shared connections)
- [_container_runtime](_container_runtime.md) (1 shared connections)
- [RuntimeError](RuntimeError.md) (1 shared connections)

## Source Files

- `src/hal0/system/seam.py`
- `tests/system/test_seam.py`

## Audit Trail

- EXTRACTED: 122 (76%)
- INFERRED: 39 (24%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*