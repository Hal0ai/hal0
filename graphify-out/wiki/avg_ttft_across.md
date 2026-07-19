# avg_ttft_across

> 6 nodes

## Key Concepts

- **avg_ttft_across()** (7 connections) — `src/hal0/slots/ttft_samples.py`
- **test_avg_ttft_across_equally_weights_slots_regardless_of_sample_count()** (3 connections) — `tests/slots/test_ttft_samples.py`
- **test_avg_ttft_across_returns_none_when_no_slot_has_data()** (2 connections) — `tests/slots/test_ttft_samples.py`
- **test_avg_ttft_across_skips_slots_with_no_in_window_data()** (2 connections) — `tests/slots/test_ttft_samples.py`
- **Mean of per-slot avg TTFT across slots that have data.      Equally weights slot** (1 connections) — `src/hal0/slots/ttft_samples.py`
- **One slot's churn shouldn't drown another's single sample: the fleet     average** (1 connections) — `tests/slots/test_ttft_samples.py`

## Relationships

- [test_ttft_samples.py](test_ttft_samples.py.md) (3 shared connections)
- [SlotSamples](SlotSamples.md) (2 shared connections)
- [ttft_samples.py](ttft_samples.py.md) (1 shared connections)

## Source Files

- `src/hal0/slots/ttft_samples.py`
- `tests/slots/test_ttft_samples.py`

## Audit Trail

- EXTRACTED: 10 (62%)
- INFERRED: 6 (38%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*