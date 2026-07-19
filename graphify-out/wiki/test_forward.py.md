# test_forward.py

> 15 nodes

## Key Concepts

- **test_forward.py** (9 connections) — `tests/dispatcher/test_forward.py`
- **_make_dispatcher()** (9 connections) — `tests/dispatcher/test_forward.py`
- **_call()** (7 connections) — `tests/dispatcher/test_forward.py`
- **MockTransport** (6 connections)
- **test_forward_streaming_connect_error_raises_typed_envelope()** (6 connections) — `tests/dispatcher/test_forward.py`
- **test_forward_passes_upstream_status_through()** (5 connections) — `tests/dispatcher/test_forward.py`
- **test_forward_network_error_raises_typed_envelope()** (5 connections) — `tests/dispatcher/test_forward.py`
- **test_forward_streaming_pipes_chunks()** (5 connections) — `tests/dispatcher/test_forward.py`
- **test_forward_non_streaming_returns_upstream_body()** (4 connections) — `tests/dispatcher/test_forward.py`
- **test_aclose_is_idempotent()** (2 connections) — `tests/dispatcher/test_forward.py`
- **Unit tests for ``Dispatcher.forward``.  Uses ``httpx.MockTransport`` to stub ups** (1 connections) — `tests/dispatcher/test_forward.py`
- **Build a Dispatcher whose internal httpx client is backed by ``transport``.** (1 connections) — `tests/dispatcher/test_forward.py`
- **Upstream 4xx/5xx bodies are forwarded as-is, not wrapped in dispatch errors.** (1 connections) — `tests/dispatcher/test_forward.py`
- **Streaming responses pipe upstream chunks through unchanged.** (1 connections) — `tests/dispatcher/test_forward.py`
- **Open-stream failures surface as UpstreamUnavailable (not stream-time).** (1 connections) — `tests/dispatcher/test_forward.py`

## Relationships

- [Dispatcher](Dispatcher.md) (2 shared connections)
- [_RecordingSlotManager](_RecordingSlotManager.md) (2 shared connections)
- [UpstreamCall](UpstreamCall.md) (1 shared connections)

## Source Files

- `tests/dispatcher/test_forward.py`

## Audit Trail

- EXTRACTED: 61 (97%)
- INFERRED: 2 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*