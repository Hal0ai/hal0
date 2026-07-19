# Typer

> 32 nodes

## Key Concepts

- **Typer** (37 connections)
- **memory_ops_commands.py** (7 connections) — `src/hal0/cli/memory_ops_commands.py`
- **ops_retry_cmd()** (7 connections) — `src/hal0/cli/memory_ops_commands.py`
- **test_memory_recall_commands.py** (7 connections) — `tests/cli/test_memory_recall_commands.py`
- **ops_list_cmd()** (6 connections) — `src/hal0/cli/memory_ops_commands.py`
- **_require_api()** (5 connections) — `src/hal0/cli/memory_ops_commands.py`
- **_list_bank_ops()** (5 connections) — `src/hal0/cli/memory_ops_commands.py`
- **test_cli_docs_parity.py** (5 connections) — `tests/cli/test_cli_docs_parity.py`
- **_walk()** (5 connections) — `tests/cli/test_cli_docs_parity.py`
- **_all_bank_ids()** (4 connections) — `src/hal0/cli/memory_ops_commands.py`
- **bench_commands.py** (3 connections) — `src/hal0/cli/bench_commands.py`
- **memory_recall_commands.py** (3 connections) — `src/hal0/cli/memory_recall_commands.py`
- **test_allowed_missing_are_still_real_commands()** (3 connections) — `tests/cli/test_cli_docs_parity.py`
- **bench()** (2 connections) — `src/hal0/cli/bench_commands.py`
- **test_cli_mdx_documents_every_command()** (2 connections) — `tests/cli/test_cli_docs_parity.py`
- **stub_api()** (2 connections) — `tests/cli/test_memory_recall_commands.py`
- **Context** (1 connections)
- **`hal0 bench` — benchmarking CLI (design §5), thin mount over hal0.bench.cli.  Th** (1 connections) — `src/hal0/cli/bench_commands.py`
- **Any** (1 connections)
- **``hal0 memory ops`` — cross-bank async-operation admin.  Hindsight's operations** (1 connections) — `src/hal0/cli/memory_ops_commands.py`
- **List async operations (retain, consolidation, refresh_mental_model, ...).** (1 connections) — `src/hal0/cli/memory_ops_commands.py`
- **Retry failed operations — either one by id, or every failed op in scope.** (1 connections) — `src/hal0/cli/memory_ops_commands.py`
- **``hal0 memory recall`` — debug recall through the ACL front door.  Hits ``POST /** (1 connections) — `src/hal0/cli/memory_recall_commands.py`
- **Guard: docs/reference/cli.mdx must document every real CLI command.  Issue #501** (1 connections) — `tests/cli/test_cli_docs_parity.py`
- **Return every full command path the Typer app exposes.      e.g. ``["status", "sl** (1 connections) — `tests/cli/test_cli_docs_parity.py`
- *... and 7 more nodes in this community*

## Relationships

- [die](die.md) (11 shared connections)
- [agent_commands.py](agent_commands.py.md) (1 shared connections)
- [app_commands.py](app_commands.py.md) (1 shared connections)
- [CapabilitySelection](CapabilitySelection.md) (1 shared connections)
- [_run_nonstreaming_turn](_run_nonstreaming_turn.md) (1 shared connections)
- [orchestrate_models](orchestrate_models.md) (1 shared connections)
- [Enum](Enum.md) (1 shared connections)
- [Check](Check.md) (1 shared connections)
- [_write_diagnostics_section](_write_diagnostics_section.md) (1 shared connections)
- [doctor_commands.py](doctor_commands.py.md) (1 shared connections)
- [main.py](main.py.md) (1 shared connections)
- [memory_bank_commands.py](memory_bank_commands.py.md) (1 shared connections)

## Source Files

- `src/hal0/cli/bench_commands.py`
- `src/hal0/cli/memory_ops_commands.py`
- `src/hal0/cli/memory_recall_commands.py`
- `tests/cli/test_cli_docs_parity.py`
- `tests/cli/test_memory_recall_commands.py`

## Audit Trail

- EXTRACTED: 112 (94%)
- INFERRED: 7 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*