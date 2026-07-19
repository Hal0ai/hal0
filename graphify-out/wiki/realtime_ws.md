# realtime_ws

> 10 nodes

## Key Concepts

- **realtime_ws()** (9 connections) — `src/hal0/api/routes/realtime.py`
- **_resolve_config()** (6 connections) — `src/hal0/api/routes/realtime.py`
- **realtime.py** (5 connections) — `src/hal0/api/routes/realtime.py`
- **WebSocket** (4 connections)
- **_forwarded_auth()** (4 connections) — `src/hal0/api/routes/realtime.py`
- **_query_model()** (3 connections) — `src/hal0/api/routes/realtime.py`
- **``WS /v1/realtime`` — OpenAI Realtime WebSocket surface (HP-realtime inc-1).  Th** (1 connections) — `src/hal0/api/routes/realtime.py`
- **Return the ``[realtime]`` config, defaulting if the app has none loaded.** (1 connections) — `src/hal0/api/routes/realtime.py`
- **Credential to forward on loopback STT/TTS/chat calls (so they pass the     enfor** (1 connections) — `src/hal0/api/routes/realtime.py`
- **OpenAI Realtime WS endpoint (query ``?model=``).** (1 connections) — `src/hal0/api/routes/realtime.py`

## Relationships

- [schema.py](schema.py.md) (1 shared connections)
- [SlotState](SlotState.md) (1 shared connections)
- [.emit](emit.md) (1 shared connections)
- [backends.py](backends.py.md) (1 shared connections)
- [RealtimeSession](RealtimeSession.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/realtime.py`

## Audit Trail

- EXTRACTED: 30 (86%)
- INFERRED: 5 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*