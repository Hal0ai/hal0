# TestImageMismatch

> 9 nodes

## Key Concepts

- **TestImageMismatch** (9 connections) — `tests/providers/test_container.py`
- **_image_mismatch()** (8 connections) — `src/hal0/providers/container.py`
- **.test_no_mismatch_when_running_equals_declared()** (2 connections) — `tests/providers/test_container.py`
- **.test_mismatch_when_running_differs_from_declared()** (2 connections) — `tests/providers/test_container.py`
- **.test_no_mismatch_when_running_unknown()** (2 connections) — `tests/providers/test_container.py`
- **.test_no_mismatch_when_declared_unknown()** (2 connections) — `tests/providers/test_container.py`
- **.test_whitespace_is_ignored()** (2 connections) — `tests/providers/test_container.py`
- **Return True iff both image refs are known AND differ (#663).      The determinis** (1 connections) — `src/hal0/providers/container.py`
- **#663 - _image_mismatch compares the running image ref vs the declared profile im** (1 connections) — `tests/providers/test_container.py`

## Relationships

- [_resolve_llama_scalars](_resolve_llama_scalars.md) (1 shared connections)
- [container_enrichment](container_enrichment.md) (1 shared connections)
- [Mount](Mount.md) (1 shared connections)
- [ContainerProvider](ContainerProvider.md) (1 shared connections)
- [resolve_profile_flags](resolve_profile_flags.md) (1 shared connections)

## Source Files

- `src/hal0/providers/container.py`
- `tests/providers/test_container.py`

## Audit Trail

- EXTRACTED: 16 (55%)
- INFERRED: 13 (45%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*