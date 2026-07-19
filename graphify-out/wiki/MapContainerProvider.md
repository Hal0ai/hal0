# MapContainerProvider

> 19 nodes · cohesion 0.18

## Key Concepts

- **MapContainerProvider** (12 connections) — `tests/slot_view/test_aggregator.py`
- **Any** (10 connections)
- **TestShadowSlotStatusInheritance** (10 connections) — `tests/slot_view/test_aggregator.py`
- **_npu_anchor_cfg()** (7 connections) — `tests/slot_view/test_aggregator.py`
- **_shadow_cfg()** (7 connections) — `tests/slot_view/test_aggregator.py`
- **.test_disabled_anchor_does_not_serve()** (5 connections) — `tests/slot_view/test_aggregator.py`
- **.test_disabled_shadow_not_inherited()** (5 connections) — `tests/slot_view/test_aggregator.py`
- **.test_shadow_inherits_running_anchor()** (5 connections) — `tests/slot_view/test_aggregator.py`
- **.test_stopped_anchor_propagates()** (5 connections) — `tests/slot_view/test_aggregator.py`
- **.test_shadow_without_anchor_stays_stopped()** (4 connections) — `tests/slot_view/test_aggregator.py`
- **.__init__()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **.no_real_systemctl()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **.health()** (2 connections) — `tests/slot_view/test_aggregator.py`
- **.iter_configs()** (2 connections) — `tests/slot_view/test_aggregator.py`
- **.__init__()** (2 connections) — `tests/slot_view/test_aggregator.py`
- **.is_active()** (1 connections) — `tests/slot_view/test_aggregator.py`
- **Per-slot ``is_active`` — the trio anchor runs, shadows have no unit.** (1 connections) — `tests/slot_view/test_aggregator.py`
- **#733: embed/stt shadow slots have no unit/container of their own —     the npu a** (1 connections) — `tests/slot_view/test_aggregator.py`
- **container_enrichment's stopped-vs-crashed escalation shells out         to a rea** (1 connections) — `tests/slot_view/test_aggregator.py`

## Relationships

- [container_enrichment](container_enrichment.md) (11 shared connections)
- [_slot](_slot.md) (9 shared connections)
- [SlotState](SlotState.md) (2 shared connections)
- [slot](slot.md) (1 shared connections)
- [config_enrichment](config_enrichment.md) (1 shared connections)

## Source Files

- `tests/slot_view/test_aggregator.py`

## Audit Trail

- EXTRACTED: 79 (92%)
- INFERRED: 7 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*