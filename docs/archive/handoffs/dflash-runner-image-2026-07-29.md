# DFlash runner image — integration handoff (2026-07-29)

Source: issue Hal0ai/hal0#1349 (field report from a Strix Halo box) and
charlie12345/rocmfp4-llama#16 (two latent fork bugs + DFlash graft).

## What this is

A new llama-server runner image with **DFlash speculative decoding** grafted onto
the ROCmFP4/STRIX_LEAN fork family, validated by the reporter on gfx1151:

- Qwen3.6-35B-A3B STRIX_LEAN: 63 → **96 tok/s** reasoning (same binary, FP4 + DFlash).
- Cross-model draft (Qwen3.6 draft on Ornith-35B, Qwen3.5-MoE lineage):
  **112.5 tok/s at 98% acceptance**.
- Includes two latent fork bug fixes independent of DFlash
  (`llm_graph_result::reset()` never nulled `t_h_pre_norm`; `encode()` sized
  encoder batches with `n_embd_inp()` instead of `n_embd_inp_enc()`).

## Key lineage finding (changes the integration plan)

`charlie12345/rocmfp4-llama` is a **different, newer lineage** than
`charlie12345/ROCmFPX` (the parent of `Hal0ai/Hal0_ROCmFPX`, which produced the
current default image `ghcr.io/hal0ai/hal0-rocmfpx:c077206`):

- `git merge-base` between the two: **no common ancestor**.
- The old ROCmFPX lineage is ~9300 commits behind the rocmfp4-llama lineage.
- Consequence: the graft **cannot be merged into `Hal0_ROCmFPX`**. The new image
  builds from the rocmfp4-llama lineage directly. This likely also clears the
  lxc105 finding (old fork rejects newer GGUF arch versions) — but that cuts both
  ways: every currently-served GGUF must be re-tested on the new lineage.

## What was done

1. **Reviewed** `gsrunion/rocmfp4-llama@dflash-graft` — 5 commits:
   4 verbatim cherry-picks of merged upstream llama.cpp PRs
   (#22105, #25110, #25246, #25823 — all confirmed real and merged upstream)
   plus one port-fix commit `5c493ec38` (~200 lines: the two bug fixes, fork API
   renames (`pre_norm` vs upstream `nextn`, 2-arg `accept()`), `n_embd_inp_enc()`
   hparam, `ctx_other` for DFlash drafts in server, qwen35moe `t_layer_inp`
   exposure, `llama_mul_mat_hadamard` alias). Verdict: clean, well-scoped, no
   hygiene issues. Two root-level validation scripts (`validate.sh`,
   `phase4-ornith.sh`) are author dev-debris, localhost-only; drop them if the
   branch is ever rebased for upstreaming into charlie's repo.
2. **Forked** `charlie12345/rocmfp4-llama` → **`Hal0ai/rocmfp4-llama`** and pushed
   the graft as branch **`integrate/dflash-graft`** (tip `5c493ec38`).
3. **Built and pushed** `ghcr.io/hal0ai/hal0-rocmfpx:dflash-5c493ec` on the Halo
   box (10.0.1.142, ssh alias `hal0`). Followed the established hal0-rocmfpx
   pattern rather than the upstream `.devops` Dockerfile: compile in
   `localhost/hal0-rocmfpx-builder:7.2.4` (matching ROCm to the runtime base),
   then layer the binaries onto
   `ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server`, including
   the same stale-binary hygiene fix the c077206 image carries. Push rc=0.
   Build script `/root/dflash_build.sh`, logs under `/root/rocmfp4-build/dflash/`.
   `llama-server --help` confirms `--spec-type … draft-dflash` is present.
4. **Validated** on the same box (see Results below).

## Platform bug (real, fixed on the Proxmox host 2026-07-30)

The first round's ~4x-low numbers **were** a genuine GPU clock fault, confirmed and
fixed on the pve host by a follow-up investigation. On kernel 7.0.6-2-pve +
smu_v14 (Strix Halo APU) the **`auto` DPM governor is broken**: under 90% GPU load
sclk stayed at 600 MHz / mclk 400 MHz / ~16 W. Not thermal, not a power cap, not a
BIOS pin. Setting `power_dpm_force_performance_level=high` gives 2900 MHz /
1000 MHz / 91 W and **3.6x** decode. Toggling high→auto does *not* repair auto.
Fix persisted as `amdgpu-perf-high.service` on the host; idle cost ~18 W vs 9 W.
**Recheck clocks under load after any pve kernel upgrade** — silent regression risk.

Note the sysfs control is read-only from inside this LXC, so this must be set on the
host, not on 10.0.1.142.

Sequence note for anyone reading the raw logs: the 15:07 round ran clock-locked; the
20:57 and later rounds ran at full clocks (control re-measure of the plain 35B gave
52.63 t/s at 22:00 and 57.69 t/s at 00:47, versus 13.4 t/s clock-locked). Everything
from 20:57 onward is valid; the 15:07 round is not.

## VOID: first measurement round (15:07–16:00) — do not cite

The first round ran minutes after a 28-core compile finished and every number is
roughly 4x low. Proven by re-running one cell on an idle box: gemma-4-12B Q4_K_M
measured **5.10 t/s** at 15:16 and **20.40 t/s** at 20:50 — same image, same
model, same flags. Anything derived from that round is void, including a
confidently-wrong "the GPU is clock-locked, fix it on the Proxmox host"
diagnosis that was in this document. Cause was transient post-compile system
state, not platform configuration. `power_dpm_force_performance_level` is `auto`,
which is correct and was never the problem; the level-0 clock readings taken then
were idle-state samples, and the low throughput had a different, transient cause.

**Lesson for future runs: never bench within ~30 min of a heavy compile on this
box, and sanity-check one known cell against a prior number before trusting a
round.** Only the two non-performance gates from that round survive, since they
are pass/fail rather than timed: GGUF compatibility (5/5 models load, including
both ROCmFP4-STRIX_LEAN) and MTP still functioning (coherent output, 93/132
acceptance).

## Results (2026-07-29 20:57–21:03, idle box)

`/root/dflash_retest.sh`. Tier-A `llama-bench`, `-ngl 99 -fa 1 -mmp 0`, r=5.

| model | old (c077206) | new (dflash) | delta |
|---|---|---|---|
| Qwen3.5-0.8B UD-Q4_K_XL | 153.12 ± 2.88 | 124.09 ± 6.72 | **-19.0%** |
| gemma-4-12B Q4_K_M | 20.51 ± 0.13 | 19.81 ± 0.11 | -3.4% |

- **Gate 1 — GGUF compatibility: PASS** (from the first round; pass/fail, unaffected).
- **Gate 2 — plain decode: FAIL, and worse than first thought.** The small model
  regresses **-19%**, not the -7.6% the degraded round suggested. The 12B is -3.4%.
  The pattern — large penalty on a small model, small penalty on a big one — points
  at per-token overhead in the new lineage rather than reduced kernel throughput.
- **Gate 3 — MTP still works: PASS** (from the first round).
- **Gate 4 — DFlash uplift: FAIL as configured.** Measured on the reporter's own
  target file so there is no model-pairing excuse:

| config (reporter's Qwen3.6-35B-A3B STRIX_LEAN) | decode t/s | acceptance | their number |
|---|---|---|---|
| `llama-bench` tg128 | 52.63 ± 0.65 | — | — |
| server, plain | 47.28 | — | 63.1 |
| server + DFlash draft, two-file | **32.34** | **78.5%** (226/288) | 96.0 @ 97–98% |

  DFlash costs us **-32%** where it gained them +52%. The discriminator is
  **acceptance: 78.5% here vs their 97–98%.** At ~78% the draft-plus-verify
  overhead exceeds the win; near 98% it pays for itself several times over. Our
  plain baseline now roughly reconciles with theirs (52.6 `llama-bench` / 47.3
  server-timing vs their 63.1 server-timing on a reasoning workload), so the box
  is no longer suspect — the acceptance gap is the whole story.

  Likely causes of the acceptance gap, in order of suspicion: (a) draft-parameter
  flags — we guessed `--spec-draft-n-max 4 --spec-draft-p-min 0.0`, and DFlash
  block size is not the same knob as MTP's; (b) draft provenance — we used
  `Alittlehammmer`'s GGUF conversion of `z-lab/Qwen3.6-35B-A3B-DFlash`, they may
  have converted themselves; (c) prompt/workload differences. All three are the
  outstanding #1349 ask.

## Tuning matrix + architecture finding (2026-07-30, clocks fixed)

### A bug we found and fixed: DFlash aborts on dense qwen35 targets

DFlash against the dense 27B hard-aborted:
`GGML_ASSERT(t_layer_inp[il] != nullptr && "layer input tensor is null")` at
`llama-graph.cpp:924`, via `llama_decode → graph_reserve → build_graph`.

Cause: DFlash asks the target for hidden states at its `target_layers`, but only
**6 of 133** model archs in this fork populate `t_layer_inp` (`llama`, `qwen3`,
`qwen3moe`, `qwen35moe`, `gemma4`, `openai-moe`). The graft author added the
exposure to `qwen35moe` (their MoE target) and left the dense `qwen35` arch out.
One-line fix, mirroring qwen35moe:

```c
// src/models/qwen35.cpp, top of the transformer-layer loop
res->t_layer_inp[il] = inpL;
```

Applied locally and rebuilt as `ghcr.io/hal0ai/hal0-rocmfpx:dflash-densefix`
(not pushed). Worth sending upstream to `charlie12345/rocmfp4-llama` — it is a
latent crash for any dense qwen35 + DFlash/EAGLE3 pairing, independent of hal0.
Any other arch we later want to draft for needs the same one-liner.

### `n_max` sweep — bigger is strictly worse

Reporter's own FP4 MoE target, BF16 draft, `dflash.block_size=16` (so n_max clamps
at 15). Baseline no-draft **47.28 t/s**:

| n_max | decode t/s | acceptance | drafted | accepted |
|---|---|---|---|---|
| 4 | **35.22** | 77.1% | 292 | 225 |
| 6 | 31.91 | 60.3% | 388 | 234 |
| 8 | 26.25 | 52.3% | 461 | 241 |
| 12 | 23.44 | 36.9% | 658 | 243 |
| 15 | 23.51 | 29.6% | 821 | 243 |

Accepted tokens **saturate at ~225–243 regardless of n_max** while drafted tokens
triple. DFlash takes an independent argmax per block position, so acceptance decays
along the block and everything past ~4 positions is drafted-then-thrown-away. My
initial guess that `n_max 4` was the bug (vLLM examples use 15) was wrong — 4 was
already the best value in the sweep. Draft precision is irrelevant: Q8_0 matched
BF16 within noise (31.68 vs 31.91 at n=6).

### The real discriminator is dense vs sparse-MoE, not configuration

| target | plain | DFlash (best n) | MTP |
|---|---|---|---|
| CHADROCK3.6-27B **dense** FP4 | 12.51 | **14.65** (n=6, 63.9%) → **+17%** | **23.25** (56.4%) → **+86%** |
| Qwen3.6-35B-A3B **MoE** FP4 | 47.28 | 35.22 (n=4, 77.1%) → **-25%** | — |

This reproduces the published mechanism: for A3B (8-of-256 routing, sparsity 0.031)
the verify pass must load the **union** of experts activated across all K draft
tokens, so verification cost scales with K instead of amortising. That report
measured 100% acceptance and still lost throughput, with an expert-saturation
threshold `T_thres ≈ 94` — far above any usable K. Our -25% at 77% acceptance fits.

The reporter's +52% on the same MoE architecture remains unexplained; their fused
single-file build (which we cannot load) may differ materially from the two-file
path we measured.

**Decisive point: MTP beats DFlash by 5x on the margin where DFlash even works**
(+86% vs +17% on the dense target), and hal0 already ships MTP.

## Verdict

**Do not adopt DFlash, and do not repin the default.** Reasons, all measured at
correct clocks:

1. **DFlash loses on sparse MoE** (-25% best case) and is architecturally unsuited
   to it, not merely misconfigured. Our MoE slots are where the throughput is.
2. **On dense targets, where DFlash does win (+17%), MTP wins far more (+86%)** —
   and MTP is already shipping, needs no second model file, and no new image.
3. The new lineage separately regresses plain decode **-19%** (0.8B) / -3.4% (12B).

So the image is strictly worse than `c077206` on every axis we can measure. It stays
published for opt-in `image_pin` trial; it should not become the default.

### On grafting DFlash onto our best runner image

Feasible but not worth doing. The old `c077206` lineage already carries the
prerequisites — `common_speculative_impl`, `t_h_pre_norm`, `target_layer_ids`,
`llama_set_embeddings_layer_inp`, `n_embd_inp` are all present; it exposes
`draft-mtp` / `draft-simple` / `ngram-*` but not `draft-dflash`. So a backport is a
bounded port of the DFlash impl class plus `src/models/dflash.cpp`, not a
lineage-wide rewrite.

But the point of "best of both worlds" was DFlash's speed, and DFlash is the weaker
speculation method for us on both model classes. The best of both worlds is what we
already run: `c077206` + MTP. Revisit only if a future DFlash drafter fixes the
MoE verification economics, or if we start serving dense models where MTP drafts
are unavailable.

### Worth doing regardless

Send the `qwen35.cpp` `t_layer_inp` one-liner upstream to
`charlie12345/rocmfp4-llama` — a latent hard crash for any dense qwen35 + DFlash or
EAGLE3 pairing, unrelated to whether hal0 adopts DFlash.

## Model assets downloaded (~43 GB, `/root/dflash_fetch.sh`)

| path under `/mnt/ai-models` | what |
|---|---|
| `qwen3.6-35b-a3b-dflash-strix-lean/` | reporter's fused target+draft, 19 GB |
| `dflash-draft-qwen3.6-35b-a3b/` | standalone draft, BF16 747 MB + Q8_0 402 MB |
| `dflash-draft-qwen3.6-27b/` | standalone draft, BF16 3.3 GB + Q8_0 1.8 GB |
| `qwen3.6-35b-a3b-strix-lean/` | reporter's plain target, 19 GB (gap check) |

The standalone drafts are `Alittlehammmer/*-DFlash-GGUF-llama.cpp` conversions of
`z-lab/*-DFlash` — z-lab is the origin of DFlash (232k / 101k downloads on HF).

Live DFlash numbers are in the Results section above. One separate finding:

- **The fused single-file GGUF does not load on our build:**
  `done_getting_tensors: wrong number of tensors; expected 822, got 753`. The repo
  README claims branch `dflash-graft` supports embedded drafts, but the branch tip
  is unchanged from what we built (`5c493ec38`) and carries no `dflash.embedded`
  handling — the two fixes that README describes (loader tensor-count check
  allowing sibling-model tensors, and copying the draft's `mask_token_id`) are not
  in it. Use the two-file path, or ask the reporter where those fixes live.

## Trial wiring (no hal0 code changes needed)

DFlash needs a per-model draft GGUF, so the right layers are the existing
escape hatches, not a new seed profile:

- Slot: `image_pin = "ghcr.io/hal0ai/hal0-rocmfpx:dflash-5c493ec"`
  (spec-hw-slot-ownership §3 — overrides `RUNNER_IMAGES[binary]` for that slot).
- Model registry `defaults.extra_args`:
  `--spec-type draft-dflash --spec-draft-model /models/<dflash-draft>.gguf`
  (`merge_flags` precedence lets these ride alongside/override the MTP bundle
  defaults; the fork excludes DFlash from the draft-simple auto-enable heuristic,
  so an explicit `--spec-type` is required).
- Draft models: the reporter's HF repos (`gsrunion/…-ROCmFPX-GGUF`,
  `…-STRIX_LEAN-GGUF`) — recipes requested on #1349.

## Promotion criteria (before repinning the default)

1. ~~Every currently-served GGUF loads on the new lineage (lxc105 re-test).~~ **PASS**
2. ~~MTP profiles (`--spec-type draft-mtp`) still work.~~ **PASS**
3. Plain-decode parity — **FAIL**, -19% on the 0.8B, -3.4% on the 12B (idle box, r=5).
4. DFlash uplift at server level — **FAIL as configured**, -32% at 78.5% acceptance.

Repro scripts on the Halo box, all non-disruptive (`podman run --rm`, models
mounted read-only, no hal0 slot touched):

| script | what |
|---|---|
| `/root/dflash_retest.sh` | **the trustworthy round** — A/B + reporter-file DFlash |
| `/root/dflash_ab.sh` | five-model Tier-A A/B (first, void round) |
| `/root/dflash_gates.sh` | r=5 noise check + MTP launch probe |
| `/root/dflash_measure.sh` | DFlash baseline / two-file / baked |
| `/root/dflash_fetch.sh` | model downloads |
| `/root/dflash_build.sh` | image build |

Once 3 and 4 resolve, in one PR: bump `DEFAULT_ROCMFPX_IMAGE` (src/hal0/config/schema.py) to the
new tag, add `ghcr.io/hal0ai/hal0-rocmfpx:c077206` to
`STALE_ROCMFPX_IMAGE_REFS`, and (per docs/design/toolbox-repo-consolidation.md
T0-A) repoint the fork CI to publish
`ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocmfpx-<ver>-dflash`. A DFlash flag
bundle beside `build_mtp_flag_bundle()` only makes sense once draft-model path
resolution has a home in the model registry (model-owned drawer semantics).
