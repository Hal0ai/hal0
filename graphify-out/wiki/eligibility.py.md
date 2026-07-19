# eligibility.py

> 11 nodes

## Key Concepts

- **eligibility.py** (5 connections) — `src/hal0/bundles/eligibility.py`
- **_read_meminfo_gb()** (4 connections) — `src/hal0/bundles/eligibility.py`
- **host_ram_gb()** (4 connections) — `src/hal0/bundles/eligibility.py`
- **eligible_tiers()** (4 connections) — `src/hal0/bundles/eligibility.py`
- **reset_cache()** (2 connections) — `src/hal0/bundles/eligibility.py`
- **Path** (1 connections)
- **Hardware-anchored tier eligibility.  Reads ``/proc/meminfo`` once per process an** (1 connections) — `src/hal0/bundles/eligibility.py`
- **Parse MemTotal out of /proc/meminfo and return whole GB.      Returns ``0`` if t** (1 connections) — `src/hal0/bundles/eligibility.py`
- **Detected unified RAM in whole GB. Process-lifetime cached.** (1 connections) — `src/hal0/bundles/eligibility.py`
- **Return bundle names whose ``min_ram_gb`` <= host RAM.      The returned list pre** (1 connections) — `src/hal0/bundles/eligibility.py`
- **Drop the cached probe + eligibility lists. Test-only.** (1 connections) — `src/hal0/bundles/eligibility.py`

## Relationships

- [BundleManifest](BundleManifest.md) (1 shared connections)

## Source Files

- `src/hal0/bundles/eligibility.py`

## Audit Trail

- EXTRACTED: 24 (96%)
- INFERRED: 1 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*