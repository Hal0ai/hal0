# _instrument_streaming_throughput

> 13 nodes · cohesion 0.15

## Key Concepts

- **_instrument_streaming_throughput()** (7 connections) — `src/hal0/api/routes/v1.py`
- **_record_nonstreaming_throughput()** (7 connections) — `src/hal0/api/routes/v1.py`
- **_slot_events()** (5 connections) — `src/hal0/api/routes/v1.py`
- **_slot_ttft_events()** (4 connections) — `src/hal0/api/routes/v1.py`
- **_update_slot_kv_occupancy()** (4 connections) — `src/hal0/api/routes/v1.py`
- **_update_slot_throughput()** (4 connections) — `src/hal0/api/routes/v1.py`
- **StreamingResponse** (1 connections)
- **Wrap a streaming response body iterator with a token counter     plus a one-shot** (1 connections) — `src/hal0/api/routes/v1.py`
- **Pull ``usage.completion_tokens`` + a recent timestamp out of a JSON     response** (1 connections) — `src/hal0/api/routes/v1.py`
- **Return the per-slot tps_events deque for ``slot_name`` (or None).      ``app_sta** (1 connections) — `src/hal0/api/routes/v1.py`
- **Per-slot ttft_events deque (mirrors `_slot_events`).** (1 connections) — `src/hal0/api/routes/v1.py`
- **Record a FLM decoding-speed sample (tok/s) for the slot.      Stored on ``app_st** (1 connections) — `src/hal0/api/routes/v1.py`
- **Record a FLM KV-column occupancy sample (0-100%) for the slot.      Stored on ``** (1 connections) — `src/hal0/api/routes/v1.py`

## Relationships

- [v1.py](v1.py.md) (14 shared connections)

## Source Files

- `src/hal0/api/routes/v1.py`

## Audit Trail

- EXTRACTED: 38 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*