# Bundle

> 11 nodes · cohesion 0.18

## Key Concepts

- **Bundle** (9 connections) — `src/hal0/bundles/schema.py`
- **schema.py** (4 connections) — `src/hal0/bundles/schema.py`
- **list_bundle_summaries()** (4 connections) — `src/hal0/bundles/tiers.py`
- **.slug()** (2 connections) — `src/hal0/bundles/schema.py`
- **.total_size_gb()** (2 connections) — `src/hal0/bundles/schema.py`
- **test_bundle_total_size_handles_missing_primary_and_coder()** (2 connections) — `tests/bundles/test_schema.py`
- **Typed dataclasses for bundle manifests.  Bundle manifests on disk are JSON files** (1 connections) — `src/hal0/bundles/schema.py`
- **URL-safe lowercase identifier used by the REST surface.** (1 connections) — `src/hal0/bundles/schema.py`
- **Sum of declared model sizes for the install-time download estimate.** (1 connections) — `src/hal0/bundles/schema.py`
- **The hal0 bundle metadata block.      Maps 1:1 onto a row of the plan §8.2 table.** (1 connections) — `src/hal0/bundles/schema.py`
- **Project the manifests onto the lightweight :class:`Bundle` shape.      Used by `** (1 connections) — `src/hal0/bundles/tiers.py`

## Relationships

- [test_schema.py](test_schema.py.md) (3 shared connections)
- [BundleManifest](BundleManifest.md) (2 shared connections)
- [ModelEntry](ModelEntry.md) (2 shared connections)
- [tiers.py](tiers.py.md) (1 shared connections)

## Source Files

- `src/hal0/bundles/schema.py`
- `src/hal0/bundles/tiers.py`
- `tests/bundles/test_schema.py`

## Audit Trail

- EXTRACTED: 28 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*