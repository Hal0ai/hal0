# DispatchContext

> 25 nodes

## Key Concepts

- **DispatchContext** (40 connections) — `src/hal0/omni_router/dispatch.py`
- **tools_by_name()** (16 connections) — `src/hal0/omni_router/tools.py`
- **dispatch.py** (15 connections) — `src/hal0/omni_router/dispatch.py`
- **Any** (13 connections)
- **_route_or_error()** (12 connections) — `src/hal0/omni_router/dispatch.py`
- **_missing()** (11 connections) — `src/hal0/omni_router/dispatch.py`
- **_post_json()** (11 connections) — `src/hal0/omni_router/dispatch.py`
- **_model_id_of()** (10 connections) — `src/hal0/omni_router/dispatch.py`
- **handle_generate_image()** (8 connections) — `src/hal0/omni_router/dispatch.py`
- **handle_edit_image()** (8 connections) — `src/hal0/omni_router/dispatch.py`
- **handle_text_to_speech()** (8 connections) — `src/hal0/omni_router/dispatch.py`
- **handle_transcribe_audio()** (8 connections) — `src/hal0/omni_router/dispatch.py`
- **handle_analyze_image()** (8 connections) — `src/hal0/omni_router/dispatch.py`
- **handle_embed_text()** (8 connections) — `src/hal0/omni_router/dispatch.py`
- **handle_rerank_documents()** (8 connections) — `src/hal0/omni_router/dispatch.py`
- **handle_route_to_chat()** (8 connections) — `src/hal0/omni_router/dispatch.py`
- **dispatch_tool()** (5 connections) — `src/hal0/omni_router/dispatch.py`
- **OmniRouter tool dispatch handlers — plan §7.  Each of the eight tools has a coro** (1 connections) — `src/hal0/omni_router/dispatch.py`
- **Carrier for the per-loop dependencies a handler needs.      Bundled into one obj** (1 connections) — `src/hal0/omni_router/dispatch.py`
- **Return an error message if any required key is missing/empty,     else ``None``.** (1 connections) — `src/hal0/omni_router/dispatch.py`
- **Resolve the target loaded slot for ``tool``.      Returns ``(loaded_slot, None)`** (1 connections) — `src/hal0/omni_router/dispatch.py`
- **POST JSON to a hal0 /v1 endpoint; return parsed body or an     ``{"error": ...}`** (1 connections) — `src/hal0/omni_router/dispatch.py`
- **Special-case dispatcher — see :mod:`hal0.omni_router.route_to_chat`.      Valida** (1 connections) — `src/hal0/omni_router/dispatch.py`
- **Look up a tool's handler and run it; return the tool_result body.      Returns `** (1 connections) — `src/hal0/omni_router/dispatch.py`
- **Return a name → ToolDefinition map. Built fresh per call.      Kept as a functio** (1 connections) — `src/hal0/omni_router/tools.py`

## Relationships

- [make_slot](make_slot.md) (23 shared connections)
- [OmniRouter](OmniRouter.md) (6 shared connections)
- [test_tools.py](test_tools.py.md) (6 shared connections)
- [SlotManagerLike](SlotManagerLike.md) (2 shared connections)
- [route_to_chat.py](route_to_chat.py.md) (2 shared connections)

## Source Files

- `src/hal0/omni_router/dispatch.py`
- `src/hal0/omni_router/tools.py`

## Audit Trail

- EXTRACTED: 151 (74%)
- INFERRED: 54 (26%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*