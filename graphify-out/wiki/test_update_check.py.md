# test_update_check.py

> 36 nodes

## Key Concepts

- **test_update_check.py** (15 connections) — `tests/registry/test_update_check.py`
- **evaluate_model_update()** (12 connections) — `src/hal0/registry/update_check.py`
- **_model()** (10 connections) — `tests/registry/test_update_check.py`
- **fetch_remote_lfs_shas()** (7 connections) — `src/hal0/registry/update_check.py`
- **_tree_transport()** (6 connections) — `tests/registry/test_update_check.py`
- **test_run_pull_dest_override_replaces_in_place()** (6 connections) — `tests/registry/test_update_check.py`
- **update_check.py** (4 connections) — `src/hal0/registry/update_check.py`
- **test_evaluate_none_for_rows_without_hf_coords()** (4 connections) — `tests/registry/test_update_check.py`
- **test_evaluate_repo_unreachable_never_flags_update()** (4 connections) — `tests/registry/test_update_check.py`
- **test_evaluate_missing_remote_file_never_flags_update()** (4 connections) — `tests/registry/test_update_check.py`
- **test_evaluate_no_local_sha_never_flags_update()** (4 connections) — `tests/registry/test_update_check.py`
- **test_evaluate_basename_fallback_resolves_subdir_hosted_file()** (4 connections) — `tests/registry/test_update_check.py`
- **test_evaluate_basename_fallback_skips_ambiguous_match()** (4 connections) — `tests/registry/test_update_check.py`
- **MockTransport** (3 connections)
- **test_evaluate_update_available_when_shas_differ()** (3 connections) — `tests/registry/test_update_check.py`
- **test_evaluate_up_to_date_when_shas_match_case_insensitively()** (3 connections) — `tests/registry/test_update_check.py`
- **test_fetch_surfaces_lfs_oids_and_skips_non_lfs()** (3 connections) — `tests/registry/test_update_check.py`
- **test_fetch_maps_failed_repos_to_none_without_raising()** (3 connections) — `tests/registry/test_update_check.py`
- **test_fetch_sends_bearer_token_when_provided()** (3 connections) — `tests/registry/test_update_check.py`
- **Any** (2 connections)
- **_tree_url()** (1 connections) — `src/hal0/registry/update_check.py`
- **AsyncClient** (1 connections)
- **Any** (1 connections)
- **HF update detection for pulled models.  A registry row pulled from HuggingFace r** (1 connections) — `src/hal0/registry/update_check.py`
- **Fetch the current LFS sha256 per file for each repo.      Returns ``{repo: {path** (1 connections) — `src/hal0/registry/update_check.py`
- *... and 11 more nodes in this community*

## Relationships

- [models.py](models.py.md) (2 shared connections)
- [run_pull](run_pull.md) (2 shared connections)

## Source Files

- `src/hal0/registry/update_check.py`
- `tests/registry/test_update_check.py`

## Audit Trail

- EXTRACTED: 94 (78%)
- INFERRED: 26 (22%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*