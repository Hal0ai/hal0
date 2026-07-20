# _match_curated

> 12 nodes · cohesion 0.18

## Key Concepts

- **_match_curated()** (8 connections) — `src/hal0/registry/discover.py`
- **test_curated_pull_coords.py** (5 connections) — `tests/registry/test_curated_pull_coords.py`
- **test_get_curated_resolves_exact_coords()** (3 connections) — `tests/registry/test_curated_pull_coords.py`
- **test_match_curated_by_filename()** (3 connections) — `tests/registry/test_curated_pull_coords.py`
- **test_match_curated_seed_stack_files()** (3 connections) — `tests/registry/test_curated_pull_coords.py`
- **test_no_duplicate_ids()** (2 connections) — `tests/registry/test_curated_pull_coords.py`
- **Return the curated entry whose ``hf_file`` equals ``filename``.** (1 connections) — `src/hal0/registry/discover.py`
- **Tests for the custom-GGUF curated coords added in fix/stack-model-pull-coords.** (1 connections) — `tests/registry/test_curated_pull_coords.py`
- **The new ids do not collide with any existing curated id.** (1 connections) — `tests/registry/test_curated_pull_coords.py`
- **Each new id resolves via get_curated() to the EXACT hf_repo/hf_file.      This i** (1 connections) — `tests/registry/test_curated_pull_coords.py`
- **The on-disk filename resolves back to the curated entry (scan-backfill).** (1 connections) — `tests/registry/test_curated_pull_coords.py`
- **The three seed-stack files match their curated entries by filename.** (1 connections) — `tests/registry/test_curated_pull_coords.py`

## Relationships

- [discover.py](discover.py.md) (2 shared connections)
- [CuratedModel](CuratedModel.md) (1 shared connections)
- [scan_and_register](scan_and_register.md) (1 shared connections)
- [test_discover.py](test_discover.py.md) (1 shared connections)
- [get_curated](get_curated.md) (1 shared connections)

## Source Files

- `src/hal0/registry/discover.py`
- `tests/registry/test_curated_pull_coords.py`

## Audit Trail

- EXTRACTED: 25 (83%)
- INFERRED: 5 (17%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*