# compute_config_drift

> 47 nodes

## Key Concepts

- **compute_config_drift()** (13 connections) — `src/hal0/slots/drift.py`
- **Protocol** (11 connections)
- **test_config_drift_aliases.py** (11 connections) — `tests/slots/test_config_drift_aliases.py`
- **reconcile_trio_slots()** (8 connections) — `src/hal0/slots/npu/trio.py`
- **DriftHost** (7 connections) — `src/hal0/slots/drift.py`
- **NpuTrioHost** (7 connections) — `src/hal0/slots/npu/trio.py`
- **drift.py** (6 connections) — `src/hal0/slots/drift.py`
- **_argv_values()** (6 connections) — `src/hal0/slots/drift.py`
- **test_no_false_drift_for_registry_id_model_and_alias()** (6 connections) — `tests/slots/test_config_drift_aliases.py`
- **test_real_model_drift_still_flagged_after_resolution()** (6 connections) — `tests/slots/test_config_drift_aliases.py`
- **test_no_false_drift_between_alias_spellings()** (5 connections) — `tests/slots/test_config_drift_aliases.py`
- **Path** (5 connections)
- **FakeContainerProvider** (5 connections)
- **test_real_drift_still_detected_across_spellings()** (5 connections) — `tests/slots/test_config_drift_aliases.py`
- **test_ctx_size_change_surfaces_as_drift()** (5 connections) — `tests/slots/test_config_drift_aliases.py`
- **_resolve_drift_flags()** (4 connections) — `src/hal0/slots/drift.py`
- **trio.py** (4 connections) — `src/hal0/slots/npu/trio.py`
- **._maybe_load_config()** (3 connections) — `src/hal0/slots/drift.py`
- **Any** (3 connections)
- **._resolve_model_info()** (3 connections) — `src/hal0/slots/drift.py`
- **Any** (3 connections)
- **.iter_configs()** (3 connections) — `src/hal0/slots/npu/trio.py`
- **.create()** (3 connections) — `src/hal0/slots/npu/trio.py`
- **test_argv_values_matches_long_spelling_for_short_key()** (3 connections) — `tests/slots/test_config_drift_aliases.py`
- **test_argv_values_matches_short_spelling_for_long_key()** (3 connections) — `tests/slots/test_config_drift_aliases.py`
- *... and 22 more nodes in this community*

## Relationships

- [SlotState](SlotState.md) (5 shared connections)
- [SlotManager](SlotManager.md) (5 shared connections)
- [write_slot_toml](write_slot_toml.md) (2 shared connections)
- [FakeContainerProvider](FakeContainerProvider.md) (2 shared connections)
- [manager.py](manager.py.md) (1 shared connections)
- [AttemptHandle](AttemptHandle.md) (1 shared connections)
- [_Runner](_Runner.md) (1 shared connections)
- [SlotManagerLike](SlotManagerLike.md) (1 shared connections)
- [migrate_slot_id_keying](migrate_slot_id_keying.md) (1 shared connections)
- [SlotConfigError](SlotConfigError.md) (1 shared connections)
- [RoutingHost](RoutingHost.md) (1 shared connections)
- [WatchdogHost](WatchdogHost.md) (1 shared connections)

## Source Files

- `src/hal0/slots/drift.py`
- `src/hal0/slots/npu/trio.py`
- `tests/slots/test_config_drift_aliases.py`

## Audit Trail

- EXTRACTED: 149 (90%)
- INFERRED: 17 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*