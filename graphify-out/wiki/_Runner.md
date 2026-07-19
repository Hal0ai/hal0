# _Runner

> 12 nodes

## Key Concepts

- **_Runner** (14 connections) — `src/hal0/hardware/gpu_view.py`
- **test_install_venv_sanitizes_leaked_root_home()** (5 connections) — `tests/agents/test_hermes_provision.py`
- **test_provision_via_uv_sanitizes_leaked_root_home()** (4 connections) — `tests/agents/test_hermes_provision.py`
- **test_provision_via_uv_creates_install_dir_world_traversable()** (4 connections) — `tests/agents/test_hermes_provision.py`
- **test_install_venv_rebuilds_venv_on_unsupported_interpreter()** (3 connections) — `tests/agents/test_hermes_provision.py`
- **test_install_venv_keeps_supported_venv()** (3 connections) — `tests/agents/test_hermes_provision.py`
- **test_ensure_python_prefers_system_interpreter_over_uv()** (2 connections) — `tests/agents/test_hermes_provision.py`
- **test_ensure_python_provisions_via_uv_when_no_system_interpreter()** (2 connections) — `tests/agents/test_hermes_provision.py`
- **test_ensure_python_returns_none_when_uv_fetch_fails()** (2 connections) — `tests/agents/test_hermes_provision.py`
- **.__call__()** (1 connections) — `src/hal0/hardware/gpu_view.py`
- **O15: uv must not inherit a leaked HOME=/root.      On a py3.14-only host the pro** (1 connections) — `tests/agents/test_hermes_provision.py`
- **O15 (same leak class): venv + pip subprocesses also get a sane HOME.** (1 connections) — `tests/agents/test_hermes_provision.py`

## Relationships

- [test_hermes_provision.py](test_hermes_provision.py.md) (8 shared connections)
- [Path](Path.md) (7 shared connections)
- [sample](sample.md) (4 shared connections)
- [compute_config_drift](compute_config_drift.md) (1 shared connections)

## Source Files

- `src/hal0/hardware/gpu_view.py`
- `tests/agents/test_hermes_provision.py`

## Audit Trail

- EXTRACTED: 26 (62%)
- INFERRED: 16 (38%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*