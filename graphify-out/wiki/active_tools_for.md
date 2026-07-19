# active_tools_for

> 32 nodes · cohesion 0.13

## Key Concepts

- **active_tools_for()** (29 connections) — `src/hal0/omni_router/filter.py`
- **test_filter.py** (27 connections) — `tests/omni_router/test_filter.py`
- **_caller_with_tools_label()** (18 connections) — `tests/omni_router/test_filter.py`
- **test_image_slot_without_edit_label_skips_edit_image()** (6 connections) — `tests/omni_router/test_filter.py`
- **test_llm_without_vision_label_does_not_enable_analyze_image()** (6 connections) — `tests/omni_router/test_filter.py`
- **test_peer_chat_slot_enables_route_to_chat()** (6 connections) — `tests/omni_router/test_filter.py`
- **test_tool_order_matches_canonical_order()** (6 connections) — `tests/omni_router/test_filter.py`
- **test_all_slots_present_yields_all_eight_tools()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_caller_with_tool_calling_but_no_other_slots_returns_empty()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_caller_without_tool_calling_label_gets_empty_list()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_disabled_image_slot_disables_generate_image()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_disabled_peer_chat_slot_does_not_enable_route_to_chat()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_embedding_slot_enables_embed_text()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_image_slot_with_edit_label_enables_edit_image()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_missing_caller_slot_returns_empty_list()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_no_peer_chat_slot_disables_route_to_chat()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_reranking_slot_enables_rerank_documents()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_transcription_slot_enables_transcribe_audio()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_tts_slot_enables_text_to_speech()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_vision_capable_llm_enables_analyze_image()** (5 connections) — `tests/omni_router/test_filter.py`
- **test_image_slot_present_enables_generate_image()** (4 connections) — `tests/omni_router/test_filter.py`
- **_caller_no_tools_label()** (3 connections) — `tests/omni_router/test_filter.py`
- **Return the filtered tool list for a chat slot, per plan §7.3.      Args:** (1 connections) — `src/hal0/omni_router/filter.py`
- **Filter matrix tests — plan §7.3.  Covers the dynamic-filtering decision tree:** (1 connections) — `tests/omni_router/test_filter.py`
- **Plain LLM peer doesn't enable analyze_image — vision label required.** (1 connections) — `tests/omni_router/test_filter.py`
- *... and 7 more nodes in this community*

## Relationships

- [make_slot](make_slot.md) (24 shared connections)
- [FakeSlotManager](FakeSlotManager.md) (19 shared connections)
- [OmniRouter](OmniRouter.md) (6 shared connections)
- [tools_by_name](tools_by_name.md) (1 shared connections)

## Source Files

- `src/hal0/omni_router/filter.py`
- `tests/omni_router/test_filter.py`

## Audit Trail

- EXTRACTED: 140 (78%)
- INFERRED: 40 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*