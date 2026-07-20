# tools_by_name

> 30 nodes · cohesion 0.18

## Key Concepts

- **tools_by_name()** (16 connections) — `src/hal0/omni_router/tools.py`
- **dispatch.py** (15 connections) — `src/hal0/omni_router/dispatch.py`
- **Any** (13 connections)
- **_route_or_error()** (12 connections) — `src/hal0/omni_router/dispatch.py`
- **ToolDefinition** (12 connections) — `src/hal0/omni_router/tools.py`
- **_missing()** (11 connections) — `src/hal0/omni_router/dispatch.py`
- **_post_json()** (11 connections) — `src/hal0/omni_router/dispatch.py`
- **_model_id_of()** (10 connections) — `src/hal0/omni_router/dispatch.py`
- **handle_analyze_image()** (8 connections) — `src/hal0/omni_router/dispatch.py`
- **handle_edit_image()** (8 connections) — `src/hal0/omni_router/dispatch.py`
- **handle_embed_text()** (8 connections) — `src/hal0/omni_router/dispatch.py`
- **handle_generate_image()** (8 connections) — `src/hal0/omni_router/dispatch.py`
- **handle_rerank_documents()** (8 connections) — `src/hal0/omni_router/dispatch.py`
- **handle_route_to_chat()** (8 connections) — `src/hal0/omni_router/dispatch.py`
- **handle_text_to_speech()** (8 connections) — `src/hal0/omni_router/dispatch.py`
- **handle_transcribe_audio()** (8 connections) — `src/hal0/omni_router/dispatch.py`
- **dispatch_tool()** (5 connections) — `src/hal0/omni_router/dispatch.py`
- **tools.py** (4 connections) — `src/hal0/omni_router/tools.py`
- **_load_tool_definitions()** (3 connections) — `src/hal0/omni_router/tools.py`
- **test_definitions_round_trip_through_isinstance()** (2 connections) — `tests/omni_router/test_tools.py`
- **OmniRouter tool dispatch handlers — plan §7.  Each of the eight tools has a coro** (1 connections) — `src/hal0/omni_router/dispatch.py`
- **Return an error message if any required key is missing/empty,     else ``None``.** (1 connections) — `src/hal0/omni_router/dispatch.py`
- **Resolve the target loaded slot for ``tool``.      Returns ``(loaded_slot, None)`** (1 connections) — `src/hal0/omni_router/dispatch.py`
- **POST JSON to a hal0 /v1 endpoint; return parsed body or an     ``{"error": ...}`** (1 connections) — `src/hal0/omni_router/dispatch.py`
- **Special-case dispatcher — see :mod:`hal0.omni_router.route_to_chat`.      Valida** (1 connections) — `src/hal0/omni_router/dispatch.py`
- *... and 5 more nodes in this community*

## Relationships

- [FakeSlotManager](FakeSlotManager.md) (13 shared connections)
- [test_tools.py](test_tools.py.md) (7 shared connections)
- [OmniRouter](OmniRouter.md) (4 shared connections)
- [route_to_chat.py](route_to_chat.py.md) (2 shared connections)
- [active_tools_for](active_tools_for.md) (1 shared connections)
- [.to_openai_tool](to_openai_tool.md) (1 shared connections)

## Source Files

- `src/hal0/omni_router/dispatch.py`
- `src/hal0/omni_router/tools.py`
- `tests/omni_router/test_tools.py`

## Audit Trail

- EXTRACTED: 143 (76%)
- INFERRED: 45 (24%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*