# make_slot

> 35 nodes · cohesion 0.10

## Key Concepts

- **make_slot()** (80 connections) — `tests/omni_router/conftest.py`
- **test_route_to_chat.py** (28 connections) — `tests/omni_router/test_route_to_chat.py`
- **validate_delegation()** (14 connections) — `src/hal0/omni_router/route_to_chat.py`
- **test_filter_no_labels.py** (10 connections) — `tests/omni_router/test_filter_no_labels.py`
- **chat_slot_has_tool_calling()** (9 connections) — `src/hal0/omni_router/filter.py`
- **test_legacy_labels_only_still_routes_tools()** (5 connections) — `tests/omni_router/test_filter_no_labels.py`
- **test_tool_calling_flag_alone_ships_tools_no_labels()** (5 connections) — `tests/omni_router/test_filter_no_labels.py`
- **test_tool_calling_flag_false_suppresses_tools_even_with_label()** (5 connections) — `tests/omni_router/test_filter_no_labels.py`
- **test_validate_depth_limit_rejected()** (4 connections) — `tests/omni_router/test_route_to_chat.py`
- **test_validate_npu_npu_rejected()** (4 connections) — `tests/omni_router/test_route_to_chat.py`
- **test_validate_target_wrong_type_rejected()** (4 connections) — `tests/omni_router/test_route_to_chat.py`
- **test_chat_slot_has_tool_calling_prefers_model_info()** (3 connections) — `tests/omni_router/test_filter_no_labels.py`
- **test_chat_slot_has_tool_calling_false_no_labels()** (3 connections) — `tests/omni_router/test_filter.py`
- **test_chat_slot_has_tool_calling_false_wrong_label()** (3 connections) — `tests/omni_router/test_filter.py`
- **test_chat_slot_has_tool_calling_true()** (3 connections) — `tests/omni_router/test_filter.py`
- **test_validate_gpu_to_npu_allowed()** (3 connections) — `tests/omni_router/test_route_to_chat.py`
- **test_validate_happy_path_returns_none()** (3 connections) — `tests/omni_router/test_route_to_chat.py`
- **test_validate_npu_to_gpu_allowed()** (3 connections) — `tests/omni_router/test_route_to_chat.py`
- **test_validate_self_delegation_rejected()** (3 connections) — `tests/omni_router/test_route_to_chat.py`
- **test_validate_target_disabled_is_rejected()** (3 connections) — `tests/omni_router/test_route_to_chat.py`
- **test_validate_target_missing()** (3 connections) — `tests/omni_router/test_route_to_chat.py`
- **test_build_messages_omits_context_when_none()** (2 connections) — `tests/omni_router/test_route_to_chat.py`
- **test_build_messages_omits_system_when_unset()** (2 connections) — `tests/omni_router/test_route_to_chat.py`
- **test_build_messages_with_system_prompt_and_context()** (2 connections) — `tests/omni_router/test_route_to_chat.py`
- **Return True iff the chat slot's model is allowed to see tools.      Per plan §7.** (1 connections) — `src/hal0/omni_router/filter.py`
- *... and 10 more nodes in this community*

## Relationships

- [FakeSlotManager](FakeSlotManager.md) (52 shared connections)
- [active_tools_for](active_tools_for.md) (24 shared connections)
- [test_router_loop.py](test_router_loop.py.md) (6 shared connections)
- [route_to_chat.py](route_to_chat.py.md) (4 shared connections)
- [test_model_meta.py](test_model_meta.py.md) (2 shared connections)
- [OmniRouter](OmniRouter.md) (2 shared connections)
- [RoutingHost](RoutingHost.md) (2 shared connections)
- [Any](Any.md) (1 shared connections)

## Source Files

- `src/hal0/omni_router/filter.py`
- `src/hal0/omni_router/route_to_chat.py`
- `tests/omni_router/conftest.py`
- `tests/omni_router/test_filter.py`
- `tests/omni_router/test_filter_no_labels.py`
- `tests/omni_router/test_route_to_chat.py`

## Audit Trail

- EXTRACTED: 184 (86%)
- INFERRED: 31 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*