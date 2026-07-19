# FakeSlotManager

> 58 nodes · cohesion 0.10

## Key Concepts

- **FakeSlotManager** (57 connections) — `tests/omni_router/conftest.py`
- **DispatchContext** (40 connections) — `src/hal0/omni_router/dispatch.py`
- **.dispatch_tool()** (35 connections) — `tests/fixtures/hermes/contracts/plugin_context.py`
- **make_http_client()** (31 connections) — `tests/omni_router/conftest.py`
- **test_dispatch.py** (30 connections) — `tests/omni_router/test_dispatch.py`
- **_ctx()** (18 connections) — `tests/omni_router/test_dispatch.py`
- **conftest.py** (10 connections) — `tests/omni_router/conftest.py`
- **test_transport_failure_returns_error_envelope()** (7 connections) — `tests/omni_router/test_dispatch.py`
- **test_route_to_chat_depth_limit_enforced()** (7 connections) — `tests/omni_router/test_route_to_chat.py`
- **test_route_to_chat_happy_path()** (7 connections) — `tests/omni_router/test_route_to_chat.py`
- **test_route_to_chat_increments_depth_during_callback()** (7 connections) — `tests/omni_router/test_route_to_chat.py`
- **test_route_to_chat_no_callback_returns_error()** (7 connections) — `tests/omni_router/test_route_to_chat.py`
- **test_route_to_chat_non_standard_response_passed_through()** (7 connections) — `tests/omni_router/test_route_to_chat.py`
- **test_analyze_image_happy_path()** (6 connections) — `tests/omni_router/test_dispatch.py`
- **test_edit_image_happy_path()** (6 connections) — `tests/omni_router/test_dispatch.py`
- **test_embed_text_happy_path()** (6 connections) — `tests/omni_router/test_dispatch.py`
- **test_generate_image_happy_path()** (6 connections) — `tests/omni_router/test_dispatch.py`
- **test_generate_image_passes_optional_size_and_n()** (6 connections) — `tests/omni_router/test_dispatch.py`
- **test_rerank_documents_happy_path()** (6 connections) — `tests/omni_router/test_dispatch.py`
- **test_text_to_speech_binary_response_returns_metadata()** (6 connections) — `tests/omni_router/test_dispatch.py`
- **test_text_to_speech_happy_path()** (6 connections) — `tests/omni_router/test_dispatch.py`
- **test_transcribe_audio_happy_path()** (6 connections) — `tests/omni_router/test_dispatch.py`
- **test_upstream_5xx_returns_error_envelope()** (6 connections) — `tests/omni_router/test_dispatch.py`
- **test_route_to_chat_context_appended()** (6 connections) — `tests/omni_router/test_route_to_chat.py`
- **test_route_to_chat_missing_prompt()** (6 connections) — `tests/omni_router/test_route_to_chat.py`
- *... and 33 more nodes in this community*

## Relationships

- [make_slot](make_slot.md) (52 shared connections)
- [active_tools_for](active_tools_for.md) (19 shared connections)
- [tools_by_name](tools_by_name.md) (13 shared connections)
- [test_router_loop.py](test_router_loop.py.md) (7 shared connections)
- [OmniRouter](OmniRouter.md) (4 shared connections)
- [RoutingHost](RoutingHost.md) (4 shared connections)
- [Any](Any.md) (2 shared connections)
- [PluginContext](PluginContext.md) (1 shared connections)

## Source Files

- `src/hal0/omni_router/dispatch.py`
- `tests/fixtures/hermes/contracts/plugin_context.py`
- `tests/omni_router/conftest.py`
- `tests/omni_router/test_dispatch.py`
- `tests/omni_router/test_route_to_chat.py`

## Audit Trail

- EXTRACTED: 328 (78%)
- INFERRED: 94 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*