# test_store_golden.py

> 39 nodes · cohesion 0.09

## Key Concepts

- **test_store_golden.py** (17 connections) — `tests/registry/test_store_golden.py`
- **_pull_fileset()** (17 connections) — `tests/registry/test_store_golden.py`
- **_body()** (10 connections) — `tests/registry/test_store_golden.py`
- **.test_single_file_pull_writes_row_blob_and_pointer()** (8 connections) — `tests/registry/test_store_golden.py`
- **_blob()** (7 connections) — `tests/registry/test_store_golden.py`
- **.test_orphan_pruned_live_retained_missing_bytes_tolerated()** (7 connections) — `tests/registry/test_store_golden.py`
- **.test_delete_one_of_two_keeps_bytes_delete_last_removes_bytes()** (7 connections) — `tests/registry/test_store_golden.py`
- **_model_files()** (6 connections) — `tests/registry/test_store_golden.py`
- **.test_identical_blob_across_two_models_is_one_inode_refcount_two()** (6 connections) — `tests/registry/test_store_golden.py`
- **TestGcReconcilesDbVsFilesystem** (6 connections) — `tests/registry/test_store_golden.py`
- **.test_all_shards_and_mmproj_recorded_with_roles()** (6 connections) — `tests/registry/test_store_golden.py`
- **TestNfsRelabelOmission** (6 connections) — `tests/registry/test_store_golden.py`
- **.test_written_dest_is_under_the_read_resolver_root()** (6 connections) — `tests/registry/test_store_golden.py`
- **Path** (5 connections)
- **.test_reconcile_reaps_bare_bytes_retains_live_skips_partial()** (5 connections) — `tests/registry/test_store_golden.py`
- **TestReadWritePrecedenceParity** (5 connections) — `tests/registry/test_store_golden.py`
- **.test_repull_new_revision_flips_pointer_and_keeps_old_bytes()** (5 connections) — `tests/registry/test_store_golden.py`
- **_entry()** (4 connections) — `tests/registry/test_store_golden.py`
- **_mock_client()** (4 connections) — `tests/registry/test_store_golden.py`
- **registry()** (4 connections) — `tests/registry/test_store_golden.py`
- **TestContentAddressedDedup** (4 connections) — `tests/registry/test_store_golden.py`
- **TestMultiShardMmprojFileset** (4 connections) — `tests/registry/test_store_golden.py`
- **TestPullPersistsRegistryAndFileset** (4 connections) — `tests/registry/test_store_golden.py`
- **TestRefcountSafeDelete** (4 connections) — `tests/registry/test_store_golden.py`
- **TestRevisionUpdatePointerFlip** (4 connections) — `tests/registry/test_store_golden.py`
- *... and 14 more nodes in this community*

## Relationships

- [SqliteModelRegistry](SqliteModelRegistry.md) (20 shared connections)
- [plan_fileset](plan_fileset.md) (11 shared connections)
- [connect](connect.md) (8 shared connections)
- [run_pull](run_pull.md) (2 shared connections)
- [tx](tx.md) (1 shared connections)

## Source Files

- `tests/registry/test_store_golden.py`

## Audit Trail

- EXTRACTED: 154 (85%)
- INFERRED: 28 (15%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*