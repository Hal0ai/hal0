# ApprovalQueue

> 41 nodes

## Key Concepts

- **ApprovalQueue** (105 connections) — `src/hal0/mcp/approval_queue.py`
- **test_approval_queue.py** (15 connections) — `tests/mcp/test_approval_queue.py`
- **_noop_executor()** (10 connections) — `tests/mcp/test_approval_queue.py`
- **test_admin_stacks.py** (8 connections) — `tests/mcp/test_admin_stacks.py`
- **Any** (4 connections)
- **test_unmapped_tool_distinct_args_get_distinct_ids()** (4 connections) — `tests/mcp/test_approval_queue.py`
- **test_unmapped_tool_identical_args_still_dedup()** (4 connections) — `tests/mcp/test_approval_queue.py`
- **test_dedup_pointer_cleared_after_resolution()** (4 connections) — `tests/mcp/test_approval_queue.py`
- **test_registered_tools_carry_their_annotations()** (3 connections) — `tests/mcp/test_admin.py`
- **test_build_server_advertises_shared_param_schema()** (3 connections) — `tests/mcp/test_admin.py`
- **test_memory_delete_single_id_autonomous()** (3 connections) — `tests/mcp/test_admin.py`
- **mock_transport()** (3 connections) — `tests/mcp/test_admin_stacks.py`
- **test_stack_list_dispatches_get()** (3 connections) — `tests/mcp/test_admin_stacks.py`
- **test_stack_status_substitutes_slug()** (3 connections) — `tests/mcp/test_admin_stacks.py`
- **test_approved_stack_apply_posts_to_apply_url()** (3 connections) — `tests/mcp/test_admin_stacks.py`
- **test_enqueue_returns_new_id()** (3 connections) — `tests/mcp/test_approval_queue.py`
- **test_enqueue_dedups_same_tool_and_target()** (3 connections) — `tests/mcp/test_approval_queue.py`
- **test_enqueue_distinct_targets_not_deduped()** (3 connections) — `tests/mcp/test_approval_queue.py`
- **test_unmapped_tool_approve_runs_correct_target()** (3 connections) — `tests/mcp/test_approval_queue.py`
- **test_double_resolve_raises_value_error()** (3 connections) — `tests/mcp/test_approval_queue.py`
- **test_subscriber_receives_lifecycle_events()** (3 connections) — `tests/mcp/test_approval_queue.py`
- **queue()** (2 connections) — `tests/mcp/test_admin_stacks.py`
- **test_stack_apply_gates_for_approval()** (2 connections) — `tests/mcp/test_admin_stacks.py`
- **test_approve_runs_executor_and_records_result()** (2 connections) — `tests/mcp/test_approval_queue.py`
- **test_approve_failure_lands_failed_state()** (2 connections) — `tests/mcp/test_approval_queue.py`
- *... and 16 more nodes in this community*

## Relationships

- [test_admin.py](test_admin.py.md) (22 shared connections)
- [KeyError](KeyError.md) (9 shared connections)
- [test_brain_injection.py](test_brain_injection.py.md) (8 shared connections)
- [test_approvals.py](test_approvals.py.md) (6 shared connections)
- [test_board_chat_admin_tools.py](test_board_chat_admin_tools.py.md) (6 shared connections)
- [test_board_chat_tool_use_e2e.py](test_board_chat_tool_use_e2e.py.md) (6 shared connections)
- [test_brain_resilience.py](test_brain_resilience.py.md) (5 shared connections)
- [approvals.py](approvals.py.md) (4 shared connections)
- [test_brain_read_only.py](test_brain_read_only.py.md) (4 shared connections)
- [admin.py](admin.py.md) (3 shared connections)
- [test_brain_ctx_precheck.py](test_brain_ctx_precheck.py.md) (3 shared connections)
- [test_brain_framing.py](test_brain_framing.py.md) (3 shared connections)

## Source Files

- `src/hal0/mcp/approval_queue.py`
- `tests/mcp/test_admin.py`
- `tests/mcp/test_admin_stacks.py`
- `tests/mcp/test_approval_queue.py`

## Audit Trail

- EXTRACTED: 151 (69%)
- INFERRED: 68 (31%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*