# test_router_loop.py

> 27 nodes

## Key Concepts

- **test_router_loop.py** (22 connections) — `tests/omni_router/test_router_loop.py`
- **_make_router()** (18 connections) — `tests/omni_router/test_router_loop.py`
- **_caller()** (17 connections) — `tests/omni_router/test_router_loop.py`
- **_img_slot()** (13 connections) — `tests/omni_router/test_router_loop.py`
- **test_route_to_chat_depth_limit_through_loop()** (7 connections) — `tests/omni_router/test_router_loop.py`
- **test_loop_dispatches_multiple_tool_calls_in_one_round()** (5 connections) — `tests/omni_router/test_router_loop.py`
- **test_loop_budget_terminates_on_pathological_tool_call_storm()** (5 connections) — `tests/omni_router/test_router_loop.py`
- **test_loop_forces_stream_false()** (5 connections) — `tests/omni_router/test_router_loop.py`
- **test_loop_strips_omni_knob_from_outbound_body()** (5 connections) — `tests/omni_router/test_router_loop.py`
- **test_loop_handles_dict_arguments_shape()** (5 connections) — `tests/omni_router/test_router_loop.py`
- **test_loop_handles_malformed_arguments_gracefully()** (5 connections) — `tests/omni_router/test_router_loop.py`
- **test_loop_exits_after_one_round_when_no_tool_calls()** (4 connections) — `tests/omni_router/test_router_loop.py`
- **test_loop_includes_active_tools_on_first_request()** (4 connections) — `tests/omni_router/test_router_loop.py`
- **test_loop_dispatches_single_tool_call_and_continues()** (4 connections) — `tests/omni_router/test_router_loop.py`
- **test_loop_skips_loop_when_no_tools_active()** (4 connections) — `tests/omni_router/test_router_loop.py`
- **test_active_tools_surface_round_trip()** (4 connections) — `tests/omni_router/test_router_loop.py`
- **test_dispatch_surface_round_trip()** (4 connections) — `tests/omni_router/test_router_loop.py`
- **Any** (3 connections)
- **test_loop_chat_completion_transport_error_returned()** (3 connections) — `tests/omni_router/test_router_loop.py`
- **OmniRouter.run_loop tests — plan §7.1 + ADR-0008 §8.  Covers the OpenAI tool-cal** (1 connections) — `tests/omni_router/test_router_loop.py`
- **Caller without ``tool-calling`` → no loop, single passthrough.** (1 connections) — `tests/omni_router/test_router_loop.py`
- **A model that emits tool_calls forever still terminates.** (1 connections) — `tests/omni_router/test_router_loop.py`
- **Plan deferral — PR-16 returns non-streaming responses; PR-18     layers streamin** (1 connections) — `tests/omni_router/test_router_loop.py`
- **The dispatcher's opt-in field ``omni`` is hal0-internal; it     must NOT be forw** (1 connections) — `tests/omni_router/test_router_loop.py`
- **The loop wires the chat_completion callback into the dispatch     context; route** (1 connections) — `tests/omni_router/test_router_loop.py`
- *... and 2 more nodes in this community*

## Relationships

- [make_slot](make_slot.md) (9 shared connections)
- [FakeSlotManager](FakeSlotManager.md) (4 shared connections)
- [OmniRouter](OmniRouter.md) (2 shared connections)

## Source Files

- `tests/omni_router/test_router_loop.py`

## Audit Trail

- EXTRACTED: 145 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*