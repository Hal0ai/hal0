# install_openwebui

> 48 nodes

## Key Concepts

- **install_openwebui()** (14 connections) — `src/hal0/install/extensions.py`
- **extensions.py** (11 connections) — `src/hal0/install/extensions.py`
- **ExtensionOutcome** (11 connections) — `src/hal0/install/orchestrate.py`
- **test_extensions.py** (11 connections) — `tests/install/test_extensions.py`
- **test_app_install_openwebui.py** (8 connections) — `tests/cli/test_app_install_openwebui.py`
- **get_extension()** (7 connections) — `src/hal0/install/extensions.py`
- **install_extension()** (6 connections) — `src/hal0/install/extensions.py`
- **_run_ok()** (5 connections) — `src/hal0/install/extensions.py`
- **_podman_usable()** (4 connections) — `src/hal0/install/extensions.py`
- **_docker_present()** (4 connections) — `src/hal0/install/extensions.py`
- **_wait_active()** (4 connections) — `src/hal0/install/extensions.py`
- **test_extensions_comfyui.py** (4 connections) — `tests/install/test_extensions_comfyui.py`
- **_kind()** (3 connections) — `src/hal0/cli/setup_command.py`
- **Extension** (3 connections) — `src/hal0/install/extensions.py`
- **list_extensions()** (3 connections) — `src/hal0/install/extensions.py`
- **test_app_install_openwebui_slow_start_is_not_fatal()** (3 connections) — `tests/cli/test_app_install_openwebui.py`
- **test_registry_has_grouped_extensions()** (3 connections) — `tests/install/test_extensions.py`
- **test_install_openwebui_docker_only_host_gets_explicit_skip_reason()** (3 connections) — `tests/install/test_extensions.py`
- **_run()** (2 connections) — `src/hal0/install/extensions.py`
- **test_app_install_openwebui_success_does_not_exit()** (2 connections) — `tests/cli/test_app_install_openwebui.py`
- **test_app_install_openwebui_no_runtime_exits_nonzero()** (2 connections) — `tests/cli/test_app_install_openwebui.py`
- **test_app_install_openwebui_hard_failure_dies()** (2 connections) — `tests/cli/test_app_install_openwebui.py`
- **test_get_unknown_extension_returns_none()** (2 connections) — `tests/install/test_extensions.py`
- **test_install_agent_runs_hal0_agent_install()** (2 connections) — `tests/install/test_extensions.py`
- **test_install_extension_openwebui_delegates_to_install_openwebui()** (2 connections) — `tests/install/test_extensions.py`
- *... and 23 more nodes in this community*

## Relationships

- [orchestrate.py](orchestrate.py.md) (3 shared connections)
- [build_auto_selections](build_auto_selections.md) (2 shared connections)
- [app_commands.py](app_commands.py.md) (1 shared connections)
- [Typer](Typer.md) (1 shared connections)

## Source Files

- `src/hal0/cli/setup_command.py`
- `src/hal0/install/extensions.py`
- `src/hal0/install/orchestrate.py`
- `tests/cli/test_app_install_openwebui.py`
- `tests/install/test_extensions.py`
- `tests/install/test_extensions_comfyui.py`

## Audit Trail

- EXTRACTED: 116 (77%)
- INFERRED: 35 (23%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*