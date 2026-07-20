# ApprovalQueue

> God node · 105 connections · `src/hal0/mcp/approval_queue.py`

**Community:** [ApprovalQueue](ApprovalQueue.md)

## Connections by Relation

### calls
- [create_app()](create_app%28%29.md) `INFERRED`
- test_fabricated_approval_id_does_not_execute_gated_call() `EXTRACTED`
- test_hostile_card_text_cannot_flip_read_only() `EXTRACTED`
- test_injection_in_tool_result_still_gates_destructive_call() `EXTRACTED`
- test_gated_tool_emits_approval_required_frame() `EXTRACTED`
- _request() `INFERRED`
- test_only_real_approval_id_executes() `EXTRACTED`
- test_undecided_gate_times_out_without_executing() `EXTRACTED`
- test_persona_loosening_skips_approval_from_sidebar() `INFERRED`
- test_approving_mid_turn_resumes_with_executed_result() `EXTRACTED`
- test_denying_mid_turn_resumes_with_denial() `EXTRACTED`
- _request() `INFERRED`
- test_read_only_overrides_persona_auto_approve() `EXTRACTED`
- test_persona_cannot_loosen_destructive_floor() `INFERRED`
- test_dispatch_tool_refuses_disallowed_local_tool() `INFERRED`
- test_dispatch_tool_routes_admin_names_through_mcp_core() `INFERRED`
- _build_app() `EXTRACTED`
- test_gated_tool_returns_pending_approval_from_chat_path() `INFERRED`
- test_read_only_refuses_gated_before_enqueue() `EXTRACTED`
- test_dedup_pointer_cleared_after_resolution() `INFERRED`

### contains
- approval_queue.py `EXTRACTED`

### method
- .approve() `EXTRACTED`
- .deny() `EXTRACTED`
- .enqueue() `EXTRACTED`
- ._emit() `EXTRACTED`
- .get() `EXTRACTED`
- .list_all() `EXTRACTED`
- .list_pending() `EXTRACTED`
- .subscribe() `EXTRACTED`
- .__init__() `EXTRACTED`

### rationale_for
- Async-safe pending-approval queue with dedup + SSE fan-out.      One instance pe `EXTRACTED`

### references
- _fake_request() `EXTRACTED`
- _fake_request() `EXTRACTED`
- _fake_request() `EXTRACTED`
- _fake_request() `EXTRACTED`
- dispatch() `EXTRACTED`
- _queue() `EXTRACTED`
- build_server() `EXTRACTED`
- test_dispatch_policy_loosened_pull_runs_immediately() `EXTRACTED`
- test_double_approve_returns_409() `EXTRACTED`
- test_list_pending_returns_enqueued_entries() `EXTRACTED`
- test_autonomous_path_arg_resolution() `EXTRACTED`
- test_autonomous_write_runs_now_not_queued() `EXTRACTED`
- test_dispatch_policy_bulk_memory_delete_stays_gated() `EXTRACTED`
- test_list_wrap_does_not_touch_dict_payloads() `EXTRACTED`
- test_provider_list_wraps_bare_list_into_dict() `EXTRACTED`
- test_slot_list_wraps_bare_list_into_dict() `EXTRACTED`
- queue() `EXTRACTED`
- test_approve_runs_executor() `EXTRACTED`
- test_deny_does_not_run_executor() `EXTRACTED`
- test_approved_stack_apply_posts_to_apply_url() `EXTRACTED`

### uses
- _StubLLM `INFERRED`
- _RestRecorder `INFERRED`
- _FakeKanban `INFERRED`
- _StubLLM `INFERRED`
- _RestRecorder `INFERRED`
- _StubLLM `INFERRED`
- _RecordingClient `INFERRED`
- _RecordingLLM `INFERRED`
- ToolPolicy `INFERRED`
- _RecordingLLM `INFERRED`
- _FakeKanban `INFERRED`
- ApprovalNotFound `INFERRED`
- ApprovalAlreadyResolved `INFERRED`
- ApprovalQueueUnavailable `INFERRED`
- _FakeKanban `INFERRED`
- _RestRecorder `INFERRED`
- _FakeKanban `INFERRED`

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*