# test_agent_approvals_list.py

> 26 nodes

## Key Concepts

- **test_agent_approvals_list.py** (13 connections) — `tests/cli/test_agent_approvals_list.py`
- **stub_api()** (7 connections) — `tests/cli/test_agent_approvals_list.py`
- **test_approvals_list_projects_tool_and_target_into_summary()** (4 connections) — `tests/cli/test_agent_approvals_list.py`
- **test_approvals_list_falls_back_to_dash_when_client_id_missing()** (4 connections) — `tests/cli/test_agent_approvals_list.py`
- **MonkeyPatch** (3 connections)
- **test_approvals_list_empty_short_circuits()** (3 connections) — `tests/cli/test_agent_approvals_list.py`
- **test_summary_uses_primary_target_arg_for_registered_tool()** (2 connections) — `tests/cli/test_agent_approvals_list.py`
- **test_summary_falls_back_to_first_scalar_for_unregistered_tool()** (2 connections) — `tests/cli/test_agent_approvals_list.py`
- **test_summary_renders_tool_alone_when_args_empty()** (2 connections) — `tests/cli/test_agent_approvals_list.py`
- **test_summary_truncates_to_60_chars()** (2 connections) — `tests/cli/test_agent_approvals_list.py`
- **test_summary_joins_list_primary_target()** (2 connections) — `tests/cli/test_agent_approvals_list.py`
- **test_fmt_enqueued_at_renders_iso_from_epoch()** (2 connections) — `tests/cli/test_agent_approvals_list.py`
- **test_fmt_enqueued_at_passes_through_non_float_strings()** (2 connections) — `tests/cli/test_agent_approvals_list.py`
- **test_fmt_enqueued_at_dash_for_missing()** (1 connections) — `tests/cli/test_agent_approvals_list.py`
- **Tests for ``hal0 agent approvals list`` table projection.  Regression coverage f** (1 connections) — `tests/cli/test_agent_approvals_list.py`
- **Stub the API layer so the CLI runs offline and we drive its input.** (1 connections) — `tests/cli/test_agent_approvals_list.py`
- **``model_delete`` → ``"model_delete <model_id>"`` via _PRIMARY_TARGET_ARG.** (1 connections) — `tests/cli/test_agent_approvals_list.py`
- **Unknown tools get a best-effort scalar so operators see context.** (1 connections) — `tests/cli/test_agent_approvals_list.py`
- **Empty args → bare tool name (no trailing space, no crash).** (1 connections) — `tests/cli/test_agent_approvals_list.py`
- **A pathological model id can't blow out the column width.** (1 connections) — `tests/cli/test_agent_approvals_list.py`
- **``memory_delete`` has a list-valued primary arg (ids=[…]).** (1 connections) — `tests/cli/test_agent_approvals_list.py`
- **Epoch float → short ISO with ``Z`` suffix (UTC), no microseconds.** (1 connections) — `tests/cli/test_agent_approvals_list.py`
- **If the API ever migrates to a pre-formatted ISO string, don't mangle it.** (1 connections) — `tests/cli/test_agent_approvals_list.py`
- **A pending ``model_delete`` row renders the model id in Summary,     the agent's** (1 connections) — `tests/cli/test_agent_approvals_list.py`
- **Agent column degrades to "—" when ``client_id`` is absent / empty.** (1 connections) — `tests/cli/test_agent_approvals_list.py`
- *... and 1 more nodes in this community*

## Relationships

- [test_hermes_provision_idempotency.py](test_hermes_provision_idempotency.py.md) (1 shared connections)

## Source Files

- `tests/cli/test_agent_approvals_list.py`

## Audit Trail

- EXTRACTED: 60 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*