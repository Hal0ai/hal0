# orchestrate_models

> 29 nodes · cohesion 0.11

## Key Concepts

- **orchestrate_models()** (14 connections) — `src/hal0/comfyui/orchestrate.py`
- **test_orchestrate.py** (9 connections) — `tests/comfyui/test_orchestrate.py`
- **curated_set()** (8 connections) — `src/hal0/comfyui/orchestrate.py`
- **orchestrate.py** (7 connections) — `src/hal0/comfyui/orchestrate.py`
- **_default_log_path()** (5 connections) — `src/hal0/comfyui/orchestrate.py`
- **_noop_workflow()** (5 connections) — `tests/comfyui/test_orchestrate.py`
- **test_required_failure_fails_run_but_continues()** (5 connections) — `tests/comfyui/test_orchestrate.py`
- **orchestrate_models_cmd()** (4 connections) — `src/hal0/cli/comfyui_commands.py`
- **FamilyResult** (4 connections) — `src/hal0/comfyui/orchestrate.py`
- **_make_runner()** (4 connections) — `tests/comfyui/test_orchestrate.py`
- **test_all_success()** (4 connections) — `tests/comfyui/test_orchestrate.py`
- **test_optional_failure_tolerated()** (4 connections) — `tests/comfyui/test_orchestrate.py`
- **comfyui_commands.py** (3 connections) — `src/hal0/cli/comfyui_commands.py`
- **_default_runner()** (3 connections) — `src/hal0/comfyui/orchestrate.py`
- **_all_ok_runner()** (3 connections) — `tests/comfyui/test_orchestrate.py`
- **test_default_log_path_used_when_unspecified()** (3 connections) — `tests/comfyui/test_orchestrate.py`
- **Path** (2 connections)
- **test_curated_set_covers_all_capabilities()** (2 connections) — `tests/comfyui/test_orchestrate.py`
- **TextIO** (2 connections)
- **hal0 comfyui subcommands — ComfyUI model provisioning helpers.  Currently expose** (1 connections) — `src/hal0/cli/comfyui_commands.py`
- **Pull the curated ComfyUI model set end-to-end, logging each step.      Runs the** (1 connections) — `src/hal0/cli/comfyui_commands.py`
- **#1199: orchestrate the curated ComfyUI model set in one command.  Operators prev** (1 connections) — `src/hal0/comfyui/orchestrate.py`
- **Run the curated ComfyUI model pull sequence end-to-end, writing a log.      Each** (1 connections) — `src/hal0/comfyui/orchestrate.py`
- **Per-family outcome of an orchestration run.** (1 connections) — `src/hal0/comfyui/orchestrate.py`
- **Curated ``(capability_id, default variant)`` pairs, in CAPABILITIES order.** (1 connections) — `src/hal0/comfyui/orchestrate.py`
- *... and 4 more nodes in this community*

## Relationships

- [ModelVariant](ModelVariant.md) (4 shared connections)
- [OrchestrationResult](OrchestrationResult.md) (2 shared connections)
- [Typer](Typer.md) (1 shared connections)
- [model_store_root](model_store_root.md) (1 shared connections)
- [realtime_ws](realtime_ws.md) (1 shared connections)

## Source Files

- `src/hal0/cli/comfyui_commands.py`
- `src/hal0/comfyui/orchestrate.py`
- `tests/comfyui/test_orchestrate.py`

## Audit Trail

- EXTRACTED: 71 (70%)
- INFERRED: 30 (30%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*