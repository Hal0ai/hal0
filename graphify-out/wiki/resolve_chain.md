# resolve_chain

> 29 nodes · cohesion 0.13

## Key Concepts

- **resolve_chain()** (25 connections) — `src/hal0/normalize/resolver.py`
- **test_resolver.py** (20 connections) — `tests/normalize/test_resolver.py`
- **_slots()** (12 connections) — `tests/normalize/test_resolver.py`
- **test_agent_virtual_name_resolves_to_agent_slot()** (4 connections) — `tests/normalize/test_resolver.py`
- **test_brain_falls_back_to_agent_when_no_brain_slot()** (4 connections) — `tests/normalize/test_resolver.py`
- **test_removed_aliases_are_unknown()** (4 connections) — `tests/normalize/test_resolver.py`
- **test_utility_falls_back_to_agent_anchor()** (4 connections) — `tests/normalize/test_resolver.py`
- **test_agent_prefers_igpu_when_loaded()** (3 connections) — `tests/normalize/test_resolver.py`
- **test_brain_prefers_brain_slot_when_present()** (3 connections) — `tests/normalize/test_resolver.py`
- **test_full_miss_falls_back_to_configured_primary_unloaded()** (3 connections) — `tests/normalize/test_resolver.py`
- **test_generalized_custom_slot_falls_back_to_agent()** (3 connections) — `tests/normalize/test_resolver.py`
- **test_generalized_custom_slot_resolves()** (3 connections) — `tests/normalize/test_resolver.py`
- **test_npu_falls_to_utility_before_agent()** (3 connections) — `tests/normalize/test_resolver.py`
- **test_npu_name_matches_any_npu_device_slot()** (3 connections) — `tests/normalize/test_resolver.py`
- **test_npu_picks_npu_first_never_commandeers_agent()** (3 connections) — `tests/normalize/test_resolver.py`
- **test_unknown_virtual_name_returns_none()** (3 connections) — `tests/normalize/test_resolver.py`
- **test_utility_chain_does_not_pick_non_utility_named_slot()** (3 connections) — `tests/normalize/test_resolver.py`
- **test_utility_prefers_utility_when_loaded()** (3 connections) — `tests/normalize/test_resolver.py`
- **test_empty_slots_degrades_to_blank_resolution()** (2 connections) — `tests/normalize/test_resolver.py`
- **Resolve a virtual name to a live slot's physical model id.      Returns ``None``** (1 connections) — `src/hal0/normalize/resolver.py`
- **A slot with name "coder-mini" does NOT match hal0/utility even     when the agen** (1 connections) — `tests/normalize/test_resolver.py`
- **ADR-0023 §2: any enabled llm slot X is addressable as hal0/X with chain     (X,** (1 connections) — `tests/normalize/test_resolver.py`
- **A generalized hal0/<slot> falls back to the agent anchor when the slot's     own** (1 connections) — `tests/normalize/test_resolver.py`
- **hal0/agent must resolve to the slot named 'agent' (the GPU MoE anchor).** (1 connections) — `tests/normalize/test_resolver.py`
- **The hal0/primary and hal0/flm aliases were removed — they are no longer     know** (1 connections) — `tests/normalize/test_resolver.py`
- *... and 4 more nodes in this community*

## Relationships

- [SlotView](SlotView.md) (9 shared connections)
- [test_slot_aliases.py](test_slot_aliases.py.md) (1 shared connections)

## Source Files

- `src/hal0/normalize/resolver.py`
- `tests/normalize/test_resolver.py`

## Audit Trail

- EXTRACTED: 85 (72%)
- INFERRED: 33 (28%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*