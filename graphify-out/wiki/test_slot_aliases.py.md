# test_slot_aliases.py

> 36 nodes · cohesion 0.06

## Key Concepts

- **test_slot_aliases.py** (23 connections) — `tests/slots/test_slot_aliases.py`
- **mock_slot_manager()** (4 connections) — `tests/slots/test_slot_aliases.py`
- **test_proxy_fallback_uses_agent()** (4 connections) — `tests/slots/test_slot_aliases.py`
- **test_chat_slot_alias_map_alias_does_not_override_explicit()** (3 connections) — `tests/slots/test_slot_aliases.py`
- **test_chat_slot_alias_map_includes_agent_canonical()** (3 connections) — `tests/slots/test_slot_aliases.py`
- **test_chat_slot_alias_map_utility_no_alias_injection()** (3 connections) — `tests/slots/test_slot_aliases.py`
- **test_resolve_chain_hal0_chat_is_not_canonical()** (3 connections) — `tests/slots/test_slot_aliases.py`
- **tmp_slots_dir()** (3 connections) — `tests/slots/test_slot_aliases.py`
- **Path** (2 connections)
- **test_agent_is_gpu_seeded_not_npu()** (2 connections) — `tests/slots/test_slot_aliases.py`
- **test_iter_configs_does_not_leak_aliases()** (2 connections) — `tests/slots/test_slot_aliases.py`
- **test_list_does_not_contain_aliases()** (2 connections) — `tests/slots/test_slot_aliases.py`
- **test_manager_resolve_alias_agent_hermes_maps_to_agent()** (2 connections) — `tests/slots/test_slot_aliases.py`
- **test_resolver_no_virtual_alias_map()** (2 connections) — `tests/slots/test_slot_aliases.py`
- **test_seeded_slots_uses_utility_not_chat()** (2 connections) — `tests/slots/test_slot_aliases.py`
- **test_slot_aliases_map()** (2 connections) — `tests/slots/test_slot_aliases.py`
- **Tests for the slot back-compat alias system (issue #654 / #633, ADR-0023).  ADR-** (1 connections) — `tests/slots/test_slot_aliases.py`
- **_resolve_alias("agent-hermes") == "agent" and the agent TOML exists.** (1 connections) — `tests/slots/test_slot_aliases.py`
- **list() enumerates TOMLs from disk — no alias appears in the result.** (1 connections) — `tests/slots/test_slot_aliases.py`
- **iter_configs() is driven by disk TOMLs — aliases never appear.** (1 connections) — `tests/slots/test_slot_aliases.py`
- **No alias map; hal0/primary is not a known virtual.** (1 connections) — `tests/slots/test_slot_aliases.py`
- **hal0/chat is no longer a canonical virtual. It only resolves at all if a     lef** (1 connections) — `tests/slots/test_slot_aliases.py`
- **hal0_chat_slot_alias_map returns the canonical agent slot's model_id.** (1 connections) — `tests/slots/test_slot_aliases.py`
- **A utility-only slot set carries `utility` but injects no extra alias     (agent-** (1 connections) — `tests/slots/test_slot_aliases.py`
- **If a literal 'agent-hermes' slot still exists on disk, it takes precedence.** (1 connections) — `tests/slots/test_slot_aliases.py`
- *... and 11 more nodes in this community*

## Relationships

- [Upstream](Upstream.md) (4 shared connections)
- [lifespan](lifespan.md) (3 shared connections)
- [SlotManager](SlotManager.md) (1 shared connections)
- [UpstreamRegistry](UpstreamRegistry.md) (1 shared connections)
- [resolve_chain](resolve_chain.md) (1 shared connections)

## Source Files

- `tests/slots/test_slot_aliases.py`

## Audit Trail

- EXTRACTED: 74 (90%)
- INFERRED: 8 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*