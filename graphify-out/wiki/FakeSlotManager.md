# FakeSlotManager

> 51 nodes

## Key Concepts

- **FakeSlotManager** (57 connections) — `tests/omni_router/conftest.py`
- **active_tools_for()** (29 connections) — `src/hal0/omni_router/filter.py`
- **test_filter.py** (27 connections) — `tests/omni_router/test_filter.py`
- **_caller_with_tools_label()** (18 connections) — `tests/omni_router/test_filter.py`
- **conftest.py** (10 connections) — `tests/omni_router/conftest.py`
- **test_filter_no_labels.py** (10 connections) — `tests/omni_router/test_filter_no_labels.py`
- **test_image_slot_without_edit_label_skips_edit_image()** (6 connections) — `tests/omni_router/test_filter.py`
- **test_llm_without_vision_label_does_not_enable_analyze_image()** (6 connections) — `tests/omni_router/test_filter.py`
- **test_peer_chat_slot_enables_route_to_chat()** (6 connections) — `tests/omni_router/test_filter.py`
- **test_tool_order_matches_canonical_order()** (6 connections) — `tests/omni_router/test_filter.py`
- **test_caller_without_tool_calling_label_gets_empty_list()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_caller_with_tool_calling_but_no_other_slots_returns_empty()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_image_slot_with_edit_label_enables_edit_image()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_tts_slot_enables_text_to_speech()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_transcription_slot_enables_transcribe_audio()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_embedding_slot_enables_embed_text()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_reranking_slot_enables_rerank_documents()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_vision_capable_llm_enables_analyze_image()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_no_peer_chat_slot_disables_route_to_chat()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_disabled_peer_chat_slot_does_not_enable_route_to_chat()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_all_slots_present_yields_all_eight_tools()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_missing_caller_slot_returns_empty_list()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_disabled_image_slot_disables_generate_image()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_tool_calling_flag_alone_ships_tools_no_labels()** (5 connections) — `tests/omni_router/test_filter_no_labels.py`
- **test_tool_calling_flag_false_suppresses_tools_even_with_label()** (5 connections) — `tests/omni_router/test_filter_no_labels.py`
- *... and 26 more nodes in this community*

## Relationships

- [make_slot](make_slot.md) (51 shared connections)
- [SlotManagerLike](SlotManagerLike.md) (10 shared connections)
- [RoutingHost](RoutingHost.md) (7 shared connections)
- [test_router_loop.py](test_router_loop.py.md) (4 shared connections)
- [OmniRouter](OmniRouter.md) (2 shared connections)

## Source Files

- `src/hal0/omni_router/filter.py`
- `tests/omni_router/conftest.py`
- `tests/omni_router/test_filter.py`
- `tests/omni_router/test_filter_no_labels.py`

## Audit Trail

- EXTRACTED: 248 (84%)
- INFERRED: 46 (16%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*