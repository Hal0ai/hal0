# run_tool_loop

> 27 nodes

## Key Concepts

- **run_tool_loop()** (16 connections) — `src/hal0/toolloop/engine.py`
- **engine.py** (13 connections) — `src/hal0/toolloop/engine.py`
- **Any** (11 connections)
- **parse_text_tool_calls()** (6 connections) — `src/hal0/toolloop/engine.py`
- **openai_tool_schema()** (5 connections) — `src/hal0/toolloop/engine.py`
- **assistant_thinking()** (5 connections) — `src/hal0/toolloop/engine.py`
- **_toolcall_from_obj()** (5 connections) — `src/hal0/toolloop/engine.py`
- **build_tool_message()** (4 connections) — `src/hal0/toolloop/engine.py`
- **extract_tool_calls()** (4 connections) — `src/hal0/toolloop/engine.py`
- **assistant_message()** (4 connections) — `src/hal0/toolloop/engine.py`
- **assistant_text()** (4 connections) — `src/hal0/toolloop/engine.py`
- **split_thinking()** (4 connections) — `src/hal0/toolloop/engine.py`
- **_coerce_args()** (4 connections) — `src/hal0/toolloop/engine.py`
- **_notify()** (4 connections) — `src/hal0/toolloop/engine.py`
- **OnEvent** (2 connections)
- **LlmFn** (1 connections)
- **DispatchFn** (1 connections)
- **Provider-agnostic OpenAI tool-calling loop — the ONE core.  Both ``hal0.api.rout** (1 connections) — `src/hal0/toolloop/engine.py`
- **Render one tool as the OpenAI ``tools=[...]`` wire shape.** (1 connections) — `src/hal0/toolloop/engine.py`
- **The ``role: tool`` message folding a dispatch result back to the LLM.** (1 connections) — `src/hal0/toolloop/engine.py`
- **Pull + normalise ``tool_calls`` (arguments -> dict) from a completion.      Retu** (1 connections) — `src/hal0/toolloop/engine.py`
- **The raw assistant turn message (for replay into the next round).** (1 connections) — `src/hal0/toolloop/engine.py`
- **Pull explicit reasoning fields off the assistant message.** (1 connections) — `src/hal0/toolloop/engine.py`
- **Split ``<think>`` blocks out of assistant content -> (thinking, visible).** (1 connections) — `src/hal0/toolloop/engine.py`
- **Pull ``(name, arguments)`` from a parsed dict in the common shapes.** (1 connections) — `src/hal0/toolloop/engine.py`
- *... and 2 more nodes in this community*

## Relationships

- [_run_nonstreaming_turn](_run_nonstreaming_turn.md) (3 shared connections)
- [chat.py](chat.py.md) (2 shared connections)
- [.to_openai_tool](to_openai_tool.md) (1 shared connections)
- [OmniRouter](OmniRouter.md) (1 shared connections)

## Source Files

- `src/hal0/toolloop/engine.py`

## Audit Trail

- EXTRACTED: 96 (93%)
- INFERRED: 7 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*