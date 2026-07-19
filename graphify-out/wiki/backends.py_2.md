# backends.py

> 25 nodes

## Key Concepts

- **backends.py** (12 connections) — `src/hal0/realtime/backends.py`
- **ChatChunk** (10 connections) — `src/hal0/realtime/backends.py`
- **_default_chat_steward()** (8 connections) — `src/hal0/realtime/backends.py`
- **_default_chat_plain()** (7 connections) — `src/hal0/realtime/backends.py`
- **RealtimeBackends** (6 connections) — `src/hal0/realtime/backends.py`
- **get_backends()** (6 connections) — `src/hal0/realtime/backends.py`
- **_self_base_url()** (5 connections) — `src/hal0/realtime/backends.py`
- **_auth_headers()** (5 connections) — `src/hal0/realtime/backends.py`
- **_default_synthesize()** (5 connections) — `src/hal0/realtime/backends.py`
- **_steward_frame_to_chunks()** (5 connections) — `src/hal0/realtime/backends.py`
- **._llm_chunks()** (5 connections) — `src/hal0/realtime/session.py`
- **._bounded_after_approval()** (5 connections) — `src/hal0/realtime/session.py`
- **_iter_sse_lines()** (4 connections) — `src/hal0/realtime/backends.py`
- **Any** (4 connections)
- **_default_transcribe()** (3 connections) — `src/hal0/realtime/backends.py`
- **STT / TTS / LLM-leg seams for the Realtime engine.  The engine never imports ``v** (1 connections) — `src/hal0/realtime/backends.py`
- **One normalized event from an LLM leg the session consumes.      ``kind``:** (1 connections) — `src/hal0/realtime/backends.py`
- **Split an SSE text chunk into ``data:`` JSON payloads (drop comments).** (1 connections) — `src/hal0/realtime/backends.py`
- **Stream ``/v1/chat/completions``; text deltas + accumulated tool_calls.** (1 connections) — `src/hal0/realtime/backends.py`
- **Consume the brain SSE (``/api/brain/chat``); ZERO edits to brain/chat.py.** (1 connections) — `src/hal0/realtime/backends.py`
- **Map one brain SSE frame to zero-or-more :class:`ChatChunk` (spec §2b).** (1 connections) — `src/hal0/realtime/backends.py`
- **The four seams the session drives. Defaults hit loopback HTTP; tests     overrid** (1 connections) — `src/hal0/realtime/backends.py`
- **Return the app's :class:`RealtimeBackends`, building the loopback default     on** (1 connections) — `src/hal0/realtime/backends.py`
- **Select + drive the LLM leg; bound the steward approval wait for voice.** (1 connections) — `src/hal0/realtime/session.py`
- **Pass chunks through; once an approval notice is seen, cap the wait for         t** (1 connections) — `src/hal0/realtime/session.py`

## Relationships

- [RealtimeSession](RealtimeSession.md) (7 shared connections)
- [conftest.py](conftest.py.md) (3 shared connections)
- [audio.py](audio.py.md) (2 shared connections)
- [realtime_ws](realtime_ws.md) (1 shared connections)
- [SlotState](SlotState.md) (1 shared connections)

## Source Files

- `src/hal0/realtime/backends.py`
- `src/hal0/realtime/session.py`

## Audit Trail

- EXTRACTED: 92 (92%)
- INFERRED: 8 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*