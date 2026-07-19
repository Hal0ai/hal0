# image_cache.py

> 14 nodes · cohesion 0.21

## Key Concepts

- **image_cache.py** (7 connections) — `src/hal0/api/image_cache.py`
- **cache_dir()** (6 connections) — `src/hal0/api/image_cache.py`
- **_png_path()** (5 connections) — `src/hal0/api/image_cache.py`
- **_evict_if_over_budget()** (4 connections) — `src/hal0/api/image_cache.py`
- **write_png()** (4 connections) — `src/hal0/api/image_cache.py`
- **read_png()** (3 connections) — `src/hal0/api/image_cache.py`
- **Path** (2 connections)
- **On-disk PNG cache for ``/v1/images/generations`` URL responses.  When the OpenAI** (1 connections) — `src/hal0/api/image_cache.py`
- **Write ``png_bytes`` to the cache, return the bare uuid stem.      Caller assembl** (1 connections) — `src/hal0/api/image_cache.py`
- **Read a cached PNG by name (with or without .png suffix).      Returns None if th** (1 connections) — `src/hal0/api/image_cache.py`
- **# NOTE: The cache directory is intentionally NOT cleared at install or** (1 connections) — `src/hal0/api/image_cache.py`
- **Return ``/var/lib/hal0/images/cache``, creating it on demand.** (1 connections) — `src/hal0/api/image_cache.py`
- **Resolve a cache name to a path, or None if the name is unsafe.** (1 connections) — `src/hal0/api/image_cache.py`
- **Drop oldest PNGs until under both ceilings.      Uses ``st_mtime`` as the LRU pr** (1 connections) — `src/hal0/api/image_cache.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `src/hal0/api/image_cache.py`

## Audit Trail

- EXTRACTED: 38 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*