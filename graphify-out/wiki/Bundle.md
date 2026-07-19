# Bundle

> 18 nodes

## Key Concepts

- **Bundle** (9 connections) — `src/hal0/bundles/schema.py`
- **ModelEntry** (6 connections) — `src/hal0/bundles/schema.py`
- **Any** (6 connections)
- **.from_dict()** (6 connections) — `src/hal0/bundles/schema.py`
- **.from_dict()** (5 connections) — `src/hal0/bundles/schema.py`
- **.from_dict()** (5 connections) — `src/hal0/bundles/schema.py`
- **schema.py** (4 connections) — `src/hal0/bundles/schema.py`
- **.to_dict()** (3 connections) — `src/hal0/bundles/schema.py`
- **test_model_entry_round_trip()** (3 connections) — `tests/bundles/test_schema.py`
- **.to_dict()** (2 connections) — `src/hal0/bundles/schema.py`
- **.total_size_gb()** (2 connections) — `src/hal0/bundles/schema.py`
- **test_model_entry_lru_defaults_false()** (2 connections) — `tests/bundles/test_schema.py`
- **test_bundle_total_size_handles_missing_primary_and_coder()** (2 connections) — `tests/bundles/test_schema.py`
- **test_manifest_from_dict_rejects_missing_hal0_block()** (2 connections) — `tests/bundles/test_schema.py`
- **Typed dataclasses for bundle manifests.  Bundle manifests on disk are JSON files** (1 connections) — `src/hal0/bundles/schema.py`
- **One model assignment inside a bundle.      ``slot`` is the hal0 slot id (``chat.** (1 connections) — `src/hal0/bundles/schema.py`
- **The hal0 bundle metadata block.      Maps 1:1 onto a row of the plan §8.2 table.** (1 connections) — `src/hal0/bundles/schema.py`
- **Sum of declared model sizes for the install-time download estimate.** (1 connections) — `src/hal0/bundles/schema.py`

## Relationships

- [test_schema.py](test_schema.py.md) (9 shared connections)
- [BundleManifest](BundleManifest.md) (5 shared connections)
- [stacks.jsx](stacks.jsx.md) (1 shared connections)

## Source Files

- `src/hal0/bundles/schema.py`
- `tests/bundles/test_schema.py`

## Audit Trail

- EXTRACTED: 58 (95%)
- INFERRED: 3 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*