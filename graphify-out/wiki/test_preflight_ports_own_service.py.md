# test_preflight_ports_own_service.py

> 17 nodes

## Key Concepts

- **test_preflight_ports_own_service.py** (10 connections) — `tests/installer/test_preflight_ports_own_service.py`
- **Path** (9 connections)
- **_fake_ss()** (8 connections) — `tests/installer/test_preflight_ports_own_service.py`
- **_fake_systemctl()** (7 connections) — `tests/installer/test_preflight_ports_own_service.py`
- **_run_preflight_ports()** (7 connections) — `tests/installer/test_preflight_ports_own_service.py`
- **test_own_service_port_passes()** (6 connections) — `tests/installer/test_preflight_ports_own_service.py`
- **test_foreign_process_port_hard_fails()** (6 connections) — `tests/installer/test_preflight_ports_own_service.py`
- **test_soft_mode_warns_regardless_of_ownership()** (6 connections) — `tests/installer/test_preflight_ports_own_service.py`
- **test_either_own_unit_is_recognised()** (6 connections) — `tests/installer/test_preflight_ports_own_service.py`
- **test_no_systemctl_hard_fails_like_before()** (5 connections) — `tests/installer/test_preflight_ports_own_service.py`
- **_write_exec()** (4 connections) — `tests/installer/test_preflight_ports_own_service.py`
- **Contract tests for ``preflight_ports``' own-service detection (#F24).  install.s** (1 connections) — `tests/installer/test_preflight_ports_own_service.py`
- **Port held by hal0-api's own MainPID → OK (0), not a hard fail.** (1 connections) — `tests/installer/test_preflight_ports_own_service.py`
- **Port held by an unrelated process (no unit MainPID match) → hard fail.** (1 connections) — `tests/installer/test_preflight_ports_own_service.py`
- **No systemctl on PATH (non-systemd host) → falls back to the old hard fail.** (1 connections) — `tests/installer/test_preflight_ports_own_service.py`
- **HAL0_DOCTOR_PORTS_SOFT=1 (hal0 doctor) still just warns — unchanged.** (1 connections) — `tests/installer/test_preflight_ports_own_service.py`
- **Both hal0-api and hal0-openwebui are recognised as own-service owners.** (1 connections) — `tests/installer/test_preflight_ports_own_service.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/installer/test_preflight_ports_own_service.py`

## Audit Trail

- EXTRACTED: 80 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*