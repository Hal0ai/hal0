# test_tools.py

> 34 nodes

## Key Concepts

- **test_tools.py** (19 connections) — `tests/omni_router/test_tools.py`
- **test_openai_tool_shape()** (3 connections) — `tests/omni_router/test_tools.py`
- **test_route_to_chat_has_no_endpoint()** (3 connections) — `tests/omni_router/test_tools.py`
- **test_tools_by_name_returns_fresh_dict()** (3 connections) — `tests/omni_router/test_tools.py`
- **test_immutable_tool_objects_shared_across_calls()** (3 connections) — `tests/omni_router/test_tools.py`
- **test_to_openai_tool_omits_hal0_metadata()** (3 connections) — `tests/omni_router/test_tools.py`
- **test_tool_count_is_eight()** (2 connections) — `tests/omni_router/test_tools.py`
- **test_each_tool_shape()** (2 connections) — `tests/omni_router/test_tools.py`
- **test_definitions_are_frozen()** (2 connections) — `tests/omni_router/test_tools.py`
- **test_parameters_are_valid_json_schema_objects()** (2 connections) — `tests/omni_router/test_tools.py`
- **test_required_fields_are_listed_in_properties()** (2 connections) — `tests/omni_router/test_tools.py`
- **test_pin_block_is_present()** (2 connections) — `tests/omni_router/test_tools.py`
- **test_only_route_to_chat_has_no_endpoint()** (2 connections) — `tests/omni_router/test_tools.py`
- **test_label_tuples_are_tuples_not_lists()** (2 connections) — `tests/omni_router/test_tools.py`
- **test_endpoint_path_format()** (2 connections) — `tests/omni_router/test_tools.py`
- **test_label_gated_tools_carry_at_least_one_label()** (2 connections) — `tests/omni_router/test_tools.py`
- **test_target_slot_types_are_canonical()** (2 connections) — `tests/omni_router/test_tools.py`
- **test_tool_names_match_expected()** (1 connections) — `tests/omni_router/test_tools.py`
- **Static checks on the tool_definitions.json contract.  We pin the eight-tool coun** (1 connections) — `tests/omni_router/test_tools.py`
- **Plan §7.2: v0.2 ships exactly 8 tools. ``recall_memory`` is v0.3+.** (1 connections) — `tests/omni_router/test_tools.py`
- **``ToolDefinition`` is frozen — mutating must raise.** (1 connections) — `tests/omni_router/test_tools.py`
- **``to_openai_tool`` returns the OpenAI ``tools=[...]`` wire shape.** (1 connections) — `tests/omni_router/test_tools.py`
- **Every tool's parameters must be an object-typed schema with     ``properties`` +** (1 connections) — `tests/omni_router/test_tools.py`
- **A required field must also be declared in ``properties``.** (1 connections) — `tests/omni_router/test_tools.py`
- **The ``_pin`` block — plan §7.5 — must accompany the tools list.      hal0 owns t** (1 connections) — `tests/omni_router/test_tools.py`
- *... and 9 more nodes in this community*

## Relationships

- [DispatchContext](DispatchContext.md) (6 shared connections)
- [OmniRouter](OmniRouter.md) (1 shared connections)

## Source Files

- `tests/omni_router/test_tools.py`

## Audit Trail

- EXTRACTED: 67 (92%)
- INFERRED: 6 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*