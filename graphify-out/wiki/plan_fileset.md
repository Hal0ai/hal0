# plan_fileset

> 70 nodes

## Key Concepts

- **plan_fileset()** (28 connections) — `src/hal0/registry/fileset.py`
- **RawTreeEntry** (24 connections) — `src/hal0/registry/fileset.py`
- **fileset.py** (20 connections) — `src/hal0/registry/fileset.py`
- **_entry()** (16 connections) — `tests/registry/test_fileset.py`
- **HFUpstreamError** (12 connections) — `src/hal0/registry/fileset.py`
- **FilesetEmpty** (12 connections) — `src/hal0/registry/fileset.py`
- **FilesetVariantNotFound** (12 connections) — `src/hal0/registry/fileset.py`
- **TestPlanFilesetShardGrouping** (12 connections) — `tests/registry/test_fileset.py`
- **enumerate_repo()** (11 connections) — `src/hal0/registry/fileset.py`
- **test_fileset.py** (10 connections) — `tests/registry/test_fileset.py`
- **TestRoleOf** (10 connections) — `tests/registry/test_fileset.py`
- **TestMmprojTiebreak** (9 connections) — `tests/registry/test_fileset.py`
- **role_of()** (8 connections) — `src/hal0/registry/fileset.py`
- **TestRunnerHint** (8 connections) — `tests/registry/test_fileset.py`
- **TestEnumerateRepoPagination** (8 connections) — `tests/registry/test_fileset.py`
- **resolve_revision()** (7 connections) — `src/hal0/registry/fileset.py`
- **TestShardRe** (7 connections) — `tests/registry/test_fileset.py`
- **TestResolveRevision** (7 connections) — `tests/registry/test_fileset.py`
- **FilesetError** (6 connections) — `src/hal0/registry/fileset.py`
- **_next_link()** (4 connections) — `src/hal0/registry/fileset.py`
- **_row_to_entry()** (4 connections) — `src/hal0/registry/fileset.py`
- **_infer_runner_hint()** (4 connections) — `src/hal0/registry/fileset.py`
- **.test_requested_variant_not_found_raises()** (4 connections) — `tests/registry/test_fileset.py`
- **.test_empty_entries_raises_fileset_empty()** (4 connections) — `tests/registry/test_fileset.py`
- **.test_lexicographic_tiebreak_is_deterministic()** (4 connections) — `tests/registry/test_fileset.py`
- *... and 45 more nodes in this community*

## Relationships

- [SqliteModelRegistry](SqliteModelRegistry.md) (11 shared connections)
- [pull.py](pull.py.md) (5 shared connections)
- [detect.py](detect.py.md) (2 shared connections)
- [errors.py](errors.py.md) (1 shared connections)
- [Hal0Error](Hal0Error.md) (1 shared connections)
- [_Headers](_Headers.md) (1 shared connections)

## Source Files

- `src/hal0/registry/fileset.py`
- `tests/registry/test_fileset.py`

## Audit Trail

- EXTRACTED: 218 (65%)
- INFERRED: 119 (35%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*