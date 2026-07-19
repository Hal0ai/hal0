# EnergyVAD

> 14 nodes

## Key Concepts

- **EnergyVAD** (9 connections) — `src/hal0/realtime/vad.py`
- **vad.py** (4 connections) — `src/hal0/realtime/vad.py`
- **_window_rms()** (4 connections) — `src/hal0/realtime/vad.py`
- **.feed()** (4 connections) — `src/hal0/realtime/vad.py`
- **VadDecision** (3 connections) — `src/hal0/realtime/vad.py`
- **.reset()** (2 connections) — `src/hal0/realtime/vad.py`
- **.__init__()** (1 connections) — `src/hal0/realtime/vad.py`
- **.in_speech()** (1 connections) — `src/hal0/realtime/vad.py`
- **In-process energy-threshold server VAD (user decision 1, spec §4c-1).  **Why ene** (1 connections) — `src/hal0/realtime/vad.py`
- **A single VAD state transition emitted from :meth:`EnergyVAD.feed`.** (1 connections) — `src/hal0/realtime/vad.py`
- **Normalized RMS of one window — numpy fast path, stdlib reference else.** (1 connections) — `src/hal0/realtime/vad.py`
- **Streaming energy-threshold voice-activity detector.      Feed appended pcm16 mon** (1 connections) — `src/hal0/realtime/vad.py`
- **Clear all state (called on commit / cancel / new turn).** (1 connections) — `src/hal0/realtime/vad.py`
- **Process appended pcm; return the transitions it produced (may be empty).** (1 connections) — `src/hal0/realtime/vad.py`

## Relationships

- [RealtimeSession](RealtimeSession.md) (2 shared connections)
- [audio.py](audio.py.md) (1 shared connections)
- [test_vad.py](test_vad.py.md) (1 shared connections)

## Source Files

- `src/hal0/realtime/vad.py`

## Audit Trail

- EXTRACTED: 32 (94%)
- INFERRED: 2 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*