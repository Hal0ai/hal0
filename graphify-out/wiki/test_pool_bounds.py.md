# test_pool_bounds.py

> 15 nodes

## Key Concepts

- **test_pool_bounds.py** (8 connections) — `tests/dispatcher/test_pool_bounds.py`
- **test_pool_timeout_raises_upstream_unavailable()** (5 connections) — `tests/dispatcher/test_pool_bounds.py`
- **test_pool_timeout_streaming_raises_upstream_unavailable()** (5 connections) — `tests/dispatcher/test_pool_bounds.py`
- **_call()** (4 connections) — `tests/dispatcher/test_pool_bounds.py`
- **test_lazy_http_client_has_bounded_pool()** (3 connections) — `tests/dispatcher/test_pool_bounds.py`
- **test_lazy_http_client_read_timeout()** (3 connections) — `tests/dispatcher/test_pool_bounds.py`
- **test_dispatcher_pool_constants_are_bounded()** (2 connections) — `tests/dispatcher/test_pool_bounds.py`
- **test_direct_read_timeout_reduced()** (2 connections) — `tests/dispatcher/test_pool_bounds.py`
- **Regression tests for dispatcher HTTP client pool bounds (#415).  Without the fix** (1 connections) — `tests/dispatcher/test_pool_bounds.py`
- **The module constants must reflect the bounded values from the fix.** (1 connections) — `tests/dispatcher/test_pool_bounds.py`
- **Non-streaming read timeout must be <= 60 s (was 300 s before the fix).** (1 connections) — `tests/dispatcher/test_pool_bounds.py`
- **The lazily-constructed client must carry the connection limits.** (1 connections) — `tests/dispatcher/test_pool_bounds.py`
- **The lazily-constructed client's read timeout must use the constant.** (1 connections) — `tests/dispatcher/test_pool_bounds.py`
- **A PoolTimeout from a saturated pool surfaces as UpstreamUnavailable.      We inj** (1 connections) — `tests/dispatcher/test_pool_bounds.py`
- **A PoolTimeout on stream-open also surfaces as UpstreamUnavailable.** (1 connections) — `tests/dispatcher/test_pool_bounds.py`

## Relationships

- [Dispatcher](Dispatcher.md) (4 shared connections)
- [_RecordingSlotManager](_RecordingSlotManager.md) (2 shared connections)
- [UpstreamCall](UpstreamCall.md) (1 shared connections)

## Source Files

- `tests/dispatcher/test_pool_bounds.py`

## Audit Trail

- EXTRACTED: 33 (85%)
- INFERRED: 6 (15%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*