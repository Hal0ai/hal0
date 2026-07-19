# evaluate_model_fit

> 26 nodes

## Key Concepts

- **evaluate_model_fit()** (14 connections) — `src/hal0/model_fit.py`
- **test_model_fit_validation.py** (9 connections) — `tests/capabilities/test_model_fit_validation.py`
- **model_fit.py** (7 connections) — `src/hal0/model_fit.py`
- **test_model_fit.py** (7 connections) — `tests/model_fit/test_model_fit.py`
- **_orch()** (6 connections) — `tests/capabilities/test_model_fit_validation.py`
- **ModelFit** (5 connections) — `src/hal0/model_fit.py`
- **FakeSlotManager** (5 connections) — `tests/capabilities/test_model_fit_validation.py`
- **MonkeyPatch** (5 connections)
- **test_validate_model_fit_passes_registry_for_registry_model()** (5 connections) — `tests/capabilities/test_model_fit_validation.py`
- **FakeRegistry** (5 connections) — `tests/model_fit/test_model_fit.py`
- **test_validate_model_fit_blocks_wrong_model_class()** (4 connections) — `tests/capabilities/test_model_fit_validation.py`
- **test_validate_model_fit_blocks_profile_unsupported_slot_type()** (4 connections) — `tests/capabilities/test_model_fit_validation.py`
- **test_allows_matching_llm_gpu_profile()** (4 connections) — `tests/model_fit/test_model_fit.py`
- **test_degrades_gpu_cpu_profile_mismatch()** (4 connections) — `tests/model_fit/test_model_fit.py`
- **test_validate_model_fit_allows_npu_embedding()** (3 connections) — `tests/capabilities/test_model_fit_validation.py`
- **test_models_for_capability_filters_backends_blocked_by_model_fit()** (3 connections) — `tests/capabilities/test_model_fit_validation.py`
- **test_blocks_model_slot_type_mismatch()** (3 connections) — `tests/model_fit/test_model_fit.py`
- **test_blocks_profile_slot_type_mismatch()** (3 connections) — `tests/model_fit/test_model_fit.py`
- **test_blocks_npu_profile_device_mismatch()** (3 connections) — `tests/model_fit/test_model_fit.py`
- **.allowed()** (1 connections) — `src/hal0/model_fit.py`
- **Any** (1 connections)
- **ModelFit — contextual model/slot/device/profile compatibility.  ``model_meta`` o** (1 connections) — `src/hal0/model_fit.py`
- **Compatibility verdict for one model candidate.** (1 connections) — `src/hal0/model_fit.py`
- **Return whether a model can run in a slot/device/profile context.      The result** (1 connections) — `src/hal0/model_fit.py`
- **.__init__()** (1 connections) — `tests/model_fit/test_model_fit.py`
- *... and 1 more nodes in this community*

## Relationships

- [ProfileConfig](ProfileConfig.md) (7 shared connections)
- [catalog.py](catalog.py.md) (3 shared connections)
- [CapabilityOrchestrator](CapabilityOrchestrator.md) (3 shared connections)
- [.apply](apply.md) (2 shared connections)
- [BadRequest](BadRequest.md) (2 shared connections)
- [slots.py](slots.py.md) (1 shared connections)
- [recommend_primary_slot](recommend_primary_slot.md) (1 shared connections)
- [errors.py](errors.py.md) (1 shared connections)

## Source Files

- `src/hal0/model_fit.py`
- `tests/capabilities/test_model_fit_validation.py`
- `tests/model_fit/test_model_fit.py`

## Audit Trail

- EXTRACTED: 78 (74%)
- INFERRED: 28 (26%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*