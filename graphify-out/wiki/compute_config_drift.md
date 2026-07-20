# compute_config_drift

> 37 nodes · cohesion 0.08

## Key Concepts

- **compute_config_drift()** (13 connections) — `src/hal0/slots/drift.py`
- **test_config_drift_aliases.py** (11 connections) — `tests/slots/test_config_drift_aliases.py`
- **DriftHost** (7 connections) — `src/hal0/slots/drift.py`
- **drift.py** (6 connections) — `src/hal0/slots/drift.py`
- **_argv_values()** (6 connections) — `src/hal0/slots/drift.py`
- **test_no_false_drift_for_registry_id_model_and_alias()** (6 connections) — `tests/slots/test_config_drift_aliases.py`
- **test_real_model_drift_still_flagged_after_resolution()** (6 connections) — `tests/slots/test_config_drift_aliases.py`
- **FakeContainerProvider** (5 connections)
- **Path** (5 connections)
- **test_ctx_size_change_surfaces_as_drift()** (5 connections) — `tests/slots/test_config_drift_aliases.py`
- **test_no_false_drift_between_alias_spellings()** (5 connections) — `tests/slots/test_config_drift_aliases.py`
- **test_real_drift_still_detected_across_spellings()** (5 connections) — `tests/slots/test_config_drift_aliases.py`
- **_resolve_drift_flags()** (4 connections) — `src/hal0/slots/drift.py`
- **._maybe_load_config()** (3 connections) — `src/hal0/slots/drift.py`
- **._resolve_model_info()** (3 connections) — `src/hal0/slots/drift.py`
- **Any** (3 connections)
- **test_argv_values_last_value_wins_across_spellings()** (3 connections) — `tests/slots/test_config_drift_aliases.py`
- **test_argv_values_matches_long_spelling_for_short_key()** (3 connections) — `tests/slots/test_config_drift_aliases.py`
- **test_argv_values_matches_short_spelling_for_long_key()** (3 connections) — `tests/slots/test_config_drift_aliases.py`
- **_config_drift_values_equal()** (2 connections) — `src/hal0/slots/drift.py`
- **._is_active()** (2 connections) — `src/hal0/slots/drift.py`
- **MonkeyPatch** (2 connections)
- **Protocol** (1 connections)
- **Config-drift comparator (P3-slots §1c).  Compares a slot's *live* container argv** (1 connections) — `src/hal0/slots/drift.py`
- **Compare live container argv to the command a restart would render.      Returns** (1 connections) — `src/hal0/slots/drift.py`
- *... and 12 more nodes in this community*

## Relationships

- [SlotManager](SlotManager.md) (5 shared connections)
- [SlotConfigError](SlotConfigError.md) (3 shared connections)
- [discover.py](discover.py.md) (1 shared connections)
- [conftest.py](conftest.py.md) (1 shared connections)
- [FakeContainerProvider](FakeContainerProvider.md) (1 shared connections)

## Source Files

- `src/hal0/slots/drift.py`
- `tests/slots/test_config_drift_aliases.py`

## Audit Trail

- EXTRACTED: 108 (88%)
- INFERRED: 15 (12%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*