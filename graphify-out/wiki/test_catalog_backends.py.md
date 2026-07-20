# test_catalog_backends.py

> 14 nodes · cohesion 0.21

## Key Concepts

- **test_catalog_backends.py** (7 connections) — `tests/capabilities/test_catalog_backends.py`
- **_npu_only_hw()** (6 connections) — `tests/capabilities/test_catalog_backends.py`
- **test_flm_probe_not_hardcoded_docker()** (5 connections) — `tests/capabilities/test_catalog_backends.py`
- **test_npu_advertised_under_podman_only()** (5 connections) — `tests/capabilities/test_catalog_backends.py`
- **test_npu_hidden_when_image_absent()** (5 connections) — `tests/capabilities/test_catalog_backends.py`
- **MonkeyPatch** (3 connections)
- **_reset_probe_cache()** (3 connections) — `tests/capabilities/test_catalog_backends.py`
- **Any** (2 connections)
- **SC-2 — the NPU picker probe must honour a podman-only runtime.  ``available_back** (1 connections) — `tests/capabilities/test_catalog_backends.py`
- **A HardwareInfo-shaped stub: NPU present, no GPUs.** (1 connections) — `tests/capabilities/test_catalog_backends.py`
- **Ensure each test starts and ends with a clean image-present cache.** (1 connections) — `tests/capabilities/test_catalog_backends.py`
- **NPU is advertised when podman (not docker) reports the image present.** (1 connections) — `tests/capabilities/test_catalog_backends.py`
- **Runtime resolves but the image inspect fails → no NPU backend.** (1 connections) — `tests/capabilities/test_catalog_backends.py`
- **The resolved argv never equals literal ``docker`` under podman, and the     prob** (1 connections) — `tests/capabilities/test_catalog_backends.py`

## Relationships

- [StacksCatalog](StacksCatalog.md) (3 shared connections)
- [types.py](types.py.md) (1 shared connections)

## Source Files

- `tests/capabilities/test_catalog_backends.py`

## Audit Trail

- EXTRACTED: 33 (79%)
- INFERRED: 9 (21%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*