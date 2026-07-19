# test_throughput_history.py

> 21 nodes

## Key Concepts

- **test_throughput_history.py** (14 connections) — `tests/api/test_throughput_history.py`
- **FastAPI** (9 connections)
- **TestClient** (9 connections)
- **_seed_store()** (5 connections) — `tests/api/test_throughput_history.py`
- **test_seeded_events_produce_correct_buckets()** (5 connections) — `tests/api/test_throughput_history.py`
- **test_events_outside_window_excluded()** (5 connections) — `tests/api/test_throughput_history.py`
- **test_empty_store_returns_empty_samples()** (4 connections) — `tests/api/test_throughput_history.py`
- **test_empty_deques_return_empty_samples()** (4 connections) — `tests/api/test_throughput_history.py`
- **_build_app()** (3 connections) — `tests/api/test_throughput_history.py`
- **app()** (3 connections) — `tests/api/test_throughput_history.py`
- **client()** (3 connections) — `tests/api/test_throughput_history.py`
- **test_buckets_clamped_to_min()** (2 connections) — `tests/api/test_throughput_history.py`
- **test_buckets_clamped_to_max()** (2 connections) — `tests/api/test_throughput_history.py`
- **test_window_clamped_to_min()** (2 connections) — `tests/api/test_throughput_history.py`
- **test_window_clamped_to_max()** (2 connections) — `tests/api/test_throughput_history.py`
- **Tests for ``GET /api/stats/throughput/history``.  Mounts the router on a bare Fa** (1 connections) — `tests/api/test_throughput_history.py`
- **Populate app.state.tps_events from a {slot_name: [(mono_ts, tokens)]} dict.** (1 connections) — `tests/api/test_throughput_history.py`
- **Missing / empty tps_events => samples:[], per_slot:{}.** (1 connections) — `tests/api/test_throughput_history.py`
- **tps_events present but all deques empty => empty response.** (1 connections) — `tests/api/test_throughput_history.py`
- **Two slots with known events land in the expected bins.      Window: 100s, bucket** (1 connections) — `tests/api/test_throughput_history.py`
- **Events older than window_s must not appear in output.** (1 connections) — `tests/api/test_throughput_history.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/api/test_throughput_history.py`

## Audit Trail

- EXTRACTED: 78 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*