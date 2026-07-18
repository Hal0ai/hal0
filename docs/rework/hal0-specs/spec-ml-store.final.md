I have a complete map. Here is the implementation-ready spec.

---

# ML-2 (file-set pulling) + ML-3 (unified store) — implementation spec

Repo `/home/mint/hal0` @ `rework/descar`. READ-ONLY analysis; all file:line refs verified against code. Builds **on top of** the ML-1 SQLite schema (`model` / `model_file` / `model_backend`) — I note exactly where ML-2/ML-3 write into it.

## 0. Coordination with ML-1 (the schema I extend)

ML-1 (`spec-ml1-sqlite.raw`) lands `db/migrations/001_registry.sql` with three tables. The columns ML-2/ML-3 depend on already exist there:

- `model.revision TEXT` — resolved commit SHA (update-detect over the full set).
- `model.path NOT NULL` — entry point = shard-1 / `model.gguf`.
- `model_file(model_id, rel, dest, size_bytes, sha256, lfs, role, shard_index)`, `PRIMARY KEY(model_id, rel)`, `role ∈ model|shard|mmproj|tokenizer|config`, `shard_index` ordered (1 = entry point), `ON DELETE CASCADE`. **ML-1 imports this table EMPTY** ("write empty for pilot import"). **ML-2 is the first writer of real `model_file` rows.**
- `idx_model_file_role ON model_file(model_id, role)`.

ML-2/ML-3 add **migration `002_store.sql`** (below) for refcount/GC + a store-root sanity column. Do **not** edit `001`; forward-only per ML-1's `db/migrate.py`.

## 1. Current-state map (verified)

### The 🔴 dual store resolver (root of divergence)

Two functions independently resolve "the model root", with **different precedence and different defaults**:

| | `config/paths.py:143 model_store_root()` (READ/mount) | `registry/pull.py:224 _pull_root()` (WRITE) |
|---|---|---|
| src 1 | `HAL0_MODEL_STORE` env | — |
| src 2 | `load_hal0_config().models.store` (**raw `.store`**) | `cfg.models.effective_store()` = `store or pull_root` (`schema.py:2943`) |
| fallback | `DEFAULT_MODEL_STORE` = `/mnt/ai-models` (`paths.py:140`) | `paths.models_dir()` = `/var/lib/hal0/models` |

Divergence cases (write-here / mount-there → model-not-found at slot launch):
1. `store` set → both agree. ✓
2. `store` empty, `pull_root` set (PR#313 `--models-dir` install): **write=`pull_root`, mount=`/mnt/ai-models`.** ✗
3. config load throws (early bootstrap): **write=`/var/lib/hal0/models`, mount=`/mnt/ai-models`.** ✗

Second, narrower internal split: `_comfyui_models_dir` (`pull.py:262`) uses `paths.model_store_root()`, but flat/capability pulls use `_pull_root()` — comfyui assets and flat models can land under different roots.

Consumers of each:
- `model_store_root()`: `providers/container.py:739,747` (RO bind `Mount(store,store,ro,selinux="z")`), `providers/kokoro.py:168`, `providers/qwen3tts.py:190`, `pull.py:262` (comfyui).
- `_pull_root()`: `pull.py:245 _final_path`, `:290 _final_path_for_entry`, `:332 _tmp_dir`, `:409 _pull_jobs_dir`.

### Single-file pull engine (`registry/pull.py`, 1534 ln)
- `PullJob`/`PullFile` dataclasses (`:122`,`:152`) — `files: list[PullFile]` already models a multi-file set; top-level `bytes_*` are aggregates.
- `_download_one` (`:659`) — **fully reusable per-file core**: deterministic `.part`+resume sidecar (`_staging_paths`), Range/If-Range resume, 416 fallback, LFS `sha256` integrity (`expected_sha256` from `X-Linked-ETag`), disk preflight, atomic `os.replace`. Cleanup contract by exception type.
- `run_pull` (`:935`) — orchestrates **exactly 2 files**: `job.files=[main]`, optionally `+[mmproj]` when `mmproj_file` set (`:984-986`). Hardcoded `hf_download_url(...,"main")` (`:371,697`) — **no revision, no enum**.
- `_register_pulled` (`:1111`) — upserts one registry row; `metadata={sha256, pulled_at}`.

### Repo enumeration (`upstreams/huggingface.py:258 fetch_repo`)
- Fetches `…/tree/main` (`:270`) — **hardcoded main, NOT `?recursive=true`, NOT paginated** (HF tree caps at ~1000 entries + `X-Cache`/link cursor). Filters to `.gguf`/`.mmproj` only (`:341-348`), dropping shards/tokenizer/config. This is the Add-by-HF "Inspect" feeder; **the file-SET enumerator must supersede/extend it.**
- `update_check.py:41 _tree_url` DOES use `?recursive=true` — but still `main`, and only compares single `hf_filename` (`evaluate_model_update:139`), so **update-detect over a shard set is missing**.

### Discovery deletes shards (`registry/discover.py`)
- `_SHARD_RE = ^.+-\d{5}-of-\d{5}$` (`:84`); `_is_skippable` (`:147-160`) drops any shard, mmproj, hex-blob. `find_candidates` never emits a shard → sharded models are invisible to auto-scan and `scan_preview` (`models.py:501` reuses `is_skippable`). **ML-2(a) must group shards → one entry (shard-1) instead of dropping.**

### Path resolution / mounts
- `container.py:346 _resolve_model_path` — trusts `model_info["path"]` verbatim (no store-root assertion).
- `providers/base.py:27 Mount.render` (`:46-61`) — appends `:ro,z` from `selinux` field. `:z`/`:Z` **relabel fails on NFS** (chcon ENOTSUP) — the ML-3(e) target.

### Store migration (`registry/model_store.py`)
- `describe_store_state`/`plan_migration`/`execute_migration` — top-level-child `shutil.move`, skips dotfiles. No refcount/hardlink awareness.

### Delete = metadata-only
- `models.py:1403 delete_model` — **"The actual model file on disk is never touched"** (`:1420`). No file GC exists today. ML-3(c) adds guarded deletion.

### ModelRegistry interface (drop-in target, `store.py`)
`list/get/has/add/remove/update/route_for/reload` + `ModelNotFound`/`ModelAlreadyExists`. Callers: `api/deps.py`, `api/__init__.py:791`, routes `models/slots/stacks`, `capabilities/{catalog,orchestrator}`, `cli/{registry,capabilities,setup}_commands`, `dispatcher/router`, `providers/{container,flm}`, `registry/{discover,pull}`, `slots/manager`, `stacks/portable`, `updater/updater`. **ML-2/ML-3 must not change this interface**; new file-set methods are additive.

---

## PART (a) — file-SET pulling

### a1. New module `src/hal0/registry/fileset.py` (repo enumeration + planning)

Pure logic (no I/O side effects beyond HF GETs), unit-testable.

```
@dataclass FileSetEntry: rel, size_bytes, lfs_sha256|None, role, shard_index|None
@dataclass FileSetPlan:  repo, revision(sha), entry_rel, files:list[FileSetEntry],
                         mmproj_rel|None, total_bytes, runner_hint|None

async def enumerate_repo(repo, *, revision="main", token=None,
                         client=None) -> list[RawTreeEntry]
    # GET /api/models/{repo}/tree/{revision}?recursive=true, FOLLOW pagination:
    #   loop on Link: rel="next" header (HF pages ~1000 entries) OR ?cursor=.
    #   accumulate {path, size, lfs:{oid,size}, type}. fail-soft → raise HFUpstreamError.

async def resolve_revision(repo, ref="main", *, token, client) -> str
    # GET /api/models/{repo}/revision/{ref}  → sha  (pin the commit; §7.1c)
    # fallback: read `X-Repo-Commit` header off the tree response.

def plan_fileset(entries, *, requested_variant=None) -> FileSetPlan
    # 1. classify each rel via role_of(rel) below.
    # 2. group shards: SHARD_RE = re.compile(r'^(?P<stem>.+)-(?P<idx>\d{5})-of-(?P<tot>\d{5})\.(?P<ext>gguf|safetensors)$')
    #    - all shards sharing stem+tot = ONE model; shard_index=idx (1-based),
    #      entry = shard_index==1. role="shard".
    # 3. deterministic mmproj pairing (see a2).
    # 4. entry_rel: the single non-shard model file, or shard-1 of the chosen set.
    #    When requested_variant (a quant subdir / filename) given, restrict to it.
    # 5. total_bytes = sum(lfs.size or size).

def role_of(rel) -> str
    # basename+ext rules, reuse discover tokens:
    #   'mmproj' in name              -> "mmproj"
    #   SHARD_RE match                -> "shard"
    #   ext in {.gguf,.safetensors}   -> "model"
    #   name in {tokenizer.json,tokenizer.model,tokenizer_config.json} -> "tokenizer"
    #   name in {config.json,generation_config.json,*.jinja} -> "config"
    #   else                          -> "config"  (carried, low-priority)
```

**Shard grouping (replaces deletion).** `SHARD_RE` here is the *positive* twin of `discover._SHARD_RE`. Same regex, opposite verdict: discover drops, fileset groups. Extract the pattern to one place — see a4.

### a2. Deterministic mmproj pairing

Today mmproj pairing is directory-heuristic (`discover:251-255`) or body-supplied (`models.py:1682`). For file-sets make it deterministic in `plan_fileset`:
1. If body/curated supplies `mmproj_filename` → use that exact `rel`.
2. Else among `role=="mmproj"` entries, pick by **quant-affinity to the entry**: prefer the mmproj whose quant token (`detect.quant_from_filename`) matches the entry's; else the largest-precision mmproj (`F32 > F16 > Q8…`); ties → lexicographically first (stable). Record the tiebreak reason in `FileSetPlan` for the UI.
3. mmproj is a `model_file` row `role="mmproj"`, `shard_index=NULL`; it is **not** a routable `model` row (unchanged contract).

### a3. Multi-file download in `run_pull` (extend, don't fork)

`run_pull` (`pull.py:935`) currently builds `job.files` from `hf_file`+`mmproj_file`. Add a `fileset: FileSetPlan | None` param:

- When `fileset` given: `job.files = [PullFile(hf_filename=f.rel, kind=f.role) for f in fileset.files]` ordered shard_index then role. `base_done`/`base_total` accumulate across files exactly as the existing mmproj second-file loop (`:1023-1035`) already proves — **generalize that two-file loop into an N-file `for` loop**. Each file → `_download_one` (`:659`) unchanged (resume/integrity/atomic all reused per-file).
- `hf_download_url` (`:371`) gains a `revision` arg; pass `fileset.revision` (pinned sha) so a mid-pull upstream re-tag can't stitch mismatched shards. `_download_one`'s `If-Range` etag already guards per-file.
- final entry path = layout-derived (Part b): `<store>/models--<repo>/snapshots/<rev>/<rel>`. shard files land beside the entry; `job.path = <entry>`.
- After all files verify+install: `_register_pulled_fileset` writes the `model` row (path=entry, revision=sha) **and** the `model_file` rows (rel/dest/size/sha256/lfs/role/shard_index) + refcount bump (Part c).

Cancellation/resume: unchanged — a task cancel mid-set preserves each in-flight `.part`+sidecar (`_download_one:906-917`); the next `run_pull` resumes file-by-file (completed files already at `dest`, skipped when `dest` exists and sha matches).

### a4. Discovery stops deleting shards

`discover.py`: replace the "drop shard" behavior.
- Extract `SHARD_RE` to `registry/fileset.py` (single source); `discover` imports it.
- In `find_candidates` (`:194`): when a shard is seen, do **not** `continue`-drop. Instead bucket by `(dir, stem, tot)`; after the walk, emit **one** `CandidateModel` per bucket whose `path` = shard-1, `size_bytes` = Σ shard sizes, plus `metadata["shards"]` = ordered rel list. `_is_skippable` keeps dropping *lone* orphan shards only if the group is incomplete (missing shard-1).
- `scan_preview` (`models.py:501`) inherits this via `is_skippable`; add a `shards` field to the preview row so the UI shows "N-part model".
- `_commit_scan_rows` writes the grouped `model_file` rows for discovered sets.

### a5. Update-detect over the full set

Extend `update_check.py`:
- `evaluate_model_update` (`:106`): instead of one `hf_filename`, compare **every `model_file` row** for the model against the recursive tree's `{rel: lfs.oid}`. `update_available` = any file's local `sha256 != remote oid`, OR the set membership changed (file added/removed upstream), OR `model.revision != resolved head sha`. Return per-file deltas.
- `fetch_remote_lfs_shas` already uses `?recursive=true` — keep; add revision resolve so "new commit, same bytes" doesn't false-positive.

---

## PART (b) — unified store resolver + repo/revision layout

### b1. One resolver `src/hal0/config/store.py` (new) — kill the dual path

Single function, single precedence, used by **both** read and write:

```
def store_root() -> Path:
    # 1. HAL0_MODEL_STORE env
    # 2. load_hal0_config().models.effective_store()   # store or pull_root
    # 3. paths.models_dir()                             # /var/lib/hal0/models
    #   NOTE: default aligns READ+WRITE — no more /mnt/ai-models vs models_dir split.

def assert_under_store(p: Path) -> Path:
    # resolve, then require p is relative_to(store_root().resolve());
    # else raise StorePathEscape (fail-fast). Guards id/rel/revision injection.

def model_dir(repo, revision) -> Path:      # <store>/models--<org>--<repo>/snapshots/<rev>
def file_dest(repo, revision, rel) -> Path: # model_dir/rel, then assert_under_store
def entry_pointer(model_id) -> Path:        # <store>/by-id/<sane_id>  (stable symlink → entry file)
```

- `paths.model_store_root()` (`paths.py:143`) → **delegate to `store.store_root()`** (keep the name as a thin shim for existing callers; change its default+precedence to match). `pull._pull_root()` (`pull.py:224`) → **delete, replace call sites with `store.store_root()`**. `_comfyui_models_dir` (`pull.py:262`) → `store.store_root()/comfyui/models/<subdir>`. This collapses cases 2 & 3 above.
- Every write path calls `assert_under_store()` before `os.replace` (fail-fast on escape; complements `_sanitise_id`/`_SANITISE_RE` which only cleans the id segment).

### b2. Repo/revision-addressed layout (HF-cache-shaped)

`<store>/models--<org>--<repo>/snapshots/<rev>/<rel...>` (mirrors HF hub layout → `detect._hf_repo_name_from_path` at `detect.py:191` already parses `models--ORG--REPO`, and `discover` already skips `blobs/`/`.no_exist`). Path is **derived** from `(repo, rev, rel)` — never stored free-form, never trusted from input. The `model.path` column = the entry file's absolute dest; `model_file.dest` = each file's dest.

Back-compat: the legacy flat `<store>/<id>/<file>` and capability `<store>/<cap>/<id>/model.gguf` layouts (`pull._final_path_for_entry:265`) stay readable — `_register_pulled` already records absolute `path`, so old rows keep working. New pulls use the repo/rev layout; a `by-id/<id>` symlink (b1) gives slots a stable path across revision bumps (Part d).

---

## PART (c) — refcount + hardlink dedup + real GC

### c1. Migration `002_store.sql`

```sql
-- refcounted content-addressed blobs (dedup across models/revisions)
CREATE TABLE store_blob (
  sha256     TEXT PRIMARY KEY,     -- LFS oid / computed digest
  size_bytes INTEGER NOT NULL,
  blob_path  TEXT NOT NULL,        -- canonical on-disk file (hardlink target)
  refcount   INTEGER NOT NULL DEFAULT 0,
  created_at TEXT
);
CREATE INDEX idx_store_blob_refcount ON store_blob(refcount);
-- model_file already carries sha256; a file row referencing a blob bumps refcount.
```

`model_file.sha256` → `store_blob.sha256` is the ref edge (nullable for non-LFS files with no sha; those are not deduped, GC'd with their model dir).

### c2. Hardlink dedup on install

In the file-set install (a3), after `_download_one` verifies a file's `sha256`:
1. `SELECT blob_path FROM store_blob WHERE sha256=?`.
2. **Hit** and same-filesystem → `os.link(blob_path, dest)` (hardlink, no re-download; skip the stream entirely if the blob exists before download — check pre-fetch), `refcount += 1`.
3. **Miss** → the just-installed `dest` becomes the canonical `blob_path`; `INSERT store_blob(sha256,size,dest,1)`.
4. Cross-filesystem (dest under a different mount than blob store) → fall back to copy (no hardlink); do not refcount (log once). Hardlinks require same fs — the repo/rev layout keeps all model files under one `store_root`, so this is the norm.

Dedup wins: two models sharing a tokenizer/mmproj/identical quant shard store one copy. `pulled_at`/provenance stays per-`model_file`.

### c3. GC (orphan prune) + guarded delete

New `registry/gc.py`:
```
def collect_orphans(conn) -> list[str]        # blob_path WHERE refcount<=0
def prune_orphans(conn, *, dry_run) -> GCReport
def delete_model_files(conn, model_id) -> int # decrement refcount for each model_file
                                              # sha; unlink dest hardlink; when a blob
                                              # hits refcount 0, unlink blob_path.
                                              # ALWAYS assert_under_store() before unlink.
```

- `delete_model` (`models.py:1403`) gains `delete_files: bool = False` query param (default **false** — preserves today's "bytes never touched" contract as the safe default). When true → `delete_model_files` inside the same `record_action` audit block, after registry row removal, before `model.deleted` emit. CASCADE on `model_file` (ML-1 FK) drops the rows; GC decrements blobs first.
- Startup sweep (add beside `sweep_orphaned_partials` / `sweep_pull_jobs` in the lifespan, `api/__init__.py` ~`:791`): `prune_orphans(dry_run=False)` reaps blobs whose refcount fell to 0 via crashes. Fail-soft.
- Empty `snapshots/<rev>` dirs pruned after their last file unlinks.

---

## PART (d) — atomic model-dir / pointer swap

- **Per-file** atomicity already exists (`os.replace` in `_download_one:889`).
- **Per-set**: download all files into `<model_dir>/.staging-<jobid>/…`, then a single `os.replace(staging, snapshots/<rev>)` to publish the whole revision at once (same-fs rename = atomic dir swap). A partially-downloaded set is never visible under `snapshots/<rev>`. (`_tmp_dir` `pull.py:332` already keeps staging on the same fs — reuse under the model dir.)
- **Pointer swap for update-in-place**: `by-id/<id>` is a symlink → current entry file. On update pull, publish `snapshots/<newrev>/`, then `os.replace` a temp symlink over `by-id/<id>` (atomic). Slots resolve through `by-id/<id>`, so a swap re-points without editing slot TOMLs. Old revision dir is unref'd → GC. This replaces the current `dest_override` in-place overwrite (`pull.py:973-979`) which mutates bytes under a running slot.

## PART (e) — store permissions

Central helper `store.finalize_perms(path)` called after every install/link:
- Files `0644`, dirs `0755` + **setgid** (`02775` → new children inherit the group), owner `hal0`, group = the store group (installer's `hal0`/shared group; see MEMORY ai-models access notes — group must be the NFS-exported gid).
- `os.chown` best-effort (skip when not root / EPERM on NFS root-squash).
- **SELinux/NFS relabel fix** (`providers/base.py Mount.render:58`): `:z`/`:Z` relabel is unsupported on NFS (chcon ENOTSUP → container mount fails). Add `Mount.relabel_safe` logic:
  - detect the store fs type (statfs `f_type == NFS_SUPER_MAGIC 0x6969`, or `/proc/mounts` fstype ∈ {nfs,nfs4}).
  - NFS → **omit `:z`/`:Z`**, instead emit an SELinux `context=` mount option once at mount time (or rely on a host-level `chcon -R` skip). Set `Mount.selinux=""` for NFS sources so `render()` (`base.py:60`) drops the suffix.
  - local fs → keep `:z` (shared) as today.
- Container mounts (`container.py:747`, `kokoro.py:168`, `qwen3tts.py:190`) build their `Mount(store,...)` through a factory `store.mount_for(store_root, read_only=True)` that sets `selinux` conditionally. Single place, no per-provider drift.

---

## Files: add / touch

**Add**
- `src/hal0/config/store.py` — unified resolver, `assert_under_store`, layout derivers, `mount_for`, `finalize_perms`, `StorePathEscape`.
- `src/hal0/registry/fileset.py` — `enumerate_repo` (paginated+recursive), `resolve_revision`, `plan_fileset`, `role_of`, `SHARD_RE`, `FileSetPlan`/`FileSetEntry`.
- `src/hal0/registry/gc.py` — orphan collect/prune, `delete_model_files`.
- `src/hal0/db/migrations/002_store.sql` — `store_blob` + indexes.
- `src/hal0/db/repository.py` additions (from ML-1) — `insert_model_files`, `list_model_files`, `bump_blob`/`drop_blob_ref` (or a `registry/store_files.py` if ML-1's repo layer isn't merged yet).

**Touch**
- `registry/pull.py` — delete `_pull_root`; `hf_download_url(+revision)`; generalize `run_pull` two-file loop → N-file `fileset` loop; `_final_path*` → `store.file_dest`; `_tmp_dir`/`_pull_jobs_dir` → `store.store_root`; `_comfyui_models_dir` → `store.store_root`; add `_register_pulled_fileset` (writes `model_file` rows + `store_blob` refs + hardlink dedup + `finalize_perms`); staging-dir atomic set publish + `by-id` pointer swap.
- `upstreams/huggingface.py` — `fetch_repo` gains recursive+paginated tree + `revision`; stop pre-filtering to `.gguf`/`.mmproj` when feeding the file-set planner (keep the narrow filter only for the legacy Inspect variant dropdown, or route Inspect through `plan_fileset`).
- `registry/discover.py` — import shared `SHARD_RE`; group shards → one candidate + `metadata["shards"]`; keep dropping only incomplete groups.
- `registry/update_check.py` — set-wide `evaluate_model_update` + revision compare.
- `config/paths.py:143` — `model_store_root` delegates to `store.store_root` (aligned default).
- `providers/base.py` — `Mount` NFS-aware relabel (or leave `Mount` pure and decide `selinux` in `store.mount_for`).
- `providers/container.py:739-747`, `kokoro.py:168`, `qwen3tts.py:190` — build store mount via `store.mount_for`.
- `providers/container.py:346 _resolve_model_path` — resolve through `by-id` pointer + `assert_under_store` sanity (warn, don't hard-fail, to protect running slots).
- `api/routes/models.py` — pull route threads `FileSetPlan`; `scan_preview`/`_commit_scan_rows` shard rows; `delete_model` `delete_files` param.
- `api/__init__.py` (lifespan ~`:791`) — call `prune_orphans` in the startup sweep alongside `sweep_orphaned_partials`.
- `config/schema.py:2812 ModelsConfig` — doc `store` as the single source; mark `pull_root` fully deprecated (still read via `effective_store`).

**Stays behind the interface:** `ModelRegistry` public API (`store.py`) unchanged — file-set methods are additive on the repository/db layer, callers untouched.

## Test impact
- `tests/registry/test_pull*.py` — multi-file/shard pull, revision pin, resume mid-set, hardlink dedup (assert second model with shared blob does `os.link` not re-download), integrity-fail keeps set unpublished. Existing single-file + mmproj tests must still pass (N-file loop is a superset of the 2-file path).
- New `tests/registry/test_fileset.py` — `plan_fileset` shard grouping, mmproj tiebreak determinism, pagination (mock Link header), `role_of`.
- New `tests/config/test_store.py` — resolver precedence table (env/store/pull_root/fallback all agree read==write), `assert_under_store` escape, NFS relabel omission (mock statfs).
- New `tests/registry/test_gc.py` — refcount inc/dec, orphan prune, `delete_files` guard, `assert_under_store` before unlink.
- `tests/registry/test_discover*.py` — sharded model now surfaces one candidate (was: skipped). **This flips existing assertions** — call out as an intentional behavior change.
- `tests/providers/test_container*.py` — mount render `:z` present on local, absent on NFS.
- Pin the ml1↔ml2 seam: a test that ML-2 writes `model_file` rows ML-1 imported empty.

## Risks
- **Behavior flip**: discovery now registers sharded models — could surface previously-hidden repos as routable; llama-server needs shard-1 + siblings present (it auto-loads `-of-` siblings). Gate routing on complete set.
- **Hardlink cross-fs**: silently degrades to copy (no dedup) if staging and blob store split mounts — the unified layout must keep both under one `store_root`; assert same `st_dev`.
- **Permission chown on NFS root-squash**: `chown` to `hal0:group` fails under root_squash; must be best-effort + rely on setgid inheritance + correct export gid (see MEMORY: ai-models/manage-gids gotcha).
- **GC data loss**: `delete_files=true` + a refcount bug could unlink a blob still referenced. Mitigate: default false, `assert_under_store` before every unlink, dry-run in the sweep first, never delete outside `store_root`.
- **Legacy path readers**: rows with old flat/capability `path` must keep resolving — do not rewrite them on migration; only new pulls use repo/rev layout.
- **HF pagination**: unbounded repos (100s of shards) — cap enum + stream-plan; the tree fetch must not OOM.

## Sibling-lane interfaces
- **ML-1 (SQLite registry)**: ML-2 is the first writer of `model_file`; ML-3 adds `002_store.sql`. If ML-1's `db/repository.py` isn't merged, ML-2/3 stand up a minimal `db/connection.py` shim (WAL, `foreign_keys=ON`, `busy_timeout`) per ML-1's design and hand it over. **Blocking dependency: the `model_file`/`model.revision` columns.**
- **P3-slots container path**: `_resolve_model_path` (`container.py:346`) + the RO store `Mount` (`:747`) — ML-3 changes the *source* of the store root (unified resolver) and the mount relabel, and introduces the `by-id/<id>` pointer slots should resolve through. Coordinate so slot config stores the pointer, not the revision-pinned path (survives update swaps).
- **ML-runner (`preferred_runner`/`RUNNER_IMAGES`)**: **not present in code yet** (grep empty) — forward reference from plan §7.1b + ML-1 schema (`model.preferred_runner` column exists in the DDL). ML-2's `plan_fileset` can emit a `runner_hint` (from repo arch/quant) that the ML-runner lane consumes; leave the column populated-when-known, else NULL.