# samples_from_events

> 6 nodes

## Key Concepts

- **samples_from_events()** (7 connections) — `src/hal0/slots/ttft_samples.py`
- **test_samples_from_events_wraps_the_live_deque_by_reference()** (3 connections) — `tests/slots/test_ttft_samples.py`
- **test_samples_from_events_defaults_to_module_window()** (2 connections) — `tests/slots/test_ttft_samples.py`
- **deque** (1 connections)
- **Adapt a raw ``app.state.ttft_events[slot]`` deque to a     ``SlotSamples`` view** (1 connections) — `src/hal0/slots/ttft_samples.py`
- **samples_from_events is a lazy, read-only *view* over the caller's     deque (no** (1 connections) — `tests/slots/test_ttft_samples.py`

## Relationships

- [test_ttft_samples.py](test_ttft_samples.py.md) (2 shared connections)
- [metrics_collect.py](metrics_collect.py.md) (1 shared connections)
- [ttft_samples.py](ttft_samples.py.md) (1 shared connections)
- [SlotSamples](SlotSamples.md) (1 shared connections)

## Source Files

- `src/hal0/slots/ttft_samples.py`
- `tests/slots/test_ttft_samples.py`

## Audit Trail

- EXTRACTED: 10 (67%)
- INFERRED: 5 (33%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*