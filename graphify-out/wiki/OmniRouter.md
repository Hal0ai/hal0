# OmniRouter

> 24 nodes

## Key Concepts

- **OmniRouter** (14 connections) — `src/hal0/omni_router/router.py`
- **ToolDefinition** (12 connections) — `src/hal0/omni_router/tools.py`
- **.run_loop()** (9 connections) — `src/hal0/omni_router/router.py`
- **.active_tools()** (5 connections) — `src/hal0/omni_router/router.py`
- **.dispatch()** (5 connections) — `src/hal0/omni_router/router.py`
- **._build_context()** (5 connections) — `src/hal0/omni_router/router.py`
- **Any** (4 connections)
- **._chat_completion()** (4 connections) — `src/hal0/omni_router/router.py`
- **._strip_omni()** (4 connections) — `src/hal0/omni_router/router.py`
- **tools.py** (4 connections) — `src/hal0/omni_router/tools.py`
- **_load_tool_definitions()** (3 connections) — `src/hal0/omni_router/tools.py`
- **router.py** (2 connections) — `src/hal0/omni_router/router.py`
- **test_definitions_round_trip_through_isinstance()** (2 connections) — `tests/omni_router/test_tools.py`
- **OmniRouter — the public surface.  Wires :mod:`hal0.omni_router.filter`, :mod:`ha** (1 connections) — `src/hal0/omni_router/router.py`
- **Client-side OpenAI tool-calling loop.      Constructed once per hal0-api process** (1 connections) — `src/hal0/omni_router/router.py`
- **Return the active tool list for a chat slot. Plan §7.3.** (1 connections) — `src/hal0/omni_router/router.py`
- **Dispatch a single tool_call. Returns the tool_result body.** (1 connections) — `src/hal0/omni_router/router.py`
- **Drive the OpenAI tool-calling loop against ``/v1/chat/completions``.          Ar** (1 connections) — `src/hal0/omni_router/router.py`
- **Build a DispatchContext wired with a chat_completion callback.          The call** (1 connections) — `src/hal0/omni_router/router.py`
- **POST ``/v1/chat/completions`` and return the parsed body.          Errors are su** (1 connections) — `src/hal0/omni_router/router.py`
- **Drop hal0-specific knobs that must not reach the upstream.** (1 connections) — `src/hal0/omni_router/router.py`
- **Typed tool definitions loaded from ``tool_definitions.json``.  Plan §7.2 + §7.5.** (1 connections) — `src/hal0/omni_router/tools.py`
- **A single tool entry — immutable after load.      The dataclass is frozen so a ca** (1 connections) — `src/hal0/omni_router/tools.py`
- **Load + freeze the eight tool definitions at import time.      Raises:         Va** (1 connections) — `src/hal0/omni_router/tools.py`

## Relationships

- [DispatchContext](DispatchContext.md) (6 shared connections)
- [SlotManagerLike](SlotManagerLike.md) (3 shared connections)
- [test_router_loop.py](test_router_loop.py.md) (2 shared connections)
- [FakeSlotManager](FakeSlotManager.md) (2 shared connections)
- [lifespan](lifespan.md) (1 shared connections)
- [chat.py](chat.py.md) (1 shared connections)
- [run_tool_loop](run_tool_loop.md) (1 shared connections)
- [.to_openai_tool](to_openai_tool.md) (1 shared connections)
- [test_tools.py](test_tools.py.md) (1 shared connections)

## Source Files

- `src/hal0/omni_router/router.py`
- `src/hal0/omni_router/tools.py`
- `tests/omni_router/test_tools.py`

## Audit Trail

- EXTRACTED: 72 (86%)
- INFERRED: 12 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*