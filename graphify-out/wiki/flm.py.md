# flm.py

> 38 nodes · cohesion 0.08

## Key Concepts

- **flm.py** (24 connections) — `src/hal0/providers/flm.py`
- **flm_served_models()** (15 connections) — `src/hal0/providers/flm.py`
- **Any** (13 connections)
- **_probe_flm_catalog()** (7 connections) — `src/hal0/providers/flm.py`
- **flm_host_spawn_kwargs()** (6 connections) — `src/hal0/providers/flm.py`
- **_classify_flm_model()** (5 connections) — `src/hal0/providers/flm.py`
- **flm_host_async_spawn()** (5 connections) — `src/hal0/providers/flm.py`
- **is_flm_tag()** (5 connections) — `src/hal0/providers/flm.py`
- **_from_container()** (5 connections) — `src/hal0/slots/flm_catalog.py`
- **list_models()** (5 connections) — `src/hal0/slots/flm_catalog.py`
- **_extract_json_object()** (4 connections) — `src/hal0/providers/flm.py`
- **flm_validate()** (4 connections) — `src/hal0/providers/flm.py`
- **is_installed_flm_id()** (4 connections) — `src/hal0/providers/flm.py`
- **.health()** (3 connections) — `src/hal0/providers/flm.py`
- **.verify_embed()** (3 connections) — `src/hal0/providers/flm.py`
- **.verify_inference()** (3 connections) — `src/hal0/providers/flm.py`
- **reset_flm_catalog_cache()** (3 connections) — `src/hal0/providers/flm.py`
- **flm_catalog.py** (3 connections) — `src/hal0/slots/flm_catalog.py`
- **parse_flm_progress()** (2 connections) — `src/hal0/providers/flm.py`
- **Any** (2 connections)
- **FLMProvider — AMD NPU (XDNA2) inference backend.  FLM (Flexible Language Model)** (1 connections) — `src/hal0/providers/flm.py`
- **Drop the cached FLM catalog so the next call re-probes immediately.      Exposed** (1 connections) — `src/hal0/providers/flm.py`
- **True iff ``model_id`` matches an FLM-served tag.      Routing helper for the pul** (1 connections) — `src/hal0/providers/flm.py`
- **True iff ``model_id`` is the ``<tag>-FLM`` id of an INSTALLED FLM model.      FL** (1 connections) — `src/hal0/providers/flm.py`
- **Extract ``(bytes_downloaded, bytes_total)`` from a ``flm pull`` line.      FLM e** (1 connections) — `src/hal0/providers/flm.py`
- *... and 13 more nodes in this community*

## Relationships

- [.container_spec](container_spec.md) (9 shared connections)
- [FLMProvider](FLMProvider.md) (4 shared connections)
- [Model](Model.md) (4 shared connections)
- [FLMInferError](FLMInferError.md) (3 shared connections)
- [SlotConfigError](SlotConfigError.md) (2 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [hardware.py](hardware.py.md) (1 shared connections)
- [npu_occupancy](npu_occupancy.md) (1 shared connections)
- [catalog.py](catalog.py.md) (1 shared connections)
- [models_service.py](models_service.py.md) (1 shared connections)
- [build_per_slot](build_per_slot.md) (1 shared connections)
- [probe.py](probe.py.md) (1 shared connections)

## Source Files

- `src/hal0/providers/flm.py`
- `src/hal0/slots/flm_catalog.py`

## Audit Trail

- EXTRACTED: 122 (88%)
- INFERRED: 17 (12%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*