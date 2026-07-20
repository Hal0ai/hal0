# test_setup_install.py

> 45 nodes · cohesion 0.07

## Key Concepts

- **test_setup_install.py** (12 connections) — `tests/cli/test_setup_install.py`
- **setup_install.py** (9 connections) — `src/hal0/cli/setup_install.py`
- **_apply_in_process()** (9 connections) — `src/hal0/cli/setup_install.py`
- **_apply_via_api()** (8 connections) — `src/hal0/cli/setup_install.py`
- **_FakeAsyncClient** (8 connections) — `tests/cli/test_setup_install.py`
- **choose_apply_mode()** (6 connections) — `src/hal0/cli/setup_install.py`
- **run_install()** (6 connections) — `src/hal0/cli/setup_install.py`
- **_dashboard_url()** (5 connections) — `src/hal0/cli/setup_install.py`
- **_capture_apply_setup()** (5 connections) — `tests/cli/test_setup_install.py`
- **_install_fake_client()** (5 connections) — `tests/cli/test_setup_install.py`
- **_stub_offline_deps()** (5 connections) — `tests/cli/test_setup_install.py`
- **test_apply_in_process_falls_back_to_hugging_face_hub_token()** (5 connections) — `tests/cli/test_setup_install.py`
- **test_apply_in_process_no_token_passes_none()** (5 connections) — `tests/cli/test_setup_install.py`
- **test_apply_in_process_threads_hf_token()** (5 connections) — `tests/cli/test_setup_install.py`
- **test_apply_via_api_409_is_recoverable()** (5 connections) — `tests/cli/test_setup_install.py`
- **test_apply_via_api_non_conflict_error_still_raises()** (5 connections) — `tests/cli/test_setup_install.py`
- **_conflict_message()** (4 connections) — `src/hal0/cli/setup_install.py`
- **_run_pulls_with_progress()** (4 connections) — `src/hal0/cli/setup_install.py`
- **_empty_selections()** (4 connections) — `tests/cli/test_setup_install.py`
- **_api_reachable()** (3 connections) — `src/hal0/cli/setup_command.py`
- **test_mode_api_when_up()** (2 connections) — `tests/cli/test_setup_install.py`
- **test_mode_in_process_when_api_down()** (2 connections) — `tests/cli/test_setup_install.py`
- **Response** (1 connections)
- **Hybrid apply step for ``hal0 setup`` (spec §11, Task 4.1).  Both the ``--auto``** (1 connections) — `src/hal0/cli/setup_install.py`
- **Drive each plan while rendering one rich bar per model.      Each pull runs thro** (1 connections) — `src/hal0/cli/setup_install.py`
- *... and 20 more nodes in this community*

## Relationships

- [die](die.md) (3 shared connections)
- [build_auto_selections](build_auto_selections.md) (2 shared connections)
- [orchestrate.py](orchestrate.py.md) (2 shared connections)
- [Typer](Typer.md) (1 shared connections)
- [test_orchestrate.py](test_orchestrate.py.md) (1 shared connections)

## Source Files

- `src/hal0/cli/setup_command.py`
- `src/hal0/cli/setup_install.py`
- `tests/cli/test_setup_install.py`

## Audit Trail

- EXTRACTED: 121 (83%)
- INFERRED: 24 (17%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*