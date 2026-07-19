# SlotInterface

> 49 nodes · cohesion 0.07

## Key Concepts

- **SlotInterface** (17 connections) — `src/hal0/slots/interface.py`
- **_make_env()** (16 connections) — `tests/slots/test_interface.py`
- **test_interface.py** (15 connections) — `tests/slots/test_interface.py`
- **DesiredSlotState** (14 connections) — `src/hal0/slots/interface.py`
- **.apply()** (8 connections) — `src/hal0/slots/interface.py`
- **SlotSnapshot** (8 connections) — `src/hal0/slots/interface.py`
- **.inspect()** (6 connections) — `src/hal0/slots/interface.py`
- **._name()** (5 connections) — `src/hal0/slots/interface.py`
- **interface.py** (4 connections) — `src/hal0/slots/interface.py`
- **._would_change()** (4 connections) — `src/hal0/slots/interface.py`
- **test_inspect_assembles_one_snapshot()** (4 connections) — `tests/slots/test_interface.py`
- **.delete()** (3 connections) — `src/hal0/slots/interface.py`
- **._port_claim()** (3 connections) — `src/hal0/slots/interface.py`
- **.subscribe()** (3 connections) — `src/hal0/slots/interface.py`
- **.as_dict()** (3 connections) — `src/hal0/slots/interface.py`
- **.interface()** (3 connections) — `src/hal0/slots/manager.py`
- **_no_spawn_context_refresh()** (3 connections) — `tests/slots/test_interface.py`
- **test_apply_is_idempotent()** (3 connections) — `tests/slots/test_interface.py`
- **test_apply_loads_toward_target()** (3 connections) — `tests/slots/test_interface.py`
- **test_apply_materializes_config_idempotently()** (3 connections) — `tests/slots/test_interface.py`
- **test_apply_swaps_model_on_live_slot()** (3 connections) — `tests/slots/test_interface.py`
- **test_apply_unloads_toward_offline()** (3 connections) — `tests/slots/test_interface.py`
- **test_delete_unloads_live_slot_first()** (3 connections) — `tests/slots/test_interface.py`
- **test_inspect_surfaces_last_failure_on_error()** (3 connections) — `tests/slots/test_interface.py`
- **test_interface_property_is_cached()** (3 connections) — `tests/slots/test_interface.py`
- *... and 24 more nodes in this community*

## Relationships

- [SlotConfigError](SlotConfigError.md) (7 shared connections)
- [SlotState](SlotState.md) (3 shared connections)
- [SlotManager](SlotManager.md) (2 shared connections)
- [PortAuthority](PortAuthority.md) (1 shared connections)
- [SlotIdentityStore](SlotIdentityStore.md) (1 shared connections)

## Source Files

- `src/hal0/slots/interface.py`
- `src/hal0/slots/manager.py`
- `tests/slots/test_interface.py`

## Audit Trail

- EXTRACTED: 140 (80%)
- INFERRED: 34 (20%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*