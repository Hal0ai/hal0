# _proxy_ws

> 11 nodes · cohesion 0.25

## Key Concepts

- **_proxy_ws()** (11 connections) — `src/hal0/api/agents/chat_proxy.py`
- **events_ws()** (6 connections) — `src/hal0/api/agents/chat_proxy.py`
- **.close()** (6 connections) — `src/hal0/api/agents/chat_proxy.py`
- **submit_ws()** (6 connections) — `src/hal0/api/agents/chat_proxy.py`
- **_pump()** (3 connections) — `src/hal0/api/agents/chat_proxy.py`
- **WebSocket** (3 connections)
- **Final flush + cancel any pending timer.** (1 connections) — `src/hal0/api/agents/chat_proxy.py`
- **Read text frames from ``source_recv`` and write to ``sink_send``.      Returns c** (1 connections) — `src/hal0/api/agents/chat_proxy.py`
- **Bridge a browser WS to a hermes WS at ``upstream_path``.      ``coalesce_progres** (1 connections) — `src/hal0/api/agents/chat_proxy.py`
- **Server→browser mirror of hermes's JSON-RPC event bus.      Subscribes to upstrea** (1 connections) — `src/hal0/api/agents/chat_proxy.py`
- **Browser→hermes JSON-RPC submit channel.      Bidi WS on top of hermes ``/api/ws`** (1 connections) — `src/hal0/api/agents/chat_proxy.py`

## Relationships

- [chat_proxy.py](chat_proxy.py.md) (7 shared connections)
- [ProgressCoalescer](ProgressCoalescer.md) (3 shared connections)
- [_auth.py](_auth.py.md) (2 shared connections)

## Source Files

- `src/hal0/api/agents/chat_proxy.py`

## Audit Trail

- EXTRACTED: 38 (95%)
- INFERRED: 2 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*