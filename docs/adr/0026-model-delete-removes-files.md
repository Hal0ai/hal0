# ADR-0026: Deleting a model removes its files by default, behind guards

## Status

**ACCEPTED (2026-10-11).** Owner decision of 2026-10-11: deleting a model
deletes its files, with an opt-out.

## Context

Operators deleted models in the dashboard and saw them come back.

- `DELETE /api/models/{id}` removed only the registry row. Its docstring
  said "the actual model file on disk is never touched", and
  `cascade_delete_model` (`src/hal0/services/models_service.py`) called
  only `registry.remove()`.
- `[models].auto_scan_on_start` is `true` by default, so the next API
  restart ran `scan_and_register` (`src/hal0/registry/discover.py`),
  found the file that was still there, and registered it again.
- The Delete dialog said "This removes <size> from /var/lib/hal0/models".
  That was false twice over: nothing was removed, and most models live
  under another root.
- The startup scan ran without pruning, so a row whose file the operator
  had removed by hand stayed in the registry for good.
- The scanner skipped a file only if its *name* started with a dot, so
  model files under `<root>/.Trash-1000/files/` were registered.
- A Hugging Face shard set was named after its resolved blob path, so it
  showed up as a model with a hash for an id, next to a hardlinked copy of
  the same bytes registered under its real name.

`hal0.registry.gc.delete_model_files` already existed as an opt-in
"remove the bytes" path, but no caller used it.

## Decision

1. **Delete removes files by default.** `DELETE /api/models/{id}` takes
   `delete_files` (default `true`). `hal0 model rm` calls the same route and
   takes `--keep-files` to opt out. The dashboard dialog shows the model's
   real path and size and has an "Also delete files from disk" checkbox,
   ticked by default. The MCP `model_delete` tool gets the same parameter.
   It stays gated and cannot be loosened by persona policy
   (`POLICY_NO_LOOSEN`).

2. **One implementation.** `cascade_delete_model` captures the model's
   file set before the row is removed. The file set is the row's `path`,
   `metadata["shards"]`, the `mmproj` sidecar, and every `model_file`
   row's `dest`. The `model_file` rows cascade away with the row, which is
   why the set is captured first. The order is: unload referencing slots,
   clear slot defaults, remove the row, then call
   `delete_model_files_on_disk`. A file failure never fails the request and
   never undoes the row delete. It is reported instead.

3. **Guards.** A file is kept, and listed in `files_kept` with a reason,
   when:
   - it is not strictly inside a deletion root (`outside_roots`). The
     deletion roots are `ModelsConfig.scan_roots()` plus the resolved store
     root. A root itself is never removed.
   - it is under hal0's own config or state tree (`/etc/hal0`,
     `/var/lib/hal0`) but outside the model store (`hal0_state`). This
     covers an operator who configures a very broad root such as `/`.
   - another remaining registry row references it (`shared:<id>`). This
     compares resolved paths across `path`, shards, `mmproj` and
     `model_file.dest`. A file below a remaining row whose path is a
     directory (an FLM or ONNX model directory) also counts as shared.
   - the path is a directory (`directory`). Nothing is ever removed
     recursively.
   - the row belongs to FLM (`runtime_managed`). FLM manages its model
     directory itself.
   - the OS refuses the unlink (`unlink_failed:<error>`).
   - the file is not there at delete time (`missing_at_delete`). Examples
     are an unmounted share or a dangling symlink target. The row is still
     removed, and the path is tombstoned so the file does not come back
     when the storage returns.

   For a symlink, such as an HF `snapshots/…` link to `blobs/<sha>`, both
   the link and its resolved target are removed, and each is checked
   against the guards. A file with other hardlinks is removed, but its
   bytes are left out of `bytes_freed` and a warning says no space was
   freed. Directories left empty are removed up to, but not including,
   the deepest root that holds the file. With overlapping roots such as
   `/` and `/mnt/models`, the walk stops at `/mnt/models`. A remaining
   row's model directory is never removed. `store_blob` refcounts are released through
   `hal0.registry.gc.release_blob_ref`, the logic `delete_model_files`
   already used.

4. **Tombstones.** Every file left on disk is tombstoned, whether the
   operator opted out or a guard other than `shared:` refused it. A
   tombstone is a resolved path in `<var_lib>/registry/ignored-paths.json`
   (`hal0.registry.tombstones`). Writes are atomic. Every
   read-modify-write holds an in-process lock plus an `flock` on
   `ignored-paths.json.lock`, so overlapping deletes, in the API or from a
   CLI process, cannot drop each other's entries.
   `find_candidates` skips a tombstoned path and anything below it, so the
   model does not come back. An explicit registration clears tombstones at
   or below its path. Explicit registrations are `hal0 model add` /
   `POST /api/models/add-from-path`, scan commit, and pulls (single-file,
   file-set and FLM). A file kept as `shared:` is not tombstoned, because
   it still belongs to a live row.

   The store is a JSON file, not a table. It is a short list of strings
   that only the scanner reads, and it must outlive the row it came from.
   CONTRIBUTING rule 7 ("SQLite for machine state") was weighed against
   adding a migration for this list. If tombstones ever need querying or
   listing in the UI, they should move to a table.

5. **The startup scan prunes ghost rows.** The startup auto-scan runs with
   `prune=True`. These rows are protected:
   - every model id that a slot, a stack, or a `hal0.toml` field
     references (`_collect_referenced_model_ids`).
   - every model id with a persisted queued or running pull snapshot.
     `_auto_resume_interrupted_pulls` needs that row later in boot, and
     its file has not landed yet.

   If any reference source fails to load, the scan skips pruning for that
   boot and logs `models.auto_scan_prune_skipped`. `prune_missing` never
   drops FLM rows. It also trusts a missing file only when the scan root
   holding it exists and is non-empty, or, for a row outside every root,
   when the file's parent directory still exists. An unmounted NFS share is
   an empty mount point, so rows under it are reported as
   `missing_unverified` and kept. An empty root that is itself a mount
   point (`os.path.ismount`) counts as live, because the share is mounted
   and just holds nothing. The trade-off is an ordinary local root that
   became empty when its last model file was removed by hand. That root is
   empty and not a mount point, so its ghost row is kept until the root
   holds something again or the row is deleted explicitly. Telling that
   case apart from an unmounted share would need fstab or mount-table
   parsing, which isn't worth it for one leftover row.

   When `prune_missing` removes a row, it releases the row's `store_blob`
   references with the same `release_blob_ref` logic an explicit delete
   uses. Without that, a pruned pulled model's blob would stay referenced
   and `prune_orphans` could never reclaim it. This covers both the startup
   prune and a manual `{"prune": true}` scan.

6. **Scanner fixes.** The scanner does not descend into any directory below
   a root whose name starts with `.`. It walks with `os.walk` and prunes
   those directories in place, so a large `.Trash-1000/` costs neither
   stats nor the 30-second scan budget. Only directories below the root are
   pruned, so a root under a dot directory still scans. A shard set is named after its walked
   shard-1 path, not the resolved blob. A file whose inode matches an
   already registered file (a hardlinked copy) is not a new candidate. The
   HF `blobs/` directory was already skipped when walked directly. Blobs
   only entered the registry through resolved shard paths, which the
   naming and inode checks now cover.

## Consequences

- Deleting a model frees its disk space. The response reports
  `files_removed`, `files_kept`, `bytes_freed`, `tombstoned` and
  `warnings`, next to the existing `id`, `deleted` and `affected_slots`.
  The audit record carries the same file outcome.
- A model deleted with "keep files" stays gone across restarts and scans
  until the operator registers its path again.
- Any script that called `DELETE /api/models/{id}` expecting a
  registry-only delete now removes files. Pass `delete_files=false` to keep
  the old behaviour. The CHANGELOG lists this under Breaking.
- Removing an operator-placed file under a scan root is now the default.
  The guards keep deletes inside the roots, away from files another model
  uses, and away from directories.

## Known limitations

- **A surviving hardlink can be rediscovered.** When a deleted model's file
  has another hardlink inside a scan root, the delete unlinks only the
  registered path and warns that no space was freed. The scanner's inode
  check hides the second name only while the original row exists. Once
  the row is gone, the next scan registers the surviving link as a new
  model. Tombstoning by inode, or finding and removing every link, was
  left out. The first misfires once the inode is reused, and the second
  needs a full-tree walk on every delete. To remove the surviving link,
  delete the rediscovered model too.
