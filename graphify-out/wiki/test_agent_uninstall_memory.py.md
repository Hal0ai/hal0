# test_agent_uninstall_memory.py

> 34 nodes · cohesion 0.12

## Key Concepts

- **test_agent_uninstall_memory.py** (19 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **MemoryUninstallOutcome** (13 connections) — `src/hal0/cli/agent_commands.py`
- **MonkeyPatch** (11 connections)
- **stub_uninstall_api()** (11 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **fake_urlopen()** (10 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **_stub_memory_outcome()** (8 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **test_cli_proceeds_without_force_when_confirmed()** (6 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **test_cli_silent_on_deleted_outcome()** (5 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **test_cli_silent_on_not_found_outcome()** (5 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **test_cli_warns_on_leftover_outcome()** (5 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **test_cli_warns_on_unreachable_outcome()** (5 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **test_cli_aborts_without_force_when_declined()** (4 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **test_cli_keep_memory_skips_outcome_path()** (4 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **stub_api_base()** (3 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **test_outcome_deleted_when_delete_succeeds_and_verify_empty()** (3 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **test_outcome_deleted_when_verify_call_itself_unreachable()** (3 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **test_outcome_leftover_when_verify_still_finds_rows()** (3 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **test_outcome_not_found_when_search_returns_zero_rows()** (3 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **test_outcome_unreachable_when_delete_raises()** (3 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **test_outcome_unreachable_when_search_raises()** (3 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **Structured result from ``_uninstall_hermes_memory``.      Attributes     -------** (1 connections) — `src/hal0/cli/agent_commands.py`
- **Tests for ``_uninstall_hermes_memory`` outcome reporting (#350).  Pre-fix the he** (1 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **Pre-search has rows → delete OK → verify-search empty → outcome=deleted.** (1 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **The 2026-05-26 incident: delete returns OK but rows survive.** (1 connections) — `tests/cli/test_agent_uninstall_memory.py`
- **Search raises URLError → outcome=unreachable, leftover_count=None.** (1 connections) — `tests/cli/test_agent_uninstall_memory.py`
- *... and 9 more nodes in this community*

## Relationships

- [agent_commands.py](agent_commands.py.md) (3 shared connections)
- [test_hermes_provision_idempotency.py](test_hermes_provision_idempotency.py.md) (2 shared connections)
- [hermes_provision.py](hermes_provision.py.md) (1 shared connections)
- [AgentManager](AgentManager.md) (1 shared connections)
- [_shared.py](_shared.py.md) (1 shared connections)
- [_FakeResponse](_FakeResponse.md) (1 shared connections)

## Source Files

- `src/hal0/cli/agent_commands.py`
- `tests/cli/test_agent_uninstall_memory.py`

## Audit Trail

- EXTRACTED: 136 (96%)
- INFERRED: 5 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*