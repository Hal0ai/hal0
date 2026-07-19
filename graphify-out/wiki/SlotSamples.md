# SlotSamples

> 10 nodes

## Key Concepts

- **SlotSamples** (11 connections) — `src/hal0/slots/ttft_samples.py`
- **._recent()** (4 connections) — `src/hal0/slots/ttft_samples.py`
- **.avg_ttft()** (3 connections) — `src/hal0/slots/ttft_samples.py`
- **.first_chunk()** (2 connections) — `src/hal0/slots/ttft_samples.py`
- **.current_ttft()** (2 connections) — `src/hal0/slots/ttft_samples.py`
- **.sample_count()** (2 connections) — `src/hal0/slots/ttft_samples.py`
- **.request_started()** (1 connections) — `src/hal0/slots/ttft_samples.py`
- **.request_cancelled()** (1 connections) — `src/hal0/slots/ttft_samples.py`
- **Rolling TTFT samples + inflight-request map for one slot.      Samples are ``(mo** (1 connections) — `src/hal0/slots/ttft_samples.py`
- **Record TTFT for ``req_id``. Returns the TTFT in seconds, or         ``None`` if** (1 connections) — `src/hal0/slots/ttft_samples.py`

## Relationships

- [avg_ttft_across](avg_ttft_across.md) (2 shared connections)
- [ttft_samples.py](ttft_samples.py.md) (1 shared connections)
- [samples_from_events](samples_from_events.md) (1 shared connections)

## Source Files

- `src/hal0/slots/ttft_samples.py`

## Audit Trail

- EXTRACTED: 28 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*