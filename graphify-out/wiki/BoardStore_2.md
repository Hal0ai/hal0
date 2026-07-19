# BoardStore

> God node · 114 connections · `src/hal0/board/store.py`

**Community:** [BoardStore](BoardStore.md)

## Connections by Relation

### calls
- _ensure_store() `INFERRED`
- test_import_partial_board_fetch_failure() `EXTRACTED`
- test_import_does_not_rerun_when_not_empty() `EXTRACTED`
- test_import_from_hermes_present() `EXTRACTED`
- test_import_unreachable_falls_back_to_empty() `EXTRACTED`

### contains
- [store.py](store.py.md) `EXTRACTED`

### inherits
- [_SpyStore](_SpyStore.md) `EXTRACTED`

### method
- ._write() `EXTRACTED`
- ._read() `EXTRACTED`
- ._append_event() `EXTRACTED`
- .update_task() `EXTRACTED`
- .get_task() `EXTRACTED`
- .comment_task() `EXTRACTED`
- .create_task() `EXTRACTED`
- ._resolve_board() `EXTRACTED`
- .add_link() `EXTRACTED`
- .bulk_update() `EXTRACTED`
- .delete_task() `EXTRACTED`
- ._seed() `EXTRACTED`
- ._serialize_card() `EXTRACTED`
- ._touch() `EXTRACTED`
- ._build_patch() `EXTRACTED`
- .ensure_initialized() `EXTRACTED`
- ._fetch_hermes_snapshot() `EXTRACTED`
- .get_board() `EXTRACTED`
- .create_board() `EXTRACTED`
- ._dispatch_writeback() `EXTRACTED`

### rationale_for
- SQLite repository for the Operator Board.      The documented interface is exact `EXTRACTED`

### references
- _store() `EXTRACTED`
- _make() `EXTRACTED`
- _store() `EXTRACTED`
- _app_with_store() `EXTRACTED`
- app_client() `EXTRACTED`
- _store() `EXTRACTED`
- _build_app() `EXTRACTED`
- client() `EXTRACTED`
- test_delete_matching_if_match_ok() `EXTRACTED`
- test_delete_stale_if_match_conflicts() `EXTRACTED`
- test_get_task_emits_etag() `EXTRACTED`
- test_if_match_weak_and_star_tolerated() `EXTRACTED`
- test_patch_emits_new_etag() `EXTRACTED`
- test_patch_matching_if_match_ok() `EXTRACTED`
- test_patch_stale_if_match_conflicts() `EXTRACTED`
- test_patch_without_if_match_always_applies() `EXTRACTED`
- test_bulk_audits_and_updates() `EXTRACTED`
- test_comment_audits() `EXTRACTED`
- test_delete_task_and_audits() `EXTRACTED`
- test_links_add_remove_audit() `EXTRACTED`

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*