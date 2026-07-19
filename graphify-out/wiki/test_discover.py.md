# test_discover.py

> 50 nodes

## Key Concepts

- **test_discover.py** (29 connections) — `tests/registry/test_discover.py`
- **find_candidates()** (25 connections) — `src/hal0/registry/discover.py`
- **Path** (21 connections)
- **ModelRegistry** (14 connections)
- **test_register_candidate_writes_shard_model_file_rows()** (7 connections) — `tests/registry/test_discover.py`
- **test_scan_and_register_reranker_capability()** (6 connections) — `tests/registry/test_discover.py`
- **test_register_candidate_comfyui_checkpoint_tagged_image()** (6 connections) — `tests/registry/test_discover.py`
- **test_scan_and_register_attaches_and_omits_sidecar()** (6 connections) — `tests/registry/test_discover.py`
- **test_scan_and_register_backfills_existing_coordless_row()** (6 connections) — `tests/registry/test_discover.py`
- **test_register_candidate_curated_uses_curated_id()** (5 connections) — `tests/registry/test_discover.py`
- **test_register_candidate_non_curated_uses_suggested_id()** (5 connections) — `tests/registry/test_discover.py`
- **test_scan_and_register_idempotent()** (5 connections) — `tests/registry/test_discover.py`
- **test_scan_and_register_missing_root_is_silent()** (5 connections) — `tests/registry/test_discover.py`
- **test_curated_match_by_filename()** (4 connections) — `tests/registry/test_discover.py`
- **test_known_paths_short_circuit()** (4 connections) — `tests/registry/test_discover.py`
- **test_find_candidates_associates_sidecar()** (4 connections) — `tests/registry/test_discover.py`
- **test_find_candidates_no_sidecar_is_none()** (4 connections) — `tests/registry/test_discover.py`
- **test_backfill_coordless_fills_from_curated()** (4 connections) — `tests/registry/test_discover.py`
- **test_backfill_coordless_is_idempotent()** (4 connections) — `tests/registry/test_discover.py`
- **test_backfill_coordless_skips_rows_with_coords()** (4 connections) — `tests/registry/test_discover.py`
- **test_backfill_coordless_no_curated_match_left_alone()** (4 connections) — `tests/registry/test_discover.py`
- **test_find_candidates_groups_complete_shard_set()** (4 connections) — `tests/registry/test_discover.py`
- **test_find_candidates_drops_incomplete_shard_set()** (4 connections) — `tests/registry/test_discover.py`
- **model_root()** (3 connections) — `tests/registry/test_discover.py`
- **registry()** (3 connections) — `tests/registry/test_discover.py`
- *... and 25 more nodes in this community*

## Relationships

- [scan_and_register](scan_and_register.md) (22 shared connections)
- [ModelsConfig](ModelsConfig.md) (5 shared connections)
- [_guess_capability](_guess_capability.md) (3 shared connections)
- [models_service.py](models_service.py.md) (1 shared connections)
- [connect](connect.md) (1 shared connections)

## Source Files

- `src/hal0/registry/discover.py`
- `tests/registry/test_discover.py`

## Audit Trail

- EXTRACTED: 175 (80%)
- INFERRED: 45 (20%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*