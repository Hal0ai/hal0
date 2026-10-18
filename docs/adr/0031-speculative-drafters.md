# ADR-0031: Speculative-decoding drafters are a model file fact, paired like the projector, switched on the profile

## Status

Accepted (2026-10-18, #2765). It amends ADR-0029 decisions 1 and 2 (one more file
fact on the model, one profile tune renamed, one more model-layer flag) and
supersedes ADR-0025 §5, which planned `CuratedModel.draft_file` and was never
built. It lands after the runner pin moves to llama.cpp b11716
(`hal0-runner-images` #37, gated and promoted on 2026-10-18): LiLiCorr
drafters need #30506; DFlash, EAGLE3 and classic drafters also work on
b11596. One PR series on top of that pin, not two.

## Context

llama.cpp speeds up decoding by letting a small **drafter** propose several
tokens that the main model verifies in one pass. The output is unchanged;
only the speed is. Four kinds of drafter exist at b11716, and vendors now
ship them beside the model:

| `--spec-type` | needs | recognised by | example |
|---|---|---|---|
| `draft-dflash` | a drafter GGUF trained for one target (DFlash, DFlash2, DSpark, LiLiCorr are all this type) | `general.architecture = dflash`; DSpark has tensor `markov_w1.weight`; DFlash2 has `dflash.selector_top_k > 0`; LiLiCorr has `lilicorr.candidate_topk > 0` | `nvidia/…-Bolt-74B-A9B-24GB-LiLiCorr-GGUF`, `z-lab/Qwen3.8-27B-DFlash2` |
| `draft-eagle3` | a drafter GGUF trained for one target | `general.architecture = eagle3` | `…/Qwen3-8B-eagle3` |
| `draft-mtp` | nothing when the target carries NextN heads; else an `mtp-*.gguf` sidecar | sidecar has the target's architecture and only `blk.N.nextn.*` tensors | `unsloth/Qwen3.8-Flash-Next-GGUF` `MTP/mtp-*.gguf` |
| `draft-simple` | any small model with the same tokenizer | a full LLM GGUF | Qwen3-0.6B drafting Qwen3-32B |
| `ngram-*` | nothing | — | `--spec-default` |

Two llama.cpp facts shape everything below (`common/arg.cpp:538-573`,
`common/speculative.cpp:2396-2434`, verified at b11716):

- `--spec-draft-model` alone never speculates. Without `--spec-type` the
  server loads the file and, for a classic drafter, does nothing with it.
  hal0 must always emit the type.
- No header key names a drafter's target. A target-specific drafter only
  carries geometry (`dflash.embedding_length`, `dflash.target_layers`,
  `eagle3.target_hidden_size`, `eagle3.target_layers`); a mismatch is a
  tensor-shape crash at load, not a message. A classic drafter must pass
  `common_speculative_are_compatible` (same vocab type, same BOS/EOS, vocab
  size within 128, identical token strings).

hal0 today drafts only with MTP heads embedded in the main file: the model
fact `mtp` (ADR-0029 §1), the profile tune `mtp = auto | off`, the
`MTP_DRAFT_BUNDLE` in the profile layer and `--spec-draft-device` in the slot
layer (`config/schema.py:758`, `providers/container.py:1078-1091`).
`--spec-draft-model` and `--spec-draft-hf` are host-owned
(`slots/argv.py:375-378`), so no profile can name a drafter file, and nothing
in hal0 does either. The Add-from-HF inspect already lists drafter files it
sees in a repo (`upstreams/huggingface.py` `drafts`) and the dialog prints
them as "not pulled". NVIDIA's Bolt recipe pairs the model, its projector and
a LiLiCorr drafter from a second repo.

## Decision

### 1. Ownership: three new rows in the ADR-0029 table

| Field | Owner | Notes |
|---|---|---|
| `draft` (absolute path or null) | **Model** | file fact, like `mmproj`: the drafter file paired with this model. One drafter per model. |
| `draft_kind` (`dflash` \| `eagle3` \| `mtp` \| `simple`, or null) | **Model** | file fact read from the drafter's header when it is paired; it is what `--spec-type` is derived from. Written by hal0, never by hand. `dflash` covers DFlash, DFlash2, DSpark and LiLiCorr; the sub-variant is recorded in `metadata.draft` (below) for display and runner gating. |
| `speculation` (`auto` \| `off` \| `ngram`) | **Profile** | **replaces `mtp`**. `auto` drafts with the model's drafter when it has one and the runner can run it, else with MTP heads when the file has them and the runner supports MTP, else not at all. `off` never drafts. `ngram` runs llama.cpp's model-free n-gram speculation (`--spec-type ngram-mod`) and ignores any drafter. |

`mtp` on the model keeps its ADR-0029 meaning: the file has NextN heads. A
model with heads and a `draft` of kind `mtp` means the heads ship as a
sidecar (the unsloth layout); a model with heads and a `dflash` drafter uses
the drafter, because a trained drafter is faster than MTP heads on every
upstream measurement and the operator paired it on purpose. The precedence
is fixed, not configurable: `draft` > embedded MTP > nothing.

`metadata.draft` on the model row records what hal0 read from the drafter
header at pairing time, so launch and UI never re-open the file:
`{"variant": "lilicorr" | "dflash2" | "dspark" | "dflash" | "eagle3" | "mtp" | "simple", "size_bytes", "sha256", "block_size", "embedding_length", "target_layers", "vocab_size", "base_model"}`,
each key present only when the header carried it.

### 2. The fit check: a drafter is refused before it is written or downloaded

`screen_draft_fit(model_header, draft_header)` in `services/models_service.py`
decides, from the two GGUF headers alone:

| drafter | accepted when |
|---|---|
| `dflash` | `dflash.embedding_length == <arch>.embedding_length` of the target, and every entry of `dflash.target_layers` is below the target's `<arch>.block_count` |
| `eagle3` | `eagle3.target_hidden_size == <arch>.embedding_length`, same `vocab_size`, and every `eagle3.target_layers` entry is below `block_count` |
| `mtp` | same `general.architecture` as the target, `<arch>.nextn_predict_layers >= 1`, and the target's `mtp` fact is not `false` |
| `simple` | same `tokenizer.ggml.model`, same `bos_token_id` and `eos_token_id`, `abs(vocab_size delta) <= 128` (the first part of llama.cpp's own check; the token-string comparison needs the vocab block and is left to llama.cpp) |
| anything else | refused: `model.draft_not_a_drafter` (the file is a plain model whose tokenizer does not match, or an mmproj, or not a GGUF) |

Refusals on a write are 422 `model.draft_mismatch` with the two values that
differ in `details`: `PUT /api/models/{id}` and `POST /api/models/{id}/pull`
refuse; `POST /api/models/inspect-draft` only reports the verdict (a 200 with
`fit.ok` false and the reasons). The check runs in three places, exactly where `screen_vision_mmproj`
runs today: `PUT /api/models/{id}` with `draft` in the body (headers read from
disk), `POST /api/models/{id}/pull` with `draft_filename` (both headers read
from Hugging Face by HTTP range before any download starts), and the discover scan when it pairs a file it found
beside a model (a file that fails the check is listed as a separate candidate,
never paired, and a `dflash`/`eagle3`-architecture file is never registered as
a model on its own).

`general.base_model.*` is not used for the verdict (NVIDIA's LiLiCorr file has
none) but is shown in the dialog when present.

**Reading a header from the Hub.** This is new plumbing: `gguf_header.py`
parses a local file through an mmap window. The same parser gains a
bytes-source variant fed by `GET …/resolve/<rev>/<file>` with a `Range`
header, following the LFS redirect, with the same `HF_TOKEN` handling as the
pull, reading in 4 MB steps until the KV block is complete or 16 MB has been
read (Bolt's header is complete within 14 MB; a classic drafter's tokenizer
vocab is the largest case). Two files per check, so a fit check costs tens of
MB against a download of tens of GB. A 416 or a non-200 ends the read and the
dialog reports "could not read the header" rather than guessing; the pull is
then refused until the operator unticks the drafter.

### 3. Launch: the drafter is a model-layer flag, the tune is a profile-layer bundle

ADR-0029 §2 gains these lines, in the same four layers:

2. **model**: when speculation is in effect through a drafter,
   `--spec-type <type> --spec-draft-model <draft>` where `<type>` is
   `draft-<draft_kind>`. When it is in effect through embedded MTP heads,
   `--spec-type draft-mtp` (today this sits in the profile bundle; it moves
   here because it is a file fact).
3. **profile**: the bundle for the kind in effect, then `flags`:
   - `DRAFT_BUNDLE["dflash"]`: `--spec-draft-ngl all --spec-draft-n-max <block_size-1, capped at 8>`
     (`block_size` from `metadata.draft`; llama.cpp clamps there anyway),
     `--spec-draft-type-k q8_0 --spec-draft-type-v q8_0`.
   - `DRAFT_BUNDLE["eagle3"]`: `--spec-draft-ngl all --spec-draft-n-max 4`, same KV types.
   - `DRAFT_BUNDLE["simple"]`: `--spec-draft-ngl all --spec-draft-n-max 8 --spec-draft-n-min 0 --spec-draft-p-min 0.75`, same KV types.
   - `DRAFT_BUNDLE["mtp"]`: the existing `MTP_DRAFT_BUNDLE` minus its `--spec-type` token.
   - `speculation = "ngram"`: `--spec-type ngram-mod --spec-ngram-size-n 24 --spec-ngram-size-m 48 --spec-ngram-n-max 64` (llama.cpp's `--spec-default`).
   The values are starting points taken from the vendor recipes and upstream
   defaults; `--spec-draft-threads*` and `--spec-draft-poll*` from
   `MTP_DRAFT_BUNDLE` apply to every kind. A profile's own `--spec-draft-*`
   flags follow the bundle and win, as today.
4. **slot**: `--spec-draft-device <device>` whenever any draft type is in
   effect (today only for MTP).

Flag ownership (`slots/argv.py`): `--spec-type` joins `MODEL_FACT_FLAGS`
(hal0 derives it from the model's drafter or heads; a profile that wants
n-gram speculation sets `speculation = "ngram"`, not the flag).
`--spec-draft-model` and `--spec-draft-hf` stay in `HOST_OWNED_FLAGS`. The
profile write screen and the launch re-screen refuse `--spec-type` in
`flags` the way they refuse `--mmproj`, with the message naming the
`speculation` field. `--spec-type` appends in llama.cpp rather than
replacing, so a second value from any layer would have produced a mixed
type; one owner closes that.

`RunnerSupports` gains `draft_kinds: frozenset[str]` (today's `mtp: bool`
stays). It is static per runner key, like every other `RunnerSupports`
field: `standard` and `cpu` carry `{"simple", "mtp", "dflash", "eagle3", "lilicorr"}`;
`cuda` carries the empty set and keeps `mtp=False` until its lane has a gate
(§8); the non-llama runners carry the empty set. One table,
`DRAFT_KIND_MIN_BUILD = {"lilicorr": 11669}`, names the llama.cpp build a
kind first works on; at launch hal0 parses the build from the slot's
resolved image tag (`b<N>-r<sha>` for the images this repo builds) and drops
a kind whose minimum the image does not meet, so an operator-pinned older
image gets the amber "unavailable" state (§5) instead of a tensor-shape
crash. A tag that carries no build number is taken as the pin. The
effective decision mirrors `_effective_mtp`:

```
speculation == "off"  -> none
speculation == "ngram" -> ngram
draft set and draft_kind in runner.draft_kinds
   and (variant != "lilicorr" or "lilicorr" in runner.draft_kinds) -> draft
mtp heads and runner.supports.mtp -> mtp
else -> none, and when a draft was set: log `speculation.draft_unsupported`
   and expose it on the slot (section 5) as "unavailable on this runner"
```

Embedding and reranking mode never drafts (unchanged). A projector and a
drafter load together (NVIDIA's recipe; verified in `server-context.cpp`).

### 4. Pairing a drafter: pull, scan, edit

**Inspect.** `POST /api/models/inspect` returns `drafts` as
`[{id, repo, kind_hint, variant_hint, size_bytes, size, source}]`, sorted best
first, where:

- `source = "repo"`: a file in the inspected repo whose name matches
  llama.cpp's own sidecar keywords plus the vendor patterns:
  `mtp-`, `dflash-`, `dflash2`, `dspark-`, `eagle3`, `lilicorr`, `speculator`, `draft`.
- `source = "companion"`: a file from a sibling repo found by one Hub search
  per inspect (`author=<org>&search=<name>`): same author, name = the
  inspected repo's name (minus a trailing `-GGUF`) plus one of `-LiLiCorr`,
  `-DFlash`, `-DFlash2`, `-DSpark`, `-EAGLE3`, `-eagle3`, `-speculator`,
  `-draft`, optionally `-GGUF`. Companion repos are listed the first time,
  then cached with the inspect entry. Cross-author drafters (z-lab's
  `DFlash-<name>` for a Qwen model) are out of scope for this release; the
  manual field covers them.
- `source = "manual"`: whatever the operator pastes into the dialog's
  "Drafter repo" field, inspected the same way.

`kind_hint`/`variant_hint` come from the filename; the real kind and the fit
verdict come from the header, which the dialog fetches (through
`POST /api/models/inspect-draft {hf_repo, hf_filename, draft_repo, draft_filename}`,
where `hf_*` name the target and `draft_*` the drafter)
only for the drafter actually selected, so changing the quant re-checks one
pair of headers, not every candidate. Best-first order:
`lilicorr > dflash2 > dspark > dflash > eagle3 > mtp > simple`, larger
precision first within a variant.

**Pull.** The pull body accepts `draft_filename` and optional `draft_repo`
(defaults to `hf_repo`). `run_pull` adds a `PullFile(kind="draft")` after the
projector; the file lands in the model's directory under its upstream
basename, like the projector; registration writes `draft`, `draft_kind`,
`metadata.draft` (from the downloaded header), a `model_file` row with
`role = "draft"`, and adds the bytes to `size_bytes` (every installed byte,
as `capacity.py` already documents). The curated catalogue gains
`CuratedModel.draft_file` and `draft_repo` (ADR-0025 §5, now built) with the
same resolution order as `mmproj_file`. A pull body without `draft_filename`
leaves an existing pairing alone, as a pull without `mmproj_filename` does.

**Scan.** `discover.find_candidates` pairs a drafter the way it pairs a
projector: same directory, name or header says drafter, fit check passes.
`hal0 model add --draft <path>` and `PUT /api/models/{id} {"draft": path}`
take a host path and run the same check with `require_on_disk`.

**Unpair.** `PUT {"draft": null}` clears `draft`, `draft_kind` and
`metadata.draft` and keeps the file; `DELETE /api/models/{id}` removes the
drafter with the model's other files (ADR-0026 adds `draft` to the file set it
removes and tombstones); `DELETE /api/models/{id}/draft` removes only the
drafter file and clears the fact. An explicit unpair (`PUT {"draft": null}`)
tombstones the drafter path (ADR-0026's rule for a removed projector), so a
later scan does not pair it again; pairing the path lifts the tombstone.

### 5. What the operator sees

**Add from Hugging Face.** After Inspect, under the quant list and the
projector row, a **Drafter** row: "found in this repo" / "found in
`<org>/<repo>`" / "none found — paste a repo", a select pre-set to the best
candidate with "None" available, the variant and size, and the fit verdict
once the header is read ("matches: hidden 5120, 6 target layers, block 8" or
the refusal text). The Pull button's size and the downloads row include it
(`· drafter` in the per-file label, from the `files` list the pulls API
already carries). A drafter the runner on this box cannot run is still
offered, with an amber note naming the runner build it needs.

**Model drawer.** Beside MMProj, a **Drafter** field: a RichSelect over the
repo's `drafts` (plus the stored value), a path input when the repo lists
none, a Pull action when the chosen file is not on disk, the variant/size/fit
band underneath. A "Measure" button arrives with the bench pair (the §6
follow-up). The facts band shows the variant.

**Profile editor.** The MTP select becomes **Speculation**:
`auto | off | ngram`, with the same inline description style as Vision.

**Slot drawer.** The resolved-argv view already shows the flags. A new
"Speculation" line states what is in effect (`LiLiCorr drafter`, `MTP heads`,
`n-gram`, `off`, or `drafter unavailable on this runner`) with the live
counters from section 6.

**Slots list row and model picker.** The picker rows (slot drawer, inference
pane) show an eye for vision and a bolt for a paired drafter beside the name,
and a compact fit chip (icon + estimated GB; that chip change ships ahead of
this work as its own PR, `fix/picker-chips`). The Slots list row shows one bolt glyph after the model name
(operator's pick, 2026-10-18): lit (accent) while the slot drafts, grey when
a drafter is paired but the profile has speculation off, amber when the
drafter is paired but the running runner cannot use it. Its hover title
names the drafter and the live acceptance rate. The slot drawer carries the
full numbers.

**Catalog.** A bolt chip on model rows that carry a drafter; the filter chips
gain "drafter".

**CLI and MCP.** `hal0 model pull <id> --draft <file> [--draft-repo <repo>]`,
`hal0 model add --draft <path>`, `hal0 model set <id> --draft <path|none>`,
`hal0 profile create|edit --speculation auto|off|ngram`; MCP `model_pull`
(`draft_filename`, `draft_repo`), `model_update` (`draft`), `profile_*`
(`speculation`), `model_inspect` (returns `drafts` with sources).

### 6. Knowing whether it helps

llama-server exposes the counters (verified at b11716): per completion,
`timings.draft_n` and `timings.draft_n_accepted`; on `/metrics`,
`llamacpp:spec_decode_num_draft_tokens_total`,
`…_num_accepted_tokens_total`, `…_num_drafts_total` and the per-position
counter; on `/slots`, `speculative: true|false`.

- `slots/metrics_collect.py` scrapes the three totals and reports on the slot
  `metrics.spec = {active, kind, variant, draft_tokens, accepted_tokens, drafts, acceptance, mean_accepted_len}`
  where `acceptance = accepted/draft` and `mean_accepted_len = 1 + accepted/drafts`.
  `active` is `/slots[].speculative`, which is also the guard against the
  "loaded but never drafting" trap.
- The dispatcher records `draft_n`/`draft_n_accepted` from proxied completions
  into the same rolling window that feeds `local_tps`, for a live rate.
- A speed-up is only ever shown when measured: the bench planner's cell
  identity already carries the profile tunes (`planner.py` `spec`). The
  planner gains one override, `tune_overrides={"speculation": "off"}`,
  applied on top of the slot's real profile for one cell, so "Measure"
  enqueues (through the existing `hal0-benchctl` queue) two cells that
  differ only in that override; the drawer shows `tok/s with / without` and
  the ratio from the results. Nothing estimates a multiplier from the
  acceptance rate. This is the last part to land and may ship in a
  follow-up PR; everything else in this ADR stands without it. Shipped state:
  the bench pair is in that follow-up (it needs a harness capability that does
  not exist yet), so the model drawer has no Measure button.

### 7. The conversion (ADR-0029 §7 pattern, one minor release)

- Registry: migration `013_model_draft.sql` adds `draft TEXT`, `draft_kind TEXT`.
  `model_file.role` gains the value `draft` (the column is free text).
- Profiles: a one-shot converter in the ADR-0029 §7 chain rewrites
  `profiles.toml` on upgrade, with the usual `.bak-<stamp>` copy: `mtp = "off"`
  becomes `speculation = "off"`, `mtp = "auto"` or unset becomes nothing
  (auto is the default). `ProfileConfig` keeps `extra="forbid"`, so after the
  rewrite the API and CLI refuse `mtp` with a 422 that names `speculation`;
  there is no alias period (the operator's standing rule: one converter, no
  shims). The converter's shape detection learns `speculation` as a
  new-shape key. Consequence for rollback: a `hal0 update --rollback` to
  1.4.x quarantines every profile that carries `speculation` into
  `profiles.invalid`. The backup is `profiles.toml.bak-<stamp>` beside the file;
  the "Rolling back past the speculation rename" section of the update and
  rollback guide tells the operator to copy it back before `hal0 update
  --rollback` to 1.4.x.
  Seeds set neither key.
- `MTP_DRAFT_BUNDLE` loses its `--spec-type draft-mtp` token (now emitted in
  the model layer); the frozen converter keeps the old bundle for the
  profile-centric migration it replays.
- The gate (`hal0-runner-images` `gates/standard.toml`) gains a probe that
  launches the Qwen3.8-27B gate model with its DFlash2 drafter and asserts
  `/slots[].speculative == true` and a non-zero accepted count.

### 8. Out of scope

Training or converting drafters; more than one drafter per model; tuning the
bundles beyond the vendor starting points (that is a bench follow-up, like
#799 was for MTP); drafters for non-llama runners (flm, kokoro, moonshine,
qwen3-tts, comfyui); EAGLE3 header detection beyond `general.architecture`
(llama.cpp has none either); the `cuda` lane's gate.

## Options considered

- **The drafter as its own registry model, linked by id.** A classic
  drafter is a real model, so this is tempting. Rejected: three of the four
  kinds are not models and would appear in every picker; the delete, scan
  and store accounting would need a second ownership path; and the projector
  precedent (a path on the row) already does what is needed. A classic
  drafter that is also registered as a model keeps both roles.
- **Keep `mtp` on the profile and add a second toggle for drafters.**
  Rejected: two switches for one decision, and `mtp = off` would be read as
  "don't use the DFlash drafter" by half the readers.
- **Let the profile carry `--spec-draft-model`.** Rejected: it is a host
  file path, which no profile may name (ADR-0029 §2), and a profile is
  shared across models.
- **Auto-pair a drafter from any repo without showing it.** Rejected: the
  dialog shows every file it pulls (the opt-out requirement), and the Hub
  search is a heuristic.

## Consequences

- Any llama model can be paired with the drafter its vendor ships, in one
  dialog, with the fit checked before the download.
- One switch on the profile covers every speculation kind; `mtp` disappears
  from the profile API after a one-release alias.
- The launch gains one model-layer flag pair and one bundle per kind; the
  argv golden tests gain one case per kind.
- A drafter that the running runner cannot use is visible as such on the
  slot, instead of a silent fallback.
- The gate exercises speculation on every runner bump, so a llama.cpp change
  that breaks drafting is caught before a pin.

## References

- llama.cpp b11716: `common/arg.cpp:538-573` (auto-detect precedence),
  `common/speculative.cpp:72-84` (type names), `:106-171` (classic
  compatibility), `:2396-2434` (header detection),
  `tools/server/server-context.cpp:733-765` (`/slots`), `:4762-4768`
  (metrics); PRs #30506 (LiLiCorr), #27342 (DFlash2), #25173 (DSpark),
  #18039 (EAGLE3), #30459 (`--spec-draft-n-ubatch`).
- NVIDIA model card `nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-GGUF`
  (recipe: `--mmproj … --spec-type draft-dflash --spec-draft-model …-LiLiCorr.gguf --spec-draft-n-max 6`).
- ADR-0025 §5, ADR-0026, ADR-0029 §1, §2, §7; `registry/model.py`,
  `services/models_service.py` (`screen_vision_mmproj`),
  `providers/container.py` (`_llama_argv_segments`, `_effective_mtp`),
  `slots/argv.py` (`MODEL_FACT_FLAGS`, `HOST_OWNED_FLAGS`),
  `config/schema.py` (`MTP_DRAFT_BUNDLE`, `ProfileConfig`),
  `upstreams/huggingface.py` (`group_quant_variants`), `registry/pull.py`,
  `registry/discover.py`, `slots/metrics_collect.py`, `bench/planner.py`.
