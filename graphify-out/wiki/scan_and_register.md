# scan_and_register

> 22 nodes · cohesion 0.13

## Key Concepts

- **scan_and_register()** (17 connections) — `src/hal0/registry/discover.py`
- **ModelRegistry** (14 connections)
- **backfill_coordless()** (10 connections) — `src/hal0/registry/discover.py`
- **test_scan_and_register_attaches_and_omits_sidecar()** (6 connections) — `tests/registry/test_discover.py`
- **test_scan_and_register_backfills_existing_coordless_row()** (6 connections) — `tests/registry/test_discover.py`
- **test_scan_and_register_reranker_capability()** (6 connections) — `tests/registry/test_discover.py`
- **test_scan_and_register_idempotent()** (5 connections) — `tests/registry/test_discover.py`
- **test_scan_and_register_missing_root_is_silent()** (5 connections) — `tests/registry/test_discover.py`
- **test_backfill_coordless_fills_from_curated()** (4 connections) — `tests/registry/test_discover.py`
- **test_backfill_coordless_is_idempotent()** (4 connections) — `tests/registry/test_discover.py`
- **test_backfill_coordless_no_curated_match_left_alone()** (4 connections) — `tests/registry/test_discover.py`
- **test_backfill_coordless_skips_rows_with_coords()** (4 connections) — `tests/registry/test_discover.py`
- **registry()** (3 connections) — `tests/registry/test_discover.py`
- **Repair existing registry rows that have empty HF coordinates.      A row auto-re** (1 connections) — `src/hal0/registry/discover.py`
- **Discover candidates under ``cfg.roots`` and register the new ones.      Returns** (1 connections) — `src/hal0/registry/discover.py`
- **End-to-end: a NON-curated reranker gguf under a scan root registers with     cap** (1 connections) — `tests/registry/test_discover.py`
- **End-to-end: the registered main model resolves its mmproj path, and     no stand** (1 connections) — `tests/registry/test_discover.py`
- **An existing registry row with empty coords whose on-disk filename matches     a** (1 connections) — `tests/registry/test_discover.py`
- **A second backfill pass is a no-op once coords are present.** (1 connections) — `tests/registry/test_discover.py`
- **A row that already carries coords is never touched, even with a curated     matc** (1 connections) — `tests/registry/test_discover.py`
- **A coord-less row with no curated filename match is left as-is.** (1 connections) — `tests/registry/test_discover.py`
- **End-to-end: scan_and_register repairs an existing coord-less row whose     file** (1 connections) — `tests/registry/test_discover.py`

## Relationships

- [test_discover.py](test_discover.py.md) (19 shared connections)
- [ModelsConfig](ModelsConfig.md) (6 shared connections)
- [register_candidate](register_candidate.md) (5 shared connections)
- [discover.py](discover.py.md) (4 shared connections)
- [_match_curated](_match_curated.md) (1 shared connections)
- [lifespan](lifespan.md) (1 shared connections)
- [config.py](config.py.md) (1 shared connections)
- [settings.py](settings.py.md) (1 shared connections)
- [models_service.py](models_service.py.md) (1 shared connections)

## Source Files

- `src/hal0/registry/discover.py`
- `tests/registry/test_discover.py`

## Audit Trail

- EXTRACTED: 70 (72%)
- INFERRED: 27 (28%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*