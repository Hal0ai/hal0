# Split/multi-file GGUF audit (issue #1256)

This document catalogs every place in the registry code that assumes a model
is exactly one file, so a future implementer can add first-class support for
split GGUFs (`model-00001-of-00003.gguf`, produced by `gguf-split` and
increasingly common for large HF uploads). It is a research artifact, not a
design doc — no source files were changed to produce it. All line numbers
were verified against the current tree on `claude/hal0-bones-09-issues`
(2026-07-12); the issue's own line refs (`pull.py:935`, `discover.py:84`/`158`,
`models.py:503`) are all still accurate as of this audit.

## Lifecycle stages

| Stage | Split-GGUF today | Why |
|---|---|---|
| **Pull** | No | `run_pull` (`pull.py:935`) seeds `job.files` with exactly one `PullFile(kind="model")` plus an optional `mmproj` sidecar (`pull.py:984-986`). There is no shard enumeration — pulling a sharded HF repo downloads whichever single filename the caller passed, which is unloadable alone. |
| **Scan / Discover** | No | `_SHARD_RE` (`discover.py:84`) matches any `-NNNNN-of-NNNNN` stem and `_is_skippable` (`discover.py:158`) drops every match unconditionally — first shard included. `hal0 model scan` and `POST /api/models/scan/preview` (`models.py:503`) both route through this, so a split GGUF set never surfaces as a candidate, complete or not. |
| **Metadata** | No | Neither `gguf_header.py` nor `detect.py` reads or reasons about the GGUF `split.count` / `split.no` / `split.tensors.count` KV keys. A hand-registered first shard reports the tensor count, size, and any derived params of shard 1 only — the header parser has no concept of "this file is 1 of N." |
| **Register** | Partial (unsafe) | `POST /api/models` (`models.py:897-926`) and `POST /api/models/add-from-path` (`models.py:743-894`) perform **no shard check at all** — they'll happily register a bare first-shard path with `size_bytes` = that one file's `stat()`. This is the "current workaround" the issue documents: it works but under-reports size and carries no shard awareness for downstream consumers (FLM/NPU routing, `model rm`, dashboard display). |
| **Run** | **Yes — already works** | The slot manager resolves a single `Model.path` string and passes it straight through as `--model <path>` (`providers/container.py:594-595`, fed from `model_path=str(model_info.get("path") or "")` at `providers/container.py:1608`). llama.cpp itself auto-discovers `-0000N-of-` siblings in the same directory from the first shard's filename, so a registry row that happens to point at shard 1 of a complete, correctly-named set loads correctly today. No change needed here. |

## Assumption locations

### `src/hal0/registry/model.py`
- `model.py:89-95` — `path: str = Field(...)` on `Model`. The registry's data model has exactly one path per model, singular, not a list. This is the root single-file assumption everything else inherits: any split-aware design either keeps `path` pointing at shard 1 (cheapest — matches how the run path already works) or needs a new `shards: int` / `paths: list[str]` field plus a migration.

### `src/hal0/registry/pull.py`
- `pull.py:122-149` — `PullFile` dataclass. `kind: str = "model"  # "model" | "mmproj"` (line 133) is the only two kinds today. Docstring (120-130) states explicitly: "A plain pull has exactly one entry (the main GGUF); a vision pull adds an `mmproj` sidecar entry." No shard kind exists yet, though the issue proposes `kind="shard"`.
- `pull.py:152-176` — `PullJob.files: list[PullFile]`. Comment: "Per-file manifest (multi-file pulls, e.g. main GGUF + mmproj)." The list shape already supports N entries mechanically; nothing in `run_pull` populates more than 2.
- `pull.py:935-987` (`run_pull` signature + manifest seed) — takes a single `hf_file: str` and optional `mmproj_file: str | None`; no `shard_files` / repo-listing parameter. Line 984: `job.files = [PullFile(hf_filename=hf_file, kind="model")]`; line 985-986 appends at most one `mmproj` entry. A split-aware version needs a third branch here that appends one `PullFile(kind="shard")` per sibling shard filename.
- `pull.py:1001-1017` — main-file download block downloads exactly `job.files[0]`; `pull.py:1022-1035` downloads `job.files[1]` (mmproj) if present. Both are hardcoded by list index, not a loop — adding shards requires generalizing this to iterate `job.files[i]` for `i >= (2 if mmproj_file else 1)`.
- `pull.py:243-291` (`_final_path` / `_final_path_for_entry`) — resolves one destination filename per model id; no shard-index-aware naming, but since shard filenames already carry their own `-NNNNN-of-NNNNN` suffix from HF, this mostly needs to be called per-shard rather than restructured.
- `pull.py:1039-1050` (`_register_pulled` call site) — registers with a single `size_bytes=size_bytes` computed as `main_rec.bytes_done` (line 1017), i.e. shard-1-only size even if shards were downloaded alongside it in a hypothetical partial implementation. Needs the aggregate-sum fix described in the proposed sketch.

### `src/hal0/registry/discover.py`
- `discover.py:80-84` — 
  ```python
  # HF Transformers multi-file shard pattern (e.g. model-00001-of-00003.safetensors).
  # These need the transformers library to stitch back together; hal0's
  # llama-server / FLM providers expect a single-file GGUF or single
  # .safetensors checkpoint, so a lone shard isn't loadable on its own.
  _SHARD_RE = re.compile(r"^.+-\d{5}-of-\d{5}$")
  ```
  The comment's premise ("a lone shard isn't loadable on its own") is true but overbroad: it's used to reject *every* shard, not just incomplete sets — including a complete, correctly-ordered set whose first member llama.cpp can load standalone.
- `discover.py:147-160` (`_is_skippable`) — line 158: `if _SHARD_RE.match(p.stem): return True`. Unconditional skip, no sibling-completeness check. A split-aware version needs to detect "is this `-00001-of-000NN`, and are shards `2..N` present in the same directory" before deciding to skip vs. surface-as-one-candidate.
- `discover.py:166-256` (`find_candidates`) — the walk that calls `_is_skippable` per file; this is where a shard-set detection pass (grouping siblings by directory + base name before the stat/curated-match logic) would need to live, since today each file is evaluated independently with no cross-file state except the existing `mmproj_by_dir` association pattern (lines 180-183, 207-217, 251-255) — which is a workable precedent to copy for shard grouping.
- `discover.py:405-409` — public `is_skippable()` wrapper re-exported for `models.py`'s scan-preview route; any fix to `_is_skippable`'s shard logic automatically propagates here.

### `src/hal0/registry/gguf_header.py`
- `gguf_header.py:85-96` — `_INTERESTING_KEYS_STATIC` frozenset, the fixed list of KV keys the parser extracts (`general.architecture`, `general.embedding_length`, `general.name`, `general.basename`, `general.size_label`, `general.file_type`). It does **not** include `split.count`, `split.no`, or `split.tensors.count` — the GGUF spec's split-file KV trio. The parser has no split awareness at all; adding these three keys to the static set is the entire fix needed here (the existing single-pass KV scan at `gguf_header.py:275-295` already handles arbitrary static keys generically, so no structural change is needed, just three more dict entries).
- `gguf_header.py:205-222` (`read_gguf_header` docstring) — enumerates exactly the four keys it extracts; needs updating once split keys are added.

### `src/hal0/registry/detect.py`
- Whole file has zero references to `split`, shard, or multi-file anything. `detect()` (`detect.py:327-405`) calls `read_gguf_header` and reads `general.architecture` / `pooling_type` / `general.file_type` but never checks for `split.count`/`split.no`. A hand-registered or scanned first shard runs through here unchanged from a full single-file GGUF — `detect()` has no way to know it's looking at a fragment, so `confidence="high"` is reported on data that's numerically wrong for aggregate size (size comes from the caller's `stat()`, not from `detect()` itself, but nothing here flags the discrepancy either).

### `src/hal0/api/routes/models.py`
- `models.py:443-569` (`POST /api/models/scan/preview`, `scan_preview`) — line 503: `if is_skippable(p): continue` inside the directory-walk branch (lines 495-514). This is the exact call the issue references at `~503`; verified current. Same shard-rejection behavior as `discover.py`, applied to the HF-cache / arbitrary-path preview flow rather than the configured-roots auto-scan.
- `models.py:520-524` — deliberate *non*-application of `is_skippable` to the *resolved* path (only the symlink name is checked) so HF-cache blob symlinks survive; this same resolved-path handling would need to carry through any shard-set-completeness check added to `_is_skippable`/`is_skippable`.
- `models.py:743-894` (`POST /api/models/add-from-path`) — no shard reference anywhere in this handler. `size_bytes = resolved.stat().st_size` (line 848) is single-file only; `detect(resolved)` (line 823) inherits `detect.py`'s blindness. This is the "current workaround #2" path the issue describes (`hal0 model register <id> --path .../<name>-00001-of-0000N.gguf`) — it succeeds today with silently wrong size metadata.
- `models.py:897-942` (`POST /api/models`, `create_model`) — raw `Model(**body)` passthrough, no validation beyond pydantic's field types. Whatever `size_bytes`/`path` the caller supplies is trusted as-is; this is the second workaround path and the natural home for a future "does this path look like an incomplete/lone shard, warn or reject" guardrail if one is wanted at the API boundary (independent of the scan-side fix).

### `src/hal0/providers/container.py` (slot manager's launch-plan builder)
- `container.py:561-603` (`_llama_argv_segments`) — line 594-595: `if model_path: base += ["--model", model_path]`. Single string, single `--model` flag — confirmed this is the actual `-m`/`--model` argv construction site (not `slots/manager.py` itself, which only orchestrates state and calls into this module).
- `container.py:1587-1608` — `model_path=str(model_info.get("path") or "")`, i.e. resolves from the registry row's single `path` field. **This is the one stage that already works for split GGUFs**: as long as `Model.path` points at a well-formed `*-00001-of-000NN.gguf` and the remaining shards sit alongside it, llama.cpp's own loader stitches the set together — hal0 doesn't need to enumerate shards to launch a slot, only to register/scan/report on them correctly.

## Proposed implementation sketch

Issue #1256 proposes four changes; tying each to the functions found above:

1. **Pull — shard enumeration.** Extend `run_pull` (`pull.py:935`) to accept a shard-file list (or detect the `-00001-of-` pattern in `hf_file` and derive sibling filenames), append one `PullFile(kind="shard")` per shard to `job.files` (generalizing the current hardcoded 2-slot manifest at `pull.py:984-1035`), and download each through the existing per-file `_download_one` (`pull.py:659-932`) — that function is already file-agnostic and needs no change. Register the row's `path` pointing at shard 1 (matches what the run path already expects); `size_bytes` becomes the sum across `job.files` (mirroring the existing `job.bytes_downloaded = sum(f.bytes_done for f in job.files)` aggregation already done for the job-level total at `pull.py:1068-1070` — the same pattern, just also written into the registry's `size_bytes` field in `_register_pulled`, `pull.py:1111-1180`).

2. **Scan/discover — exemption for complete sets.** Change `_is_skippable` (`discover.py:147-160`, specifically the shard check at line 158) to: match `-00001-of-000NN`, then check the same directory for shards `2..N` (`_SHARD_RE`'s pattern gives you `N` directly); skip only if the set is incomplete or if the file is shard `2..N` of an otherwise-complete set. Surface exactly one `CandidateModel` for a complete set. This can reuse the same directory-keyed grouping technique `find_candidates` (`discover.py:166-256`) already uses for mmproj sidecar association (`mmproj_by_dir`, lines 180-183 / 207-217 / 251-255) — a `shard_groups_by_dir` dict populated during the same walk.

3. **Metadata — `split.count` sum.** Add `split.count`, `split.no`, `split.tensors.count` to `_INTERESTING_KEYS_STATIC` (`gguf_header.py:85-96`) — no other change needed in the parser, since the KV loop is already generic over the static-key set. `detect.py`'s `detect()` (`detect.py:327-405`) can then read `header.get("split.count")` and, when present, the caller (scan/register path) sums sibling shard file sizes into `size_bytes` and stamps a `shards: N` marker into `Model.metadata` (a free-form dict already, `model.py` — no schema migration needed) so the dashboard can display it.

4. **Guardrails.** `model rm` (registry `remove()`, `store.py:404-419`) operates on a single `path` per row and has no shard awareness — removing a split-GGUF row today deletes only the registry entry, not the on-disk shard files (this is true for single-file models too, so it's not a regression, but a shard set left with N-1 orphaned files on disk is a bigger footgun than one orphaned file). A shard-aware `model rm` would need to read the new `shards: N` metadata and unlink the sibling files, or at minimum warn. FLM/NPU capability routing (`pull.py:1183-1421`, `run_flm_pull` and the FLM provider dispatch) has no path-shape check at all today; rejecting a `shards > 1` row before handing it to FLM (which has no split-GGUF concept) needs a check added wherever FLM capability eligibility is currently decided.

## Out of scope / already works

The **run path is not part of this audit's problem set.** `providers/container.py:594-595` / `:1608` pass a single `--model <path>` to llama-server exactly as they do for single-file GGUFs, and llama.cpp's own loader auto-discovers the `-0000N-of-` siblings from the first shard's filename and directory. A registry row whose `path` already points at a correctly-named, complete shard-1 file loads and serves today with zero code changes — confirmed by tracing the same `model_info.get("path")` → `--model` flow used for every other model kind. The gap is entirely upstream of Run: nothing in Pull, Scan/Discover, or Metadata will get a registry row into that state safely today.
