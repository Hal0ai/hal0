# BundleManifest

> 12 nodes

## Key Concepts

- **BundleManifest** (12 connections) — `src/hal0/bundles/schema.py`
- **_load_cached()** (6 connections) — `src/hal0/bundles/tiers.py`
- **load_all_bundles()** (6 connections) — `src/hal0/bundles/tiers.py`
- **load_bundle()** (5 connections) — `src/hal0/bundles/tiers.py`
- **.to_dict()** (4 connections) — `src/hal0/bundles/schema.py`
- **list_bundle_summaries()** (4 connections) — `src/hal0/bundles/tiers.py`
- **.to_json()** (2 connections) — `src/hal0/bundles/schema.py`
- **The full on-disk bundle JSON shape.      The ``omni`` block is the ``collection.** (1 connections) — `src/hal0/bundles/schema.py`
- **Cached manifest load. Keyed on the bundle name only — bumping the     on-disk fi** (1 connections) — `src/hal0/bundles/tiers.py`
- **Return the manifest for ``name``. Raises FileNotFoundError if     the file is mi** (1 connections) — `src/hal0/bundles/tiers.py`
- **Load every bundle in :data:`BUNDLES` order.      Tests that need to assert again** (1 connections) — `src/hal0/bundles/tiers.py`
- **Project the manifests onto the lightweight :class:`Bundle` shape.      Used by `** (1 connections) — `src/hal0/bundles/tiers.py`

## Relationships

- [Bundle](Bundle.md) (5 shared connections)
- [test_schema.py](test_schema.py.md) (5 shared connections)
- [tiers.py](tiers.py.md) (5 shared connections)
- [eligibility.py](eligibility.py.md) (1 shared connections)

## Source Files

- `src/hal0/bundles/schema.py`
- `src/hal0/bundles/tiers.py`

## Audit Trail

- EXTRACTED: 43 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*