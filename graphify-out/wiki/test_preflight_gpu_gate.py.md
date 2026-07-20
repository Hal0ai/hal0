# test_preflight_gpu_gate.py

> 21 nodes · cohesion 0.14

## Key Concepts

- **test_preflight_gpu_gate.py** (10 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **_run_gpu_gate()** (9 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **Path** (4 connections)
- **test_doctor_mode_no_device_lxc_is_soft()** (4 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **test_gate_bare_metal_no_gpu_proceeds()** (4 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **test_gate_no_device_lxc_opt_in()** (4 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **render_glob()** (3 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **test_doctor_mode_broken_gid_is_soft()** (3 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **test_gate_broken_gid_lxc_hard_stops()** (3 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **test_gate_broken_gid_on_bare_metal_does_not_block()** (3 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **test_gate_device_good_gid_proceeds()** (3 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **Contract tests for ``preflight_gpu``'s install-time gate (WS-B, #1104).  ``prefl** (1 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **Device present + gid maps to a real group → proceed (0).** (1 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **Only an LXC miswire blocks — a bare-metal unmapped gid still proceeds.** (1 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **Without the gate flag (doctor), the same broken LXC stays soft (0).** (1 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **Without the gate flag, a no-device LXC also stays soft (0).** (1 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **Source preflight.sh and run ``preflight_gpu``, returning its rc.      ``set -euo** (1 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **A glob that matches a fake render node, so 'device present' is testable.** (1 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **Device present + unmapped render gid + LXC → BROKEN_GID (hard stop).** (1 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **No device + LXC → NO_DEVICE, so install.sh can offer a CPU-only opt-in.** (1 connections) — `tests/installer/test_preflight_gpu_gate.py`
- **Genuine bare-metal CPU box → proceed (0), no friction.** (1 connections) — `tests/installer/test_preflight_gpu_gate.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/installer/test_preflight_gpu_gate.py`

## Audit Trail

- EXTRACTED: 60 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*