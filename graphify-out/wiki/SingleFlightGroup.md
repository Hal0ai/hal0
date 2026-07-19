# SingleFlightGroup

> 29 nodes

## Key Concepts

- **SingleFlightGroup** (20 connections) — `src/hal0/dispatcher/single_flight.py`
- **.__init__()** (9 connections) — `src/hal0/dispatcher/router.py`
- **test_single_flight.py** (6 connections) — `tests/dispatcher/test_single_flight.py`
- **single_flight.py** (4 connections) — `src/hal0/dispatcher/single_flight.py`
- **.do()** (4 connections) — `src/hal0/dispatcher/single_flight.py`
- **test_100_concurrent_identical_calls_share_one_invocation()** (3 connections) — `tests/dispatcher/test_single_flight.py`
- **test_exception_is_shared_with_all_waiters()** (3 connections) — `tests/dispatcher/test_single_flight.py`
- **test_second_call_after_completion_runs_fresh()** (3 connections) — `tests/dispatcher/test_single_flight.py`
- **.in_flight_keys()** (2 connections) — `src/hal0/dispatcher/single_flight.py`
- **test_distinct_keys_run_independently()** (2 connections) — `tests/dispatcher/test_single_flight.py`
- **test_kwargs_and_args_pass_through()** (2 connections) — `tests/dispatcher/test_single_flight.py`
- **ModelRegistry** (1 connections)
- **CachedModelsFn** (1 connections)
- **IsOnlineFn** (1 connections)
- **FetchModelsFn** (1 connections)
- **SlotManager** (1 connections)
- **.__init__()** (1 connections) — `src/hal0/dispatcher/single_flight.py`
- **Any** (1 connections)
- **T** (1 connections)
- **Request coalescing / single-flight for cold-cache prefetch.  When multiple concu** (1 connections) — `src/hal0/dispatcher/single_flight.py`
- **Coalesces concurrent calls with the same key into a single in-flight request.** (1 connections) — `src/hal0/dispatcher/single_flight.py`
- **Execute fn(*args, **kwargs) for key, or wait for the in-flight call.          Ar** (1 connections) — `src/hal0/dispatcher/single_flight.py`
- **Return a snapshot of currently in-flight keys (for diagnostics).** (1 connections) — `src/hal0/dispatcher/single_flight.py`
- **# NOTE: Fast path — an existing in-flight Future for this key means** (1 connections) — `src/hal0/dispatcher/single_flight.py`
- **# NOTE: catch BaseException so CancelledError and KeyboardInterrupt** (1 connections) — `src/hal0/dispatcher/single_flight.py`
- *... and 4 more nodes in this community*

## Relationships

- [Dispatcher](Dispatcher.md) (6 shared connections)
- [UpstreamCall](UpstreamCall.md) (2 shared connections)
- [_RecordingSlotManager](_RecordingSlotManager.md) (2 shared connections)
- [UpstreamRegistry](UpstreamRegistry.md) (1 shared connections)
- [SlotLoading](SlotLoading.md) (1 shared connections)

## Source Files

- `src/hal0/dispatcher/router.py`
- `src/hal0/dispatcher/single_flight.py`
- `tests/dispatcher/test_single_flight.py`

## Audit Trail

- EXTRACTED: 57 (75%)
- INFERRED: 19 (25%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*