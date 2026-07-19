# test_hermes_provision_collect.py

> 11 nodes

## Key Concepts

- **test_hermes_provision_collect.py** (6 connections) — `tests/agents/test_hermes_provision_collect.py`
- **_load()** (5 connections) — `tests/agents/test_hermes_provision_collect.py`
- **test_collect_chat_slots_against_real_lxc_payload()** (3 connections) — `tests/agents/test_hermes_provision_collect.py`
- **test_collect_chat_slots_aliases_use_stable_gateway_base_url()** (3 connections) — `tests/agents/test_hermes_provision_collect.py`
- **test_collect_chat_slots_skips_non_llm_capabilities()** (3 connections) — `tests/agents/test_hermes_provision_collect.py`
- **test_collect_chat_slots_includes_cold_llm_slots()** (3 connections) — `tests/agents/test_hermes_provision_collect.py`
- **R4 H1 regression — ``_collect_chat_slots`` filter against real LXC payloads.  Th** (1 connections) — `tests/agents/test_hermes_provision_collect.py`
- **For each scenario, ``_collect_chat_slots`` returns all enabled     llm slots wit** (1 connections) — `tests/agents/test_hermes_provision_collect.py`
- **Alias base_url must be the STABLE hal0 gateway, NOT the slot's raw     per-slot** (1 connections) — `tests/agents/test_hermes_provision_collect.py`
- **Embed/rerank/stt/tts slots must never appear in chat aliases even     when ready** (1 connections) — `tests/agents/test_hermes_provision_collect.py`
- **Cold/unready llm slots ARE collected — dispatch cold-loads them     on demand.** (1 connections) — `tests/agents/test_hermes_provision_collect.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/agents/test_hermes_provision_collect.py`

## Audit Trail

- EXTRACTED: 28 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*