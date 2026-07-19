# .apply

> 37 nodes · cohesion 0.07

## Key Concepts

- **.apply()** (16 connections) — `src/hal0/capabilities/orchestrator.py`
- **CapabilityApplyFailed** (10 connections) — `src/hal0/capabilities/orchestrator.py`
- **.get_state()** (10 connections) — `src/hal0/capabilities/orchestrator.py`
- **canonical_device()** (9 connections) — `src/hal0/model_meta/__init__.py`
- **orchestrator.py** (8 connections) — `src/hal0/capabilities/orchestrator.py`
- **._ensure_slot_exists()** (8 connections) — `src/hal0/capabilities/orchestrator.py`
- **._validate_model_in_catalog()** (8 connections) — `src/hal0/capabilities/orchestrator.py`
- **._apply_npu_trio_modality()** (6 connections) — `src/hal0/capabilities/orchestrator.py`
- **._ensure_slot_exists_npu()** (6 connections) — `src/hal0/capabilities/orchestrator.py`
- **._selection_with_defaults()** (6 connections) — `src/hal0/capabilities/orchestrator.py`
- **legal_children()** (6 connections) — `src/hal0/capabilities/orchestrator.py`
- **._load()** (5 connections) — `src/hal0/capabilities/orchestrator.py`
- **._set_flm_modality()** (5 connections) — `src/hal0/capabilities/orchestrator.py`
- **._slot_status_string()** (5 connections) — `src/hal0/capabilities/orchestrator.py`
- **spawn_context_refresh()** (4 connections) — `src/hal0/agents/hermes_refresh.py`
- **._npu_anchor_status()** (4 connections) — `src/hal0/capabilities/orchestrator.py`
- **hermes_refresh.py** (2 connections) — `src/hal0/agents/hermes_refresh.py`
- **Any** (2 connections)
- **test_canonical_device()** (2 connections) — `tests/model_meta/test_model_meta.py`
- **Fire-and-forget trigger to refresh the Hermes live-context files.  Called from t** (1 connections) — `src/hal0/agents/hermes_refresh.py`
- **Spawn a detached ``hal0-agent <agent_id> render-context``. Never raises.** (1 connections) — `src/hal0/agents/hermes_refresh.py`
- **Hal0Error** (1 connections)
- **CapabilityOrchestrator — bridge between capability children and slots.  The dash** (1 connections) — `src/hal0/capabilities/orchestrator.py`
- **Return the child names valid for ``slot``.** (1 connections) — `src/hal0/capabilities/orchestrator.py`
- **503 — the underlying SlotManager call failed.      Surfaced to the dashboard as** (1 connections) — `src/hal0/capabilities/orchestrator.py`
- *... and 12 more nodes in this community*

## Relationships

- [CapabilityOrchestrator](CapabilityOrchestrator.md) (15 shared connections)
- [CapabilitySelection](CapabilitySelection.md) (9 shared connections)
- [BadRequest](BadRequest.md) (3 shared connections)
- [test_slot_config_validation.py](test_slot_config_validation.py.md) (3 shared connections)
- [catalog.py](catalog.py.md) (3 shared connections)
- [evaluate_model_fit](evaluate_model_fit.md) (2 shared connections)
- [unknown_slot_config_keys](unknown_slot_config_keys.md) (2 shared connections)
- [test_model_meta.py](test_model_meta.py.md) (2 shared connections)
- [SlotConfigError](SlotConfigError.md) (1 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [is_container_npu_cfg](is_container_npu_cfg.md) (1 shared connections)
- [ProfileCatalog](ProfileCatalog.md) (1 shared connections)

## Source Files

- `src/hal0/agents/hermes_refresh.py`
- `src/hal0/capabilities/orchestrator.py`
- `src/hal0/model_meta/__init__.py`
- `tests/model_meta/test_model_meta.py`

## Audit Trail

- EXTRACTED: 113 (81%)
- INFERRED: 27 (19%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*