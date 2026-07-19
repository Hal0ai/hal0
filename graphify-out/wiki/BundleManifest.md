# BundleManifest

> 12 nodes · cohesion 0.24

## Key Concepts

- **BundleManifest** (12 connections) — `src/hal0/bundles/schema.py`
- **load_all_bundles()** (6 connections) — `src/hal0/bundles/tiers.py`
- **_load_cached()** (6 connections) — `src/hal0/bundles/tiers.py`
- **.from_json()** (5 connections) — `src/hal0/bundles/schema.py`
- **.from_path()** (5 connections) — `src/hal0/bundles/schema.py`
- **load_bundle()** (5 connections) — `src/hal0/bundles/tiers.py`
- **test_manifest_preserves_extra_block()** (4 connections) — `tests/bundles/test_schema.py`
- **Path** (1 connections)
- **The full on-disk bundle JSON shape.      The ``omni`` block is the ``collection.** (1 connections) — `src/hal0/bundles/schema.py`
- **Load every bundle in :data:`BUNDLES` order.      Tests that need to assert again** (1 connections) — `src/hal0/bundles/tiers.py`
- **Cached manifest load. Keyed on the bundle name only — bumping the     on-disk fi** (1 connections) — `src/hal0/bundles/tiers.py`
- **Return the manifest for ``name``. Raises FileNotFoundError if     the file is mi** (1 connections) — `src/hal0/bundles/tiers.py`

## Relationships

- [test_schema.py](test_schema.py.md) (7 shared connections)
- [tiers.py](tiers.py.md) (4 shared connections)
- [Bundle](Bundle.md) (2 shared connections)
- [ModelEntry](ModelEntry.md) (2 shared connections)
- [eligibility.py](eligibility.py.md) (1 shared connections)

## Source Files

- `src/hal0/bundles/schema.py`
- `src/hal0/bundles/tiers.py`
- `tests/bundles/test_schema.py`

## Audit Trail

- EXTRACTED: 47 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*