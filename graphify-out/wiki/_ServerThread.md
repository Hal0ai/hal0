# _ServerThread

> 8 nodes · cohesion 0.25

## Key Concepts

- **_ServerThread** (8 connections) — `tests/api/test_chat_proxy.py`
- **.__init__()** (3 connections) — `tests/api/test_chat_proxy.py`
- **FastAPI** (3 connections)
- **._register()** (2 connections) — `tests/api/test_chat_proxy.py`
- **.__init__()** (2 connections) — `tests/api/test_chat_proxy.py`
- **.stop()** (2 connections) — `tests/api/test_chat_proxy.py`
- **Runs uvicorn in a background thread for the fake hermes.** (1 connections) — `tests/api/test_chat_proxy.py`
- **.run()** (1 connections) — `tests/api/test_chat_proxy.py`

## Relationships

- [test_chat_proxy.py](test_chat_proxy.py.md) (6 shared connections)
- [ProgressCoalescer](ProgressCoalescer.md) (1 shared connections)
- [QueryStringScrubber](QueryStringScrubber.md) (1 shared connections)

## Source Files

- `tests/api/test_chat_proxy.py`

## Audit Trail

- EXTRACTED: 20 (91%)
- INFERRED: 2 (9%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*