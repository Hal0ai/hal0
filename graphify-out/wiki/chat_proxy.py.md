# chat_proxy.py

> 31 nodes · cohesion 0.11

## Key Concepts

- **chat_proxy.py** (18 connections) — `src/hal0/api/agents/chat_proxy.py`
- **_hermes_rpc()** (9 connections) — `src/hal0/api/agents/chat_proxy.py`
- **require_browser_auth()** (7 connections) — `src/hal0/api/agents/_auth.py`
- **session_create()** (6 connections) — `src/hal0/api/agents/chat_proxy.py`
- **session_history()** (6 connections) — `src/hal0/api/agents/chat_proxy.py`
- **session_resume()** (6 connections) — `src/hal0/api/agents/chat_proxy.py`
- **_hermes_base_url()** (5 connections) — `src/hal0/api/agents/chat_proxy.py`
- **_hermes_ws_url()** (5 connections) — `src/hal0/api/agents/chat_proxy.py`
- **_load_embed_token()** (5 connections) — `src/hal0/api/agents/chat_proxy.py`
- **Any** (5 connections)
- **session_handshake()** (5 connections) — `src/hal0/api/agents/chat_proxy.py`
- **_outbound_headers()** (4 connections) — `src/hal0/api/agents/chat_proxy.py`
- **_runtime_json_path()** (4 connections) — `src/hal0/api/agents/chat_proxy.py`
- **_hermes_host()** (3 connections) — `src/hal0/api/agents/chat_proxy.py`
- **_hermes_port()** (3 connections) — `src/hal0/api/agents/chat_proxy.py`
- **Request** (3 connections)
- **Request** (1 connections)
- **Verify a fresh session cookie on REST endpoints.      Raises 403 if absent / sig** (1 connections) — `src/hal0/api/agents/_auth.py`
- **Path** (1 connections)
- **Response** (1 connections)
- **Chat WS proxy + session REST shim for the hermes agent runtime.  Bridges the bro** (1 connections) — `src/hal0/api/agents/chat_proxy.py`
- **Return the on-disk runtime.json path.      Honours ``HAL0_HERMES_RUNTIME_JSON``** (1 connections) — `src/hal0/api/agents/chat_proxy.py`
- **Read the hermes embed token from runtime.json.      Returns ``None`` if the file** (1 connections) — `src/hal0/api/agents/chat_proxy.py`
- **HTTP base URL for hermes REST endpoints.** (1 connections) — `src/hal0/api/agents/chat_proxy.py`
- **WebSocket URL builder for hermes endpoints.      ``path`` includes the leading s** (1 connections) — `src/hal0/api/agents/chat_proxy.py`
- *... and 6 more nodes in this community*

## Relationships

- [_proxy_ws](_proxy_ws.md) (7 shared connections)
- [_auth.py](_auth.py.md) (3 shared connections)
- [ProgressCoalescer](ProgressCoalescer.md) (1 shared connections)

## Source Files

- `src/hal0/api/agents/_auth.py`
- `src/hal0/api/agents/chat_proxy.py`

## Audit Trail

- EXTRACTED: 102 (94%)
- INFERRED: 7 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*