# realtime_ws

> 27 nodes · cohesion 0.11

## Key Concepts

- **realtime_ws()** (9 connections) — `src/hal0/api/routes/realtime.py`
- **.emit()** (7 connections) — `src/hal0/events/__init__.py`
- **_resolve_config()** (6 connections) — `src/hal0/api/routes/realtime.py`
- **._enqueue()** (6 connections) — `src/hal0/events/__init__.py`
- **make_event()** (6 connections) — `src/hal0/events/__init__.py`
- **Any** (6 connections)
- **realtime.py** (5 connections) — `src/hal0/api/routes/realtime.py`
- **_forwarded_auth()** (4 connections) — `src/hal0/api/routes/realtime.py`
- **WebSocket** (4 connections)
- **__init__.py** (4 connections) — `src/hal0/events/__init__.py`
- **.subscribe()** (4 connections) — `src/hal0/events/__init__.py`
- **_query_model()** (3 connections) — `src/hal0/api/routes/realtime.py`
- **.backfill()** (3 connections) — `src/hal0/events/__init__.py`
- **_now_iso()** (3 connections) — `src/hal0/events/__init__.py`
- **.__init__()** (2 connections) — `src/hal0/events/__init__.py`
- **Queue** (2 connections)
- **``WS /v1/realtime`` — OpenAI Realtime WebSocket surface (HP-realtime inc-1).  Th** (1 connections) — `src/hal0/api/routes/realtime.py`
- **Return the ``[realtime]`` config, defaulting if the app has none loaded.** (1 connections) — `src/hal0/api/routes/realtime.py`
- **Credential to forward on loopback STT/TTS/chat calls (so they pass the     enfor** (1 connections) — `src/hal0/api/routes/realtime.py`
- **OpenAI Realtime WS endpoint (query ``?model=``).** (1 connections) — `src/hal0/api/routes/realtime.py`
- **In-process event bus + ring buffer for the dashboard footer.  The footer status** (1 connections) — `src/hal0/events/__init__.py`
- **Append an event to the ring and fan it out to every subscriber.          Never b** (1 connections) — `src/hal0/events/__init__.py`
- **Push to a subscriber queue, dropping oldest on overflow.          A slow consume** (1 connections) — `src/hal0/events/__init__.py`
- **Yield an asyncio.Queue receiving every event emitted after entry.          Use a** (1 connections) — `src/hal0/events/__init__.py`
- **Return matching ring entries with id > since, ordered ascending.          Filter** (1 connections) — `src/hal0/events/__init__.py`
- *... and 2 more nodes in this community*

## Relationships

- [EventBus](EventBus.md) (6 shared connections)
- [backends.py](backends.py.md) (1 shared connections)
- [RealtimeSession](RealtimeSession.md) (1 shared connections)
- [schema.py](schema.py.md) (1 shared connections)
- [SlotManager](SlotManager.md) (1 shared connections)
- [orchestrate_models](orchestrate_models.md) (1 shared connections)

## Source Files

- `src/hal0/api/routes/realtime.py`
- `src/hal0/events/__init__.py`

## Audit Trail

- EXTRACTED: 78 (92%)
- INFERRED: 7 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*