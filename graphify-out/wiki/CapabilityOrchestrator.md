# CapabilityOrchestrator

> 72 nodes · cohesion 0.07

## Key Concepts

- **CapabilityOrchestrator** (54 connections) — `src/hal0/capabilities/orchestrator.py`
- **FakeSlotManager** (30 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **test_orchestrator_reconciliation.py** (29 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **Path** (19 connections)
- **_write_caps()** (13 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **_StubSlot** (12 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **_write_embed_slot()** (10 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **_anchor_npu_writes()** (9 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **MonkeyPatch** (9 connections)
- **_read_slot_toml()** (9 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **test_embed_gpu_to_npu_no_load()** (9 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **test_npu_embed_anchor_offline_still_pending()** (9 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **test_npu_embed_enable_container_anchor_without_external_runtime()** (9 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **test_apply_commit_failure_leaves_both_files_at_before()** (8 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **test_apply_lifecycle_failure_still_persists_intent()** (8 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **test_embed_npu_to_gpu_zeroes_anchor_toggle_and_loads()** (8 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **test_npu_embed_disable_writes_anchor_toggle_off_no_unload()** (8 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **test_npu_embed_enable_writes_anchor_toggle_no_load()** (8 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **.set_configs()** (7 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **Any** (7 connections)
- **test_disable_hides_slot_from_routing()** (7 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **test_npu_embed_existing_slot_without_type_gets_typed()** (7 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **test_npu_stt_enable_sets_asr()** (7 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **npu_orchestrator()** (6 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- **orchestrator()** (6 connections) — `tests/capabilities/test_orchestrator_reconciliation.py`
- *... and 47 more nodes in this community*

## Relationships

- [.apply](apply.md) (15 shared connections)
- [FakeSlotManager](FakeSlotManager.md) (6 shared connections)
- [CapabilitySelection](CapabilitySelection.md) (4 shared connections)
- [evaluate_model_fit](evaluate_model_fit.md) (3 shared connections)
- [test_trio_status_inheritance.py](test_trio_status_inheritance.py.md) (3 shared connections)
- [test_tts_capability_switch.py](test_tts_capability_switch.py.md) (3 shared connections)
- [lifespan](lifespan.md) (1 shared connections)
- [deps.py](deps.py.md) (1 shared connections)
- [.__init__](__init__.md) (1 shared connections)
- [test_slot_config_validation.py](test_slot_config_validation.py.md) (1 shared connections)
- [ProfileCatalog](ProfileCatalog.md) (1 shared connections)
- [errors.py](errors.py.md) (1 shared connections)

## Source Files

- `src/hal0/capabilities/orchestrator.py`
- `tests/capabilities/test_orchestrator_reconciliation.py`

## Audit Trail

- EXTRACTED: 371 (94%)
- INFERRED: 22 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*