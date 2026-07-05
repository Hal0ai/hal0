# ROCmFPX runner — exploratory profile-optimization bench runbook

**Status:** READY TO RUN (on-box, GPU windows). Companion to the
concurrency-batching plan (2026-07-05) and the profile-consolidation handoff
(2026-07-04). This is the *explorative* round: it does not assume our current
seed flags are right for the new runner — it re-derives them, because the
runner's own docs contradict several of our seeds.

**What changed.** The FPX slots (`code-fpx` 27B ROCmFP4 dense · ROCm lane;
`moe-fpx` 35B-A3B ROCmFPX · Vulkan lane) run a custom llama.cpp fork
(`ciru-ai/ROCmFPX @7aa484a`, tracks upstream `b9438`/`22cadc194`, ~June-2026
master). Two deep research passes (the `jcbtc/chadrock3.6-27b-coder-rocmfp4-mtp`
model card + vendor launch scripts, and the fork's source/docs) surfaced
flag values that differ from what hal0 seeds today. This runbook sweeps those
deltas instead of trusting either side blind.

---

## 0. Research-grounded findings that drive the matrix

Each is a hypothesis to CONFIRM on our box, with the source that motivates it.

### 0.1 Batch shape — our seeds are probably pessimal
- **Finding (fork `ROCmFPX-OPTIMIZATION-PLAN.md`):** on a 3946-tok prompt,
  `-b 2048 -ub 512` gave ~1064–1088 PP t/s vs `-b 8192 -ub 2048` only
  708–822 PP t/s. Promoted shape is **`-b 512 -ub 512`** (or `-b 1024 -ub 512`);
  **small ubatch (512) wins both PP and decode** on gfx1151.
- **Our seeds:** `rocm-moe` = `-b 8192 -ub 1024`; `rocm-dnse` = `-b 8192 -ub 2048`.
  Both were tuned on the OLD toolbox runner with DIFFERENT (Qwen UD-Q5/CROWN)
  models. **Likely wrong for FPX.** → Cell **BATCH**.

### 0.2 KV-cache quant — model-specific and counterintuitive
- **Finding (fork KV-isolation docs):** for **27B, `q4_0` main + `q4_0` draft
  KV won; `q8_0` main KV *regressed* the 27B.** For **35B MoE, `q8_0` main +
  `q4_0` draft KV** is best sustained. K and V must match (K-only or V-only q8
  always regressed). Interactive quickstart default is q8/q8, but the vendor
  27B `serve_*.sh` also ships q8/q8 — i.e. the shipped script may itself be
  suboptimal per the fork's own isolation study.
- **Upstream `plunderstruck` card** claims MTP acceptance 0.87–0.90 holds
  "under warm **f16** KV" — a third data point. On 128 GB, f16 KV is
  affordable at these ctx depths.
- **Our seeds:** uniform `q8_0/q8_0`. → Cell **KVQ** (three-way: q4/q4 vs q8/q8
  vs f16/f16 main; draft KV q4 vs q8 vs f16), scored on **decode t/s AND draft
  acceptance**, per model.

### 0.3 MTP draft params — context- and model-dependent, not a constant
- **Finding (fork MTP docs):** short/med context favors `n-max 4, p-min
  0.0–0.75`; **long context (64–128k) favors `n-max 1–2, p-min 0.0`.** The
  vendor 35B script uses `n-max 3, p-min 0.25` + **`--no-spec-draft-backend-
  sampling`**; vendor 27B uses `n-max 4, p-min 0.0`. Repo warns explicitly:
  **FP3 winners ≠ FP4 winners** — tune per quant.
- **Our bundle (`build_mtp_flag_bundle`):** hardcodes `n-max 4, n-min 0,
  p-min 0.0, p-split 0.10, draft-KV q8_0/q8_0` for ALL backends. Draft KV q8
  contradicts 0.2 (fork wants q4 draft). No `--no-spec-draft-backend-sampling`.
  → Cell **MTP** (uses per-request JSON override — see 0.6 — so this sweep is
  cheap and restart-free).

### 0.4 MTP × continuous batching — CONFIRMED present, only perf-unknown
- **Finding (fork source at 7aa484a):** parallel drafting is in the tree —
  `common_speculative_init(params, n_seq)` is n_seq-aware; `server-context.cpp`
  inits speculative per `n_parallel`; `common/speculative.cpp` loops per-seq
  with a cross-ubatch MTP bridge; the `-np 1` fast path was **prototyped and
  rejected** (code is built around the parallel path). So `--spec-type
  draft-mtp -np 8` RUNS.
- **Consequence:** the plan's P2.0 "does the image accept it?" probe is
  **already answered YES by source inspection** — skip the probe, go straight
  to the perf sweep. → Cell **MTPxNP**.

### 0.5 Vulkan vs HIP — the answer to "where does Vulkan win?"
- **Finding (fork `ROCmFP4-MTP-COMPARISON.md`, 27B ROCmFP4 STRIX_LEAN, same
  binary, 262k ctx):**

  | Lane | PP t/s | Decode t/s |
  |---|---:|---:|
  | ROCm0 (HIP) | 99.8 | **27.6** |
  | Vulkan0 | **123.3** | 24.9 |

  **Vulkan wins prompt-processing (+24%); HIP wins sustained decode (+11%).**
  Confirmed in a second sweep (Vulkan better burst 40.0 / lower sustained 27.7;
  ROCm lower burst 35.0 / higher sustained 29.9). **Exception:** a StepFun
  Step-3.7 MTP sweep had **Vulkan faster on BOTH** at small `n-max` (39–41 vs
  33–36 t/s), and the fork's interactive chat launcher defaults to Vulkan0.
- **So Vulkan wins when:** (a) **prompt-processing-bound** workloads — long
  input, short output: RAG, code-context reads, doc Q&A, re-prefill after
  cache miss; (b) **burst / short-decode** interactive turns; (c) **small
  `n-max` MTP**; (d) possibly **low-concurrency** (plan fact 4: "Vulkan for
  1–4"). **HIP wins when:** sustained long-form generation, and (plan fact 4)
  **high concurrency np 8–16**, where Vulkan on Qwen3-Coder regressed at np16.
- **Implication for slot design:** the lane is a per-workload choice on ONE
  binary. `moe-fpx` on Vulkan is right for interactive; an **agent fan-in FPX
  slot may want the ROCm lane at high `-np`**. → Cell **LANE** (every model on
  BOTH lanes) and the LANE×NP crossover is the headline result.

### 0.6 Restart-free MTP sweeps via per-request JSON
- **Finding:** the fork accepts per-request `speculative.{n_max,n_min,p_min}`
  in the `/completion` JSON (helper `scripts/rocmfpx-draft-profile.py`). MTP
  param sweeps need **no server restart** — only KV-quant / batch-shape /
  lane changes require a relaunch. This collapses Cell MTP from ~12 restarts
  to a single warm server. `server_ab.py --mode batch` should pass these
  through (small enhancement, see §4).

### 0.7 Env + build facts to pin before trusting any number
- **HIP lane requires:** `HSA_OVERRIDE_GFX_VERSION=11.5.1`,
  `GGML_HIP_ENABLE_UNIFIED_MEMORY=1`. Our `rocmfpx-rocm` profile `[server].env`
  must carry these or the ROCm numbers are invalid.
- **Build:** `-DGGML_HIP_FORCE_MMQ=ON` (mandatory for FP4 MMQ), rocWMMA FA
  **OFF** (fork uses its own FA kernels), promoted gfx1151 decode tune = MoE
  rows-per-block 2 / NWARPS 2 / FA-V rows-per-thread 8. Capture the built
  image's tune profile in the results (reproducibility, plan risk 6).
- **Vulkan:** no RADV_PERFTEST tuning documented for gfx1151 (untuned axis —
  optional exploratory cell, low priority).
- `-fa on` and `-ngl 999` are non-negotiable on both lanes for these formats.

---

## 1. The matrix

Two models × the axes below. Models: **27B** = `code-fpx`
(CHADROCK3.6-27B-Coder ROCmFP4 STRIX_LEAN, dense); **35B** = `moe-fpx`
(CHADROCK3.6-35B-A3B ROCmFPX MoEQuality 7.08 BPW). Both ship mmproj (vision)
and an MTP head. Froggeric template on the 35B; `--jinja` embedded on the 27B.

**Depth axis (applies to every decode cell):** ctx-fill **2k / 32k / 128k**.
Rationale: the model card's own sweep shows decode 21→12.5→7 t/s and PP
316→201→142 across 4k→65k→130k with acceptance FLAT (~40%). Tuning at 2k and
shipping for agents that run at 32k+ is how we mis-tune. Every "best" is
reported per depth.

**Sampler axis (every MTP cell):** run **greedy** (temp 0, upper-bound
acceptance) AND **production sampler** (27B: `temp 0.6 top-p 0.95 top-k 20`
coder-precise, or card's `temp 1.0`; 35B: `temp 0`). The live quick-bench
already showed greedy inflating 27B decode 24.95→30.6 — greedy alone would
over-promise. Report both; ship against the production lane.

### Tier A — llama-bench (raw kernel shape, via `hal0-benchctl sweep`)
Single-stream, no server, no MTP (llama-bench can't spec-decode). Isolates
batch/KV/lane kernel behavior fast.

| Cell | Sweep | Fixed | Motivates |
|---|---|---|---|
| **A-BATCH** | `-b {512,1024,2048,8192} × -ub {256,512,1024,2048}` | lane=rocm, KV q8/q8 | 0.1 — find the real PP/decode knee per model |
| **A-KVQ** | `-ctk/-ctv {q4_0,q8_0,f16}` (K=V) | best batch from A-BATCH | 0.2 — per-model KV winner |
| **A-LANE** | `{ROCm0, Vulkan0}` at 3 depths | best batch+KV | 0.5 — the PP-vs-decode crossover, per model |
| **A-DEPTH** | ctx-fill `{2k,32k,128k}` | best of above | 0.2 long-ctx decay confirm |

### Tier B — server single-stream (`server_ab.py`, MTP live)
What Tier A can't measure: speculative decode, prompt-cache reuse, real
chat-templated turns.

| Cell | Sweep | Motivates |
|---|---|---|
| **B-MTP** | per-request `n-max {1,2,3,4} × p-min {0.0,0.25,0.5,0.75}`, restart-free (0.6); draft-KV `{q4_0,q8_0,f16}` (restart) | 0.3 — per-model/per-depth draft winner + acceptance |
| **B-BACKSAMP** | `--no-spec-draft-backend-sampling` on/off | 0.3 — vendor sets it on the MoE; is it a win here? |
| **B-LANE-MTP** | best-MTP on `{ROCm0, Vulkan0}` × 3 depths, greedy+sampler | 0.5 — does the StepFun "Vulkan wins both at small n-max" exception hold for chadrock? |
| **B-REUSE** | `--cache-reuse {0,256}` + `--slot-prompt-similarity` on a shared-prefix trace | agent prompt-cache behavior |

### Tier C — concurrency (`server_ab.py --mode batch`, the plan's payload)
Every cell at `-np {1,2,4,8,16}`, `-kvu` on (unified pool), aggregate +
per-stream + ttft-p95.

| Cell | Sweep | Motivates |
|---|---|---|
| **C-LANExNP** | `{ROCm0,Vulkan0} × np{1,2,4,8,16}`, MTP off | 0.5 + plan fact 4 — confirm/deny "Vulkan 1–4, ROCm 8–16" ON THIS RUNNER/MODEL |
| **C-MTPxNP** | best lane × `np{1,2,4,8}` × MTP{off,on} | 0.4 — does the 2.13× (27B) / ~1.4× (35B) MTP win SURVIVE batching, or does per-step expert streaming erode it? **First gfx1151 datapoint anywhere.** |
| **C-CTXSPLIT** | `-kvu` vs no-kvu at np4, one long request among short | plan D2 — prove unified keeps ctx a shared pool |
| **C-GTT** | `moe-fpx` np-sweep WITH `hal0-slot-agent` co-resident | plan risk 5 — reject an np that only wins by starving a neighbor (~31 GB GTT floor) |

---

## 2. Acceptance criteria (decide, don't just measure)

Ship a flag change to a seed profile ONLY if:
- **Batch/KV:** ≥ **+5%** on the governing metric (PP for prefill-bound
  classes, decode for chat) at the **32k** depth (not just 2k), production
  sampler, reproduced across ≥3 runs, no >2% regression at another depth.
- **MTP params:** higher **net decode t/s** on the production sampler at 32k;
  acceptance rate reported but not the ship metric (greedy acceptance is a
  ceiling, not a promise).
- **Lane per slot class:** adopt Vulkan for a slot class if it wins the class's
  governing metric by ≥ **+10%** at that class's operating point (interactive
  = 2k–32k, np1–2; agent = 32k+, np4–8). Otherwise ROCm.
- **`parallel N`:** adopt for a class only if aggregate ≥ **2×** np1 at ≤
  **2.5×** p95 ITL AND no starvation of a co-resident slot (C-GTT). Interactive
  slots stay low-np regardless (per-stream ≈ 1/N is a product call).
- **Batched MTP:** keep MTP on under `-np>1` only if C-MTPxNP shows the
  speculation win is retained (net aggregate with MTP ≥ without); else the
  seed's batched agent class runs MTP off and the interactive class keeps it.

Every "no change" outcome is a RESULT — record it so we stop re-litigating.

---

## 3. Expected profile deltas (hypotheses the bench will accept/reject)

Pre-registered so the bench can't rationalize post-hoc. Written as diffs to
today's seeds; the runbook either confirms → we ship, or rejects → we keep.

- **`rocmfpx-rocm` (27B dense):** `-b 8192 -ub 1024/2048` → **`-b 512 -ub 512`**
  (0.1); `-ctk/-ctv q8_0` → **`q4_0`** (0.2, the counterintuitive one);
  add env `HSA_OVERRIDE_GFX_VERSION=11.5.1`, `GGML_HIP_ENABLE_UNIFIED_MEMORY=1`
  (0.7); MTP draft-KV q8→**q4** (0.2/0.3).
- **`rocmfpx-moe` (35B A3B):** `-b 8192 -ub 1024` → **`-b 512–2048 -ub 512`**
  (0.1 + vendor 2048/512); keep main KV **q8/q8**, draft KV → **q4** (0.2);
  MTP **`n-max 3, p-min 0.25`** + **`--no-spec-draft-backend-sampling`** (0.3);
  Froggeric template (already sourced).
- **`build_mtp_flag_bundle`:** the hardcoded `n-max 4 / p-min 0.0 / draft-KV
  q8_0` is too coarse — findings 0.2/0.3 say draft KV should be q4 and n-max/
  p-min are model+depth-specific. Candidate: make the bundle's draft-KV and
  n-max **model-overridable via registry `defaults.extra_args`** (the merge
  layer already exists) rather than one global constant. Bench picks the
  per-model values; the seed default becomes the safe short-ctx one.
- **Lane assignment:** likely **unchanged** (27B→ROCm decode, 35B→Vulkan) for
  the INTERACTIVE classes, but a NEW **agent fan-in FPX class on ROCm** at
  `-np 8` if C-LANExNP confirms the crossover.
- **Vision × MTP:** the model card says run **MTP off for vision**. `_effective_mtp`
  has no vision-awareness — a vision request on an MTP slot is untested by the
  vendor. Out of bench scope but flagged: add a launch note / consider
  auto-disabling MTP when an mmproj request is served (design follow-up, not
  this round).

---

## 4. Tooling deltas needed before the run

1. **`server_ab.py` MTP param passthrough** — add `--spec-nmax`, `--spec-pmin`
   (comma-lists) to `mode_ab`/a new `mode_mtp`, sent as `speculative.*` in the
   `/completion` JSON so B-MTP sweeps run restart-free (0.6). Small, additive.
2. **`profile-matrix.sh` FPX cells** — add `MOE_MODEL`/`DENSE_MODEL` overrides
   pointing at the FPX GGUFs and the A-KVQ three-way (q4/q8/f16). The seam
   whitelist must allow `f16` KV and `q4_0` KV values.
3. **Provenance capture** — record `hal0-rocmfpx:7aa484a` + the SPIR-V
   include fix + the decode-tune profile (`ROCMFP4_DECODE_TUNE`) in the results
   header (plan risk 6). The image is local-only; numbers are irreproducible
   without it.
4. **`--mode batch` env** — ensure the HIP env vars (0.7) are in the slot's
   `[server].env` before C-* runs, or ROCm-lane numbers are wrong.

---

## 5. Run order (one GPU window, ~most-informative-first)

1. **A-LANE** on both models at 2k/32k — the Vulkan-vs-HIP crossover is the
   highest-value single result and shapes everything downstream. (~30 min)
2. **A-BATCH** + **A-KVQ** on the winning lane per model — corrects the two
   flags most likely wrong today. (~45 min)
3. **B-MTP** (restart-free) + **B-BACKSAMP** at 2k/32k — draft tuning. (~30 min)
4. **B-LANE-MTP** — does Vulkan's small-n-max "wins both" exception hold? (~20 min)
5. **C-LANExNP** — the plan's core concurrency result. (~40 min)
6. **C-MTPxNP** — batched-MTP survival, the world-first cell. (~30 min)
7. **C-GTT** — only if C-* recommends np>1 on `moe-fpx`. (~15 min)

After each tier: `aggregate` → read SUMMARY.md → apply §2 acceptance → note
accept/reject against §3 in this doc's results appendix (to be added on-box).

---

## 6. Open questions this round does NOT close

- **`--n-cpu-moe` / `-ot exps=CPU` for the 35B** — the fork exposes them but
  has no Strix recipe (their MoE was fully offloaded on 128 GB). Only relevant
  if we want to free GPU for a co-resident slot; separate investigation.
- **RADV_PERFTEST / Vulkan env tuning for gfx1151** — undocumented; an
  optional low-priority exploratory cell.
- **Compile-time decode tune** (`ROCMFP4_DECODE_TUNE` variants) — the fork
  already promoted rpb2/nwarps2 as best; re-testing means rebuilding the image.
  Out of scope unless a decode cell disappoints badly.
- **Vision + MTP combined** — untested by the vendor; design follow-up (§3).
