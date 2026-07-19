# test_manager_npu_container.py

> 21 nodes

## Key Concepts

- **test_manager_npu_container.py** (10 connections) — `tests/slots/test_manager_npu_container.py`
- **_write_npu_container_slot()** (10 connections) — `tests/slots/test_manager_npu_container.py`
- **_make_container_provider_mock()** (9 connections) — `tests/slots/test_manager_npu_container.py`
- **Path** (8 connections)
- **test_registry_style_model_id_does_not_take_flm_path()** (8 connections) — `tests/slots/test_manager_npu_container.py`
- **test_flm_inference_gate_runs_once_and_promotes_to_ready()** (7 connections) — `tests/slots/test_manager_npu_container.py`
- **test_flm_inference_gate_passes_assigned_model()** (7 connections) — `tests/slots/test_manager_npu_container.py`
- **test_flm_inference_gate_wedged_npu_stays_warming()** (7 connections) — `tests/slots/test_manager_npu_container.py`
- **test_npu_container_slot_spawns_with_flm_tag()** (6 connections) — `tests/slots/test_manager_npu_container.py`
- **test_npu_container_slot_spawns_with_toml_default()** (6 connections) — `tests/slots/test_manager_npu_container.py`
- **test_await_ready_health_timeout_stays_non_dispatchable()** (6 connections) — `tests/slots/test_manager_npu_container.py`
- **SlotManager: npu container slot spawns through ContainerProvider with the FLM ta** (1 connections) — `tests/slots/test_manager_npu_container.py`
- **Write a minimal npu container slot TOML.** (1 connections) — `tests/slots/test_manager_npu_container.py`
- **Build a MagicMock ContainerProvider with load_sync and wait_ready stubs.** (1 connections) — `tests/slots/test_manager_npu_container.py`
- **load('npu', 'gemma3:4b') calls ContainerProvider.load_sync with flm_tag set.** (1 connections) — `tests/slots/test_manager_npu_container.py`
- **load('npu') with no model_id arg uses [model].default from TOML.** (1 connections) — `tests/slots/test_manager_npu_container.py`
- **A plain registry-style id (no ``:``) falls through to registry lookup.      _res** (1 connections) — `tests/slots/test_manager_npu_container.py`
- **A /health wait timeout resolves to WARMING (non-dispatchable), not READY.      D** (1 connections) — `tests/slots/test_manager_npu_container.py`
- **The warm→ready gate runs the real-inference sentinel EXACTLY ONCE and,     on su** (1 connections) — `tests/slots/test_manager_npu_container.py`
- **Regression (#1171): the warm→ready gate must tell verify_inference which     mod** (1 connections) — `tests/slots/test_manager_npu_container.py`
- **A wedged NPU that lists a model but can't infer resolves to retryable     WARMIN** (1 connections) — `tests/slots/test_manager_npu_container.py`

## Relationships

- [SlotManager](SlotManager.md) (7 shared connections)
- [FLMProvider](FLMProvider.md) (3 shared connections)
- [flm.py](flm.py.md) (1 shared connections)
- [Model](Model.md) (1 shared connections)

## Source Files

- `tests/slots/test_manager_npu_container.py`

## Audit Trail

- EXTRACTED: 82 (87%)
- INFERRED: 12 (13%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*