# _Runner

> 17 nodes · cohesion 0.10

## Key Concepts

- **_Runner** (14 connections) — `src/hal0/hardware/gpu_view.py`
- **test_install_venv_sanitizes_leaked_root_home()** (5 connections) — `tests/agents/test_hermes_provision.py`
- **test_provision_via_uv_creates_install_dir_world_traversable()** (4 connections) — `tests/agents/test_hermes_provision.py`
- **test_provision_via_uv_sanitizes_leaked_root_home()** (4 connections) — `tests/agents/test_hermes_provision.py`
- **test_install_venv_keeps_supported_venv()** (3 connections) — `tests/agents/test_hermes_provision.py`
- **test_install_venv_rebuilds_venv_on_unsupported_interpreter()** (3 connections) — `tests/agents/test_hermes_provision.py`
- **test_delegation_targets_agent_slot_not_chat()** (2 connections) — `tests/agents/test_hermes_provision.py`
- **test_ensure_python_prefers_system_interpreter_over_uv()** (2 connections) — `tests/agents/test_hermes_provision.py`
- **test_ensure_python_provisions_via_uv_when_no_system_interpreter()** (2 connections) — `tests/agents/test_hermes_provision.py`
- **test_ensure_python_returns_none_when_uv_fetch_fails()** (2 connections) — `tests/agents/test_hermes_provision.py`
- **test_resolve_installer_root_falls_back_to_fhs_current()** (2 connections) — `tests/agents/test_hermes_provision.py`
- **Protocol** (1 connections)
- **.__call__()** (1 connections) — `src/hal0/hardware/gpu_view.py`
- **Delegation → `agent` MoE slot (thinking-off); chat stays on main model.      Thi** (1 connections) — `tests/agents/test_hermes_provision.py`
- **Non-editable FHS install: the package copy lives under the venv     (parents[3]** (1 connections) — `tests/agents/test_hermes_provision.py`
- **O15: uv must not inherit a leaked HOME=/root.      On a py3.14-only host the pro** (1 connections) — `tests/agents/test_hermes_provision.py`
- **O15 (same leak class): venv + pip subprocesses also get a sane HOME.** (1 connections) — `tests/agents/test_hermes_provision.py`

## Relationships

- [test_hermes_provision.py](test_hermes_provision.py.md) (17 shared connections)
- [sample](sample.md) (4 shared connections)

## Source Files

- `src/hal0/hardware/gpu_view.py`
- `tests/agents/test_hermes_provision.py`

## Audit Trail

- EXTRACTED: 33 (67%)
- INFERRED: 16 (33%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*