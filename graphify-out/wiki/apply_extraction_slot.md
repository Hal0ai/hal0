# apply_extraction_slot

> 17 nodes · cohesion 0.18

## Key Concepts

- **apply_extraction_slot()** (8 connections) — `src/hal0/memory/extraction_env.py`
- **test_extraction_env.py** (8 connections) — `tests/memory/test_extraction_env.py`
- **render_drop_in()** (6 connections) — `src/hal0/memory/extraction_env.py`
- **extraction_env.py** (3 connections) — `src/hal0/memory/extraction_env.py`
- **Path** (3 connections)
- **test_apply_no_restart_skips_systemctl()** (3 connections) — `tests/memory/test_extraction_env.py`
- **test_apply_threads_timeout_into_drop_in_and_status()** (3 connections) — `tests/memory/test_extraction_env.py`
- **test_apply_writes_drop_in_and_reports_status()** (3 connections) — `tests/memory/test_extraction_env.py`
- **test_render_drop_in_includes_llm_timeout()** (2 connections) — `tests/memory/test_extraction_env.py`
- **test_render_drop_in_pins_hal0_virtual()** (2 connections) — `tests/memory/test_extraction_env.py`
- **test_render_drop_in_tracks_the_slot_name()** (2 connections) — `tests/memory/test_extraction_env.py`
- **Any** (1 connections)
- **Propagate the memory graph extraction slot to hindsight-api (ADR-0023).  Hindsig** (1 connections) — `src/hal0/memory/extraction_env.py`
- **Return the drop-in contents pinning extraction to ``hal0/<slot>`` + timeout.** (1 connections) — `src/hal0/memory/extraction_env.py`
- **Write the drop-in for ``slot`` and (best-effort) restart hindsight-api.      Ret** (1 connections) — `src/hal0/memory/extraction_env.py`
- **Unit tests for the hindsight-api extraction-slot drop-in writer (ADR-0023).  ``a** (1 connections) — `tests/memory/test_extraction_env.py`
- **test_drop_in_path_is_a_systemd_override()** (1 connections) — `tests/memory/test_extraction_env.py`

## Relationships

- [memory.py](memory.py.md) (1 shared connections)

## Source Files

- `src/hal0/memory/extraction_env.py`
- `tests/memory/test_extraction_env.py`

## Audit Trail

- EXTRACTED: 36 (73%)
- INFERRED: 13 (27%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*