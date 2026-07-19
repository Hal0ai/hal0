# serialize_slot

> 12 nodes

## Key Concepts

- **serialize_slot()** (16 connections) — `src/hal0/slot_view/__init__.py`
- **TestSerializeSlot** (11 connections) — `tests/slot_view/test_aggregator.py`
- **.test_basic_shape()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **.test_backend_and_provider_lifted_from_metadata()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **.test_config_drift_lifted_from_metadata()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **.test_explicit_backend_wins_over_metadata()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **.test_models_orders_active_model_first()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **.test_no_cache_omits_models_key()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **.test_serving_with_empty_models_downgraded_to_idle()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **.test_serving_with_resident_models_stays_serving()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **.test_self_managed_provider_serving_empty_not_downgraded()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **Serialise a real Slot snapshot into the API shape.      Adds ``kind="local"`` so** (1 connections) — `src/hal0/slot_view/__init__.py`

## Relationships

- [_slot](_slot.md) (10 shared connections)
- [Any](Any.md) (3 shared connections)
- [slots.py](slots.py.md) (1 shared connections)
- [provider_requires_model](provider_requires_model.md) (1 shared connections)
- [test_dispatchable_ready_set_single_source.py](test_dispatchable_ready_set_single_source.py.md) (1 shared connections)
- [SlotState](SlotState.md) (1 shared connections)

## Source Files

- `src/hal0/slot_view/__init__.py`
- `tests/slot_view/test_aggregator.py`

## Audit Trail

- EXTRACTED: 33 (60%)
- INFERRED: 22 (40%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*