# EnergyVAD

> 18 nodes · cohesion 0.12

## Key Concepts

- **EnergyVAD** (9 connections) — `src/hal0/realtime/vad.py`
- **RealtimeBackends** (6 connections) — `src/hal0/realtime/backends.py`
- **.__init__()** (4 connections) — `src/hal0/realtime/session.py`
- **vad.py** (4 connections) — `src/hal0/realtime/vad.py`
- **.feed()** (4 connections) — `src/hal0/realtime/vad.py`
- **_window_rms()** (4 connections) — `src/hal0/realtime/vad.py`
- **._build_vad()** (3 connections) — `src/hal0/realtime/session.py`
- **VadDecision** (3 connections) — `src/hal0/realtime/vad.py`
- **.reset()** (2 connections) — `src/hal0/realtime/vad.py`
- **The four seams the session drives. Defaults hit loopback HTTP; tests     overrid** (1 connections) — `src/hal0/realtime/backends.py`
- **.in_speech()** (1 connections) — `src/hal0/realtime/vad.py`
- **.__init__()** (1 connections) — `src/hal0/realtime/vad.py`
- **In-process energy-threshold server VAD (user decision 1, spec §4c-1).  **Why ene** (1 connections) — `src/hal0/realtime/vad.py`
- **A single VAD state transition emitted from :meth:`EnergyVAD.feed`.** (1 connections) — `src/hal0/realtime/vad.py`
- **Normalized RMS of one window — numpy fast path, stdlib reference else.** (1 connections) — `src/hal0/realtime/vad.py`
- **Streaming energy-threshold voice-activity detector.      Feed appended pcm16 mon** (1 connections) — `src/hal0/realtime/vad.py`
- **Clear all state (called on commit / cancel / new turn).** (1 connections) — `src/hal0/realtime/vad.py`
- **Process appended pcm; return the transitions it produced (may be empty).** (1 connections) — `src/hal0/realtime/vad.py`

## Relationships

- [RealtimeSession](RealtimeSession.md) (5 shared connections)
- [backends.py](backends.py.md) (2 shared connections)
- [conftest.py](conftest.py.md) (1 shared connections)
- [test_vad.py](test_vad.py.md) (1 shared connections)
- [audio.py](audio.py.md) (1 shared connections)

## Source Files

- `src/hal0/realtime/backends.py`
- `src/hal0/realtime/session.py`
- `src/hal0/realtime/vad.py`

## Audit Trail

- EXTRACTED: 45 (94%)
- INFERRED: 3 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*