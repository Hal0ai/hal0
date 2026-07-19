# OmniRouter

> 31 nodes · cohesion 0.09

## Key Concepts

- **OmniRouter** (14 connections) — `src/hal0/omni_router/router.py`
- **SlotManagerLike** (13 connections) — `src/hal0/omni_router/filter.py`
- **.run_loop()** (9 connections) — `src/hal0/omni_router/router.py`
- **.active_tools()** (5 connections) — `src/hal0/omni_router/router.py`
- **._build_context()** (5 connections) — `src/hal0/omni_router/router.py`
- **.dispatch()** (5 connections) — `src/hal0/omni_router/router.py`
- **.__init__()** (4 connections) — `src/hal0/omni_router/dispatch.py`
- **filter.py** (4 connections) — `src/hal0/omni_router/filter.py`
- **Any** (4 connections)
- **._chat_completion()** (4 connections) — `src/hal0/omni_router/router.py`
- **._strip_omni()** (4 connections) — `src/hal0/omni_router/router.py`
- **Any** (4 connections)
- **.iter_configs()** (3 connections) — `src/hal0/omni_router/filter.py`
- **.loaded_slot()** (3 connections) — `src/hal0/omni_router/filter.py`
- **.resolve_for_request()** (3 connections) — `src/hal0/omni_router/filter.py`
- **.__init__()** (3 connections) — `src/hal0/omni_router/router.py`
- **router.py** (2 connections) — `src/hal0/omni_router/router.py`
- **ChatCompletionFn** (1 connections)
- **AsyncClient** (1 connections)
- **Protocol** (1 connections)
- **Dynamic per-request tool filtering — plan §7.3.  Given the active chat slot and** (1 connections) — `src/hal0/omni_router/filter.py`
- **The narrow SlotManager surface filter.py + dispatch.py need.      Stated as a Pr** (1 connections) — `src/hal0/omni_router/filter.py`
- **AsyncClient** (1 connections)
- **OmniRouter — the public surface.  Wires :mod:`hal0.omni_router.filter`, :mod:`ha** (1 connections) — `src/hal0/omni_router/router.py`
- **Drive the OpenAI tool-calling loop against ``/v1/chat/completions``.          Ar** (1 connections) — `src/hal0/omni_router/router.py`
- *... and 6 more nodes in this community*

## Relationships

- [active_tools_for](active_tools_for.md) (6 shared connections)
- [FakeSlotManager](FakeSlotManager.md) (4 shared connections)
- [tools_by_name](tools_by_name.md) (4 shared connections)
- [make_slot](make_slot.md) (2 shared connections)
- [test_router_loop.py](test_router_loop.py.md) (2 shared connections)
- [lifespan](lifespan.md) (1 shared connections)
- [chat.py](chat.py.md) (1 shared connections)
- [run_tool_loop](run_tool_loop.md) (1 shared connections)

## Source Files

- `src/hal0/omni_router/dispatch.py`
- `src/hal0/omni_router/filter.py`
- `src/hal0/omni_router/router.py`

## Audit Trail

- EXTRACTED: 92 (89%)
- INFERRED: 11 (11%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*