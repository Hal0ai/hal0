# ProgressCoalescer

> 18 nodes · cohesion 0.14

## Key Concepts

- **ProgressCoalescer** (16 connections) — `src/hal0/api/agents/chat_proxy.py`
- **.handle()** (5 connections) — `src/hal0/api/agents/chat_proxy.py`
- **._flush_now()** (4 connections) — `src/hal0/api/agents/chat_proxy.py`
- **._delayed_flush()** (3 connections) — `src/hal0/api/agents/chat_proxy.py`
- **._parse_event_type()** (3 connections) — `src/hal0/api/agents/chat_proxy.py`
- **._schedule_flush()** (3 connections) — `src/hal0/api/agents/chat_proxy.py`
- **test_coalescer_buffers_progress_then_flushes()** (3 connections) — `tests/api/test_chat_proxy.py`
- **test_coalescer_keeps_per_tool_id_separate()** (3 connections) — `tests/api/test_chat_proxy.py`
- **test_coalescer_non_progress_event_flushes_buffer_first()** (3 connections) — `tests/api/test_chat_proxy.py`
- **test_coalescer_passes_through_unparseable_frames()** (3 connections) — `tests/api/test_chat_proxy.py`
- **.__init__()** (1 connections) — `src/hal0/api/agents/chat_proxy.py`
- **Server-side coalescer for ``tool.progress`` event spam.      Buffers ``tool.prog** (1 connections) — `src/hal0/api/agents/chat_proxy.py`
- **Inspect the JSON-RPC envelope and return (event_type, tool_id).          Returns** (1 connections) — `src/hal0/api/agents/chat_proxy.py`
- **Route one upstream frame.          ``tool.progress`` → buffer + schedule flush.** (1 connections) — `src/hal0/api/agents/chat_proxy.py`
- **N rapid tool.progress frames flush at most once after 100ms.** (1 connections) — `tests/api/test_chat_proxy.py`
- **A non-progress event drains the buffer + then forwards itself.      Ordering inv** (1 connections) — `tests/api/test_chat_proxy.py`
- **Two distinct tool_ids both survive the coalescer.** (1 connections) — `tests/api/test_chat_proxy.py`
- **Garbage in → garbage straight through (don't drop frames).** (1 connections) — `tests/api/test_chat_proxy.py`

## Relationships

- [test_chat_proxy.py](test_chat_proxy.py.md) (5 shared connections)
- [_proxy_ws](_proxy_ws.md) (3 shared connections)
- [chat_proxy.py](chat_proxy.py.md) (1 shared connections)
- [_ServerThread](_ServerThread.md) (1 shared connections)

## Source Files

- `src/hal0/api/agents/chat_proxy.py`
- `tests/api/test_chat_proxy.py`

## Audit Trail

- EXTRACTED: 44 (81%)
- INFERRED: 10 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*