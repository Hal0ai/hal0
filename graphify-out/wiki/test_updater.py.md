# test_updater.py

> 104 nodes

## Key Concepts

- **test_updater.py** (52 connections) — `tests/updater/test_updater.py`
- **Updater** (40 connections) — `src/hal0/updater/updater.py`
- **Path** (29 connections)
- **MonkeyPatch** (22 connections)
- **_write_release_manifest()** (19 connections) — `tests/updater/test_updater.py`
- **_build_release_tarball()** (17 connections) — `tests/updater/test_updater.py`
- **Any** (17 connections)
- **_versioned_install_dir()** (15 connections) — `src/hal0/updater/updater.py`
- **_current_symlink()** (15 connections) — `src/hal0/updater/updater.py`
- **test_apply_records_previous_for_rollback()** (11 connections) — `tests/updater/test_updater.py`
- **test_rollback_swaps_symlink_back()** (10 connections) — `tests/updater/test_updater.py`
- **test_rollback_repips_prior_tree_when_not_editable()** (10 connections) — `tests/updater/test_updater.py`
- **test_apply_repip_failure_rolls_back_symlink()** (9 connections) — `tests/updater/test_updater.py`
- **test_rollback_repip_failure_re_swaps_symlink_forward()** (9 connections) — `tests/updater/test_updater.py`
- **test_apply_repips_swapped_tree_when_not_editable()** (8 connections) — `tests/updater/test_updater.py`
- **test_apply_sha_mismatch_raises_typed_error()** (8 connections) — `tests/updater/test_updater.py`
- **synthetic_release()** (7 connections) — `tests/updater/test_updater.py`
- **test_check_does_not_recommend_revoked_latest()** (7 connections) — `tests/updater/test_updater.py`
- **test_check_recommends_non_revoked_newer_latest()** (7 connections) — `tests/updater/test_updater.py`
- **test_apply_happy_path_swaps_symlink()** (7 connections) — `tests/updater/test_updater.py`
- **test_prepare_stages_without_swap()** (7 connections) — `tests/updater/test_updater.py`
- **test_prepare_then_commit_swaps()** (7 connections) — `tests/updater/test_updater.py`
- **test_prepare_reads_release_notes()** (7 connections) — `tests/updater/test_updater.py`
- **test_cosign_failure_surfaces_typed_error()** (7 connections) — `tests/updater/test_updater.py`
- **test_check_uses_per_channel_url()** (7 connections) — `tests/updater/test_updater.py`
- *... and 79 more nodes in this community*

## Relationships

- [updater.py](updater.py.md) (43 shared connections)
- [ConfigParseError](ConfigParseError.md) (1 shared connections)
- [ContainerProvider](ContainerProvider.md) (1 shared connections)
- [test_registry_import.py](test_registry_import.py.md) (1 shared connections)

## Source Files

- `src/hal0/updater/updater.py`
- `tests/updater/test_updater.py`

## Audit Trail

- EXTRACTED: 410 (79%)
- INFERRED: 112 (21%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*