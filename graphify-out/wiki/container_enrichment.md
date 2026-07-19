# container_enrichment

> 23 nodes

## Key Concepts

- **container_enrichment()** (29 connections) — `src/hal0/slot_view/__init__.py`
- **FakeContainerProvider** (22 connections) — `tests/slot_view/test_aggregator.py`
- **_container_cfg()** (14 connections) — `tests/slot_view/test_aggregator.py`
- **TestContainerEnrichment** (14 connections) — `tests/slot_view/test_aggregator.py`
- **.test_inflight_pull_job_wins_image_status()** (5 connections) — `tests/slot_view/test_aggregator.py`
- **.test_non_npu_slots_unaffected()** (5 connections) — `tests/slot_view/test_aggregator.py`
- **.test_running_and_healthy()** (4 connections) — `tests/slot_view/test_aggregator.py`
- **.test_device_class_and_backend_lifted_from_profile()** (4 connections) — `tests/slot_view/test_aggregator.py`
- **.test_device_class_for_non_gpu_profile()** (4 connections) — `tests/slot_view/test_aggregator.py`
- **.test_active_but_unhealthy_is_starting()** (4 connections) — `tests/slot_view/test_aggregator.py`
- **.test_active_without_port_is_running_unhealthy()** (4 connections) — `tests/slot_view/test_aggregator.py`
- **.test_inactive_is_stopped()** (4 connections) — `tests/slot_view/test_aggregator.py`
- **.test_provider_failure_degrades_to_stopped()** (4 connections) — `tests/slot_view/test_aggregator.py`
- **.test_every_slot_is_probed()** (4 connections) — `tests/slot_view/test_aggregator.py`
- **.test_actual_image_surfaced()** (4 connections) — `tests/slot_view/test_aggregator.py`
- **.test_no_image_means_not_configured_status()** (4 connections) — `tests/slot_view/test_aggregator.py`
- **.test_npu_table_surfaced()** (4 connections) — `tests/slot_view/test_aggregator.py`
- **Per-slot live container state.      For each slot, probes two live sources:** (1 connections) — `src/hal0/slot_view/__init__.py`
- **.__init__()** (1 connections) — `tests/slot_view/test_aggregator.py`
- **.is_active()** (1 connections) — `tests/slot_view/test_aggregator.py`
- **.running_image()** (1 connections) — `tests/slot_view/test_aggregator.py`
- **.image_present()** (1 connections) — `tests/slot_view/test_aggregator.py`
- **Duck-typed stand-in for ContainerProvider (sync + async mix matches).** (1 connections) — `tests/slot_view/test_aggregator.py`

## Relationships

- [MapContainerProvider](MapContainerProvider.md) (11 shared connections)
- [Any](Any.md) (4 shared connections)
- [SlotState](SlotState.md) (4 shared connections)
- [_slot](_slot.md) (4 shared connections)
- [slots.py](slots.py.md) (1 shared connections)
- [load_profiles_config](load_profiles_config.md) (1 shared connections)
- [_resolve_llama_scalars](_resolve_llama_scalars.md) (1 shared connections)
- [TestImageMismatch](TestImageMismatch.md) (1 shared connections)
- [config_enrichment](config_enrichment.md) (1 shared connections)
- [FakeUpstreams](FakeUpstreams.md) (1 shared connections)

## Source Files

- `src/hal0/slot_view/__init__.py`
- `tests/slot_view/test_aggregator.py`

## Audit Trail

- EXTRACTED: 100 (72%)
- INFERRED: 39 (28%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*