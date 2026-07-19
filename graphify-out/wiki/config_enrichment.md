# config_enrichment

> 13 nodes

## Key Concepts

- **config_enrichment()** (15 connections) — `src/hal0/slot_view/__init__.py`
- **_llm_cfg()** (12 connections) — `tests/slot_view/test_aggregator.py`
- **TestConfigEnrichment** (11 connections) — `tests/slot_view/test_aggregator.py`
- **.test_every_slot_gets_an_entry()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **.test_no_runtime_state_keys()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **.test_declared_backend_from_device()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **.test_coresident_group_for_npu_trio()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **.test_config_fields_surfaced()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **.test_ctx_max_from_context_size()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **.test_ctx_max_from_ctx_size_alias()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **.test_absent_config_fields_surface_as_defaults()** (3 connections) — `tests/slot_view/test_aggregator.py`
- **.test_no_coresident_group_without_enabled_anchor()** (2 connections) — `tests/slot_view/test_aggregator.py`
- **Per-slot TOML-derived fields for slot snapshots. Pure.      Lifts the edit-drawe** (1 connections) — `src/hal0/slot_view/__init__.py`

## Relationships

- [Any](Any.md) (3 shared connections)
- [_slot](_slot.md) (3 shared connections)
- [slots.py](slots.py.md) (1 shared connections)
- [write_slot_toml](write_slot_toml.md) (1 shared connections)
- [MapContainerProvider](MapContainerProvider.md) (1 shared connections)
- [container_enrichment](container_enrichment.md) (1 shared connections)
- [SlotState](SlotState.md) (1 shared connections)

## Source Files

- `src/hal0/slot_view/__init__.py`
- `tests/slot_view/test_aggregator.py`

## Audit Trail

- EXTRACTED: 44 (68%)
- INFERRED: 21 (32%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*