# test_chat_proxy.py

> 30 nodes

## Key Concepts

- **test_chat_proxy.py** (21 connections) — `tests/api/test_chat_proxy.py`
- **FakeHermes** (14 connections) — `tests/api/test_chat_proxy.py`
- **test_events_ws_mirrors_frames()** (9 connections) — `tests/api/test_chat_proxy.py`
- **_write_runtime_json()** (8 connections) — `tests/api/test_chat_proxy.py`
- **test_events_ws_injects_authorization_header()** (8 connections) — `tests/api/test_chat_proxy.py`
- **test_session_create_proxies_to_hermes()** (8 connections) — `tests/api/test_chat_proxy.py`
- **test_session_history_query_param_forwarded()** (8 connections) — `tests/api/test_chat_proxy.py`
- **fake_hermes()** (7 connections) — `tests/api/test_chat_proxy.py`
- **MonkeyPatch** (7 connections)
- **TestClient** (7 connections)
- **_authorise_client()** (7 connections) — `tests/api/test_chat_proxy.py`
- **client()** (6 connections) — `tests/api/test_chat_proxy.py`
- **Path** (6 connections)
- **test_session_create_rejects_without_cookie()** (4 connections) — `tests/api/test_chat_proxy.py`
- **.push_event()** (3 connections) — `tests/api/test_chat_proxy.py`
- **_free_port()** (3 connections) — `tests/api/test_chat_proxy.py`
- **.wait_for_events_connection()** (1 connections) — `tests/api/test_chat_proxy.py`
- **Functional tests for the chat-proxy WS + REST surface.  A small in-process fake** (1 connections) — `tests/api/test_chat_proxy.py`
- **In-process stand-in for the hermes dashboard runtime.      Implements just enoug** (1 connections) — `tests/api/test_chat_proxy.py`
- **Send one event frame from upstream → proxy → browser.** (1 connections) — `tests/api/test_chat_proxy.py`
- **Grab an ephemeral port the OS isn't already using.** (1 connections) — `tests/api/test_chat_proxy.py`
- **Spin up FakeHermes on a free port + point the proxy at it.** (1 connections) — `tests/api/test_chat_proxy.py`
- **Fresh app + isolated secret + tight origin allowlist.** (1 connections) — `tests/api/test_chat_proxy.py`
- **Mint a session cookie via the handshake endpoint + attach it.** (1 connections) — `tests/api/test_chat_proxy.py`
- **Drop a runtime.json with ``token`` + point the proxy at it.** (1 connections) — `tests/api/test_chat_proxy.py`
- *... and 5 more nodes in this community*

## Relationships

- [_ServerThread](_ServerThread.md) (6 shared connections)
- [ProgressCoalescer](ProgressCoalescer.md) (5 shared connections)
- [QueryStringScrubber](QueryStringScrubber.md) (3 shared connections)
- [socket](socket.md) (1 shared connections)
- [create_app](create_app.md) (1 shared connections)

## Source Files

- `tests/api/test_chat_proxy.py`

## Audit Trail

- EXTRACTED: 137 (98%)
- INFERRED: 3 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*