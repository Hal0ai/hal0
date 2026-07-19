# test_hermes_provision_idempotency.py

> 41 nodes · cohesion 0.09

## Key Concepts

- **test_hermes_provision_idempotency.py** (20 connections) — `tests/agents/test_hermes_provision_idempotency.py`
- **_install()** (15 connections) — `tests/agents/test_hermes_provision_idempotency.py`
- **install_io()** (11 connections) — `tests/agents/_hermes_fakes.py`
- **Path** (11 connections)
- **_config()** (8 connections) — `tests/agents/test_hermes_provision_idempotency.py`
- **sandbox_hermes_paths()** (6 connections) — `tests/agents/_hermes_fakes.py`
- **test_double_run_zero_mutating_steps()** (6 connections) — `tests/agents/test_hermes_provision_idempotency.py`
- **test_two_consecutive_runs_converge()** (6 connections) — `tests/agents/test_hermes_provision_idempotency.py`
- **target()** (5 connections) — `tests/agents/test_hermes_provision_idempotency.py`
- **test_config_yaml_contains_chat_slot_aliases()** (5 connections) — `tests/agents/test_hermes_provision_idempotency.py`
- **test_config_yaml_contains_mcp_servers()** (5 connections) — `tests/agents/test_hermes_provision_idempotency.py`
- **test_config_yaml_contains_persona_prelude()** (5 connections) — `tests/agents/test_hermes_provision_idempotency.py`
- **test_config_yaml_contains_role_slot_blocks()** (5 connections) — `tests/agents/test_hermes_provision_idempotency.py`
- **test_repair_run_rewrites_persona_seeds()** (5 connections) — `tests/agents/test_hermes_provision_idempotency.py`
- **install_target()** (5 connections) — `tests/agents/test_hermes_provision.py`
- **test_report_written_for_agent_status()** (4 connections) — `tests/agents/test_hermes_provision_idempotency.py`
- **install_fake_io()** (4 connections) — `tests/agents/test_hermes_provision.py`
- **_register()** (3 connections) — `tests/agents/test_hermes_provision_idempotency.py`
- **test_namespace_register_rewrites_when_delete_count_matches()** (3 connections) — `tests/agents/test_hermes_provision_idempotency.py`
- **test_namespace_register_skips_add_on_delete_count_mismatch()** (3 connections) — `tests/agents/test_hermes_provision_idempotency.py`
- **test_mcp_wire_runs_before_config_write()** (2 connections) — `tests/agents/test_hermes_provision_idempotency.py`
- **test_persona_seed_runs_before_config_write()** (2 connections) — `tests/agents/test_hermes_provision_idempotency.py`
- **Redirect every host path constant under ``tmp_path``; return (home, venv).** (1 connections) — `tests/agents/_hermes_fakes.py`
- **A deterministic :class:`hp.InstallIO` for hermetic ``install_hermes`` runs.** (1 connections) — `tests/agents/_hermes_fakes.py`
- **MonkeyPatch** (1 connections)
- *... and 16 more nodes in this community*

## Relationships

- [test_hermes_provision.py](test_hermes_provision.py.md) (7 shared connections)
- [_hermes_fakes.py](_hermes_fakes.py.md) (4 shared connections)
- [test_agent_uninstall_memory.py](test_agent_uninstall_memory.py.md) (2 shared connections)
- [Path](Path.md) (1 shared connections)
- [test_agent_approvals_list.py](test_agent_approvals_list.py.md) (1 shared connections)
- [test_capabilities_commands.py](test_capabilities_commands.py.md) (1 shared connections)

## Source Files

- `tests/agents/_hermes_fakes.py`
- `tests/agents/test_hermes_provision.py`
- `tests/agents/test_hermes_provision_idempotency.py`

## Audit Trail

- EXTRACTED: 153 (97%)
- INFERRED: 5 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*