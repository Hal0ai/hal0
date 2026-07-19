# SlotSampler

> 21 nodes

## Key Concepts

- **SlotSampler** (18 connections) — `src/hal0/metrics/sampler.py`
- **_FakeWriter** (8 connections) — `tests/metrics/test_sampler.py`
- **test_sampler.py** (7 connections) — `tests/metrics/test_sampler.py`
- **_FakeSlotManager** (7 connections) — `tests/metrics/test_sampler.py`
- **_FakeSlot** (6 connections) — `tests/metrics/test_sampler.py`
- **.test_writes_per_slot_rows_and_fleet_row()** (6 connections) — `tests/metrics/test_sampler.py`
- **.test_state_transition_emits_slot_event()** (6 connections) — `tests/metrics/test_sampler.py`
- **_FakeState** (5 connections) — `tests/metrics/test_sampler.py`
- **TestSlotSamplerTick** (5 connections) — `tests/metrics/test_sampler.py`
- **.test_writes_fleet_row_even_with_no_slots()** (4 connections) — `tests/metrics/test_sampler.py`
- **._loop()** (3 connections) — `src/hal0/metrics/sampler.py`
- **.start()** (2 connections) — `src/hal0/metrics/sampler.py`
- **.enqueue()** (2 connections) — `tests/metrics/test_sampler.py`
- **.__init__()** (2 connections) — `tests/metrics/test_sampler.py`
- **.list()** (2 connections) — `tests/metrics/test_sampler.py`
- **writer()** (2 connections) — `tests/metrics/test_sampler.py`
- **.stop()** (1 connections) — `src/hal0/metrics/sampler.py`
- **One background task; one tick = one ``slot_sample``/``slot_event`` write set.** (1 connections) — `src/hal0/metrics/sampler.py`
- **.__init__()** (1 connections) — `tests/metrics/test_sampler.py`
- **Any** (1 connections)
- **SlotSampler.tick() -- fleet row + per-slot rows + missing-sensor grace.** (1 connections) — `tests/metrics/test_sampler.py`

## Relationships

- [.tick](tick.md) (4 shared connections)
- [MetricsService](MetricsService.md) (2 shared connections)
- [MetricsWriter](MetricsWriter.md) (1 shared connections)
- [test_memory_provider_commands.py](test_memory_provider_commands.py.md) (1 shared connections)

## Source Files

- `src/hal0/metrics/sampler.py`
- `tests/metrics/test_sampler.py`

## Audit Trail

- EXTRACTED: 70 (78%)
- INFERRED: 20 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*