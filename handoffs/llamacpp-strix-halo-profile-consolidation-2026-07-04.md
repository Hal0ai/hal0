# llama.cpp seed-profile evaluation & consolidation — Strix Halo, July 2026

**Status:** RESEARCH + PROPOSAL — no code changed. Grounded in (a) a source-read
of the profile pipeline (`SEED_PROFILES`/`MTP_FLAG_BUNDLE` in `config/schema.py`,
`installer/etc-hal0/profiles.toml`, `providers/container.py` argv assembly,
`slots/argv.py`, `install/profile_derive.py`, `registry/curated.py`), and (b) two
web-research passes: llama.cpp-master flag verification (`common/arg.cpp`,
fetched 2026-07-04) plus the Strix Halo community corpus (kyuz0 toolboxes,
strixhalo.wiki, llm-tracker, lhl/strix-halo-testing, strix-benchmarks, upstream
PRs/issues). Citations inline; every flag named below was verified to exist on
llama.cpp master as of 2026-07-04.

**Goal (operator request):** ship ONE seeded profile per workload type —
MoE / Dense / Vulkan / CPU / Embed / Rerank (+ the non-llama.cpp seeds
flm / tts / tts-qwen3 / comfyui unchanged) — and get the flags right for the
current model fleet (Qwen3.5/3.6 dense + A3B MTP builds, Gemma 4, Qwopus MTP
coders, bge-reranker-v2-m3, Qwen3-Embedding-0.6B).

---

## 1. TL;DR — current → proposed

| Today | Verdict | Proposed |
|---|---|---|
| `rocm` ("MoE agents", -b 512, no jinja, no MTP) | intent/flags mismatch; undertuned prefill; fold away | **remove** (absorbed by `rocm-moe`/`rocm-dense`) |
| `rocm-dnse` (Dense+MTP) | keep, re-tune | **`rocm-dense`** |
| `rocm-moe` (MoE+MTP) | keep, re-tune | **`rocm-moe`** |
| `vulkan` (fallback) | keep, re-tune | **`vulkan`** |
| `cpu-llm` | undertuned for 16-core Zen 5 | **`cpu`** (retuned) |
| — (embed slots get plain `rocm` via `derive_profile`, `--embedding` never injected on container path) | gap | **`embed`** (new) |
| — (rerank hand-wired via extra_args per curated.py note) | gap | **`rerank`** (new) |
| `flm` / `tts` / `tts-qwen3` / `comfyui` | not llama.cpp | unchanged |
| `mtp` boolean on profiles + `MTP_FLAG_BUNDLE` constant | wrong layer | **separate MTP from profiles** (§4) |

Result: 6 llama.cpp profiles, exactly one per type, with MTP moved to
model+slot resolution instead of doubling the catalog.

---

## 2. Research facts that change our flags

Each of these is load-bearing for §3. Sources: kyuz0/amd-strix-halo-toolboxes
README + dashboard, strixhalo.wiki/AI/llamacpp-performance (+ ROCm page),
llm-tracker.info/_TOORG/Strix-Halo, strix-benchmarks.vercel.app,
lilting.ch Qwen3.5 KV-cache article, llama.cpp PR #22673 / discussions
#22411, #23659, #20856, #15396, #19674 / issues #17917, #12352, #23978,
#23302, #20085, #6263/#11105, Unsloth model docs (Qwen3.5/3.6, Gemma 4, MTP),
Qwen official llama.cpp docs.

1. **`-ngl` must be explicit.** Upstream `n_gpu_layers` now defaults to
   `-1 = auto` under the new `--fit` machinery, but free-memory detection on
   GTT/unified memory is historically unreliable — the community convention on
   Strix Halo is an explicit `-ngl 999`. Today **no hal0 profile sets `-ngl` at
   all**; live slots hand-carry `-ngl 999`/`-ngl 99` in `extra_args` (see the
   `slots/argv.py` docstring). The profile should own this.
2. **Symmetric q8_0 KV is now fine on BOTH backends; asymmetric is poison.**
   HIP: symmetric `-ctk q8_0 -ctv q8_0` keeps the fused FA kernel; K≠V silently
   falls back to a slow path (discussion #22411 — matches the existing
   profiles.toml comment). Vulkan: the old "quantized KV halves tg" reports are
   stale — q8_0/q8_0 at 65k ctx held f16-baseline decode speed on a
   Strix Halo/Vulkan sweep (lilting.ch, b8183). Caveats: q8 KV still costs some
   *prefill*; f16 remains the fastest when memory allows.
3. **Gemma is a KV-quant trap.** Quantized KV on Gemma 3 = ~10x pp slowdown
   (#12352); Gemma 4's iSWA + `--swa-full` is incompatible with cache quant
   (#23978). Gemma models must run f16 KV, no `--swa-full` — per-MODEL
   overrides, not profile flags (the argv layering already lets
   `model_defaults` beat profile).
4. **Batch sweet spots per backend:** ROCm ≈ `-ub 2048` (matches current
   rocm-dnse/moe), RADV ≈ `-ub 1024`, AMDVLK ≈ 512 (lhl sweeps). For
   wide-expert MoE, `-b 4096 -ub 4096` is the documented pp booster
   (#15396/#19674) at a few GB of compute buffer — worth a bench pass on the
   A3B fleet, but 2048 is the safe ship default. The current `rocm`/`vulkan`
   seeds' `-b 512 -ub 512` are undertuned for prefill on this hardware.
5. **Threads barely matter at full offload, and SMT hurts.** `-t 1..8` swept
   within ~1% on Strix Halo (#23659); llama.cpp's default is physical cores
   (16). `--threads-batch 32` in the current MTP profiles is SMT territory —
   drop it. `--threads 16` (or nothing) is right; `cpu-llm`'s `--threads 4` is
   badly undertuned for CPU inference on this chip.
6. **`--no-mmap` stays for GPU profiles.** Universal Strix Halo advice (kyuz0
   "always"; strixhalo.wiki: mmap is "catastrophically bad" for ROCm load
   times; page-cache double-residency on unified memory). For the CPU-only
   profile, default mmap is preferable (faster re-loads, page-cache friendly,
   no GTT involved). `--mlock` remains pointless in containers (RLIMIT).
7. **MTP upstream (PR #22673, merged 2026-05-16):** `--spec-type draft-mtp`,
   `--spec-draft-n-max` default 3, sweet spot **2–3** (our bundle uses 4);
   nondeterminism bug reported at n-max 3 (#23302) → 2 is the conservative
   pick. MTP **requires `--parallel 1`**, takes a small prefill hit, and needs
   MTP-head GGUFs. Reported gains: ~1.2x on Qwen3.6-35B-A3B on a Strix Halo
   APU (the PR itself), ~1.4x on Qwen3.5-122B-A10B under ROCm 7.2.3 (#23659) —
   MoE gains less than dense because active params are already small. Also new
   and free: `--spec-default` (tuned ngram speculation, no draft head needed)
   — worth a bench on non-MTP models like gemma-4 and the UD dense builds.
8. **`--jinja` always** for modern instruct/tool-calling models (Qwen official
   docs; needed for tool-call parsing, `reasoning_content` splitting,
   `--chat-template-kwargs`). Today only `rocm-moe` has it. Known mid-2026
   parser bugs with thinking+tools (#22684, #20260) → agent slots may want
   thinking disabled via model/slot args (`--reasoning-budget 0`).
9. **`--cache-reuse 256`** is the cheap agentic-serving win (KV-shift prompt
   reuse), and `--context-shift` is now **off** by default upstream. Caveat:
   cache-reuse interacts badly with SWA models (Gemma #21468/#21749) — another
   reason it rides the ROCm Qwen-family profiles and Gemma gets a model-level
   override.
10. **Embed/rerank sizing rule:** pooled models need the whole input in one
    *physical* batch — `-ub` = max input tokens, `-b ≥ -ub` (#6263/#11105).
    bge-reranker-v2-m3 and Qwen3-Embedding are 8192-token models → `-ub 8192`.
    Never combine `--embedding` and `--reranking` on one instance (#20085
    zero-score bug). `--pooling` should be left to GGUF metadata in the
    profile; `--pooling last` goes in the Qwen3-Embedding model defaults
    (its GGUFs require last-token pooling).
11. **Backend picture 2026:** Vulkan RADV = stability/tg default; tuned
    ROCm 7.2.x wins prefill and >32k-ctx work (and on recent MMQ-tuned builds,
    both phases for big MoE). It flips with build/ROCm version — re-bench per
    image bump. Our `rocm-7.2.4` image pin matches the community
    known-good stack. `ROCBLAS_USE_HIPBLASLT=1` should be set in the ROCm
    images (kyuz0's do; verify our fork kept it).
12. **FP4 reality check:** NVFP4 is upstream as a GGUF type but there is **no
    accelerated FP4 path on gfx1151** (RDNA3.5 WMMA = f16/bf16/int8/int4) — FP4
    on this hardware is a bandwidth/size win via dequant, not a compute win.
    Our local bench numbers are the authority for the ROCmFP4 fork builds; just
    don't expect upstream FP4 speedups to transfer.
13. **Host-level (installer, not profiles):** kernel params
    `amd_iommu=off amdgpu.gttsize=126976 ttm.pages_limit=32505856` (124 GiB
    GTT; iommu-off is worth 5–12%) — worth an installer/docs check that we set
    these. `-dio/--direct-io` if large-model loads ever hang in containers.

---

## 3. Proposed seed catalog (one per type)

Flags below are ship-defaults; `[model].defaults`/`extra_args` still override
per the existing precedence (`base < profile < model_defaults < … < extra_args`).
All GPU profiles gain explicit `-ngl 999` and `--jinja`.

### `rocm-moe` — MoE agents flagship (Qwen-AgentWorld / CROWN / ACE-SABER 35B-A3B, CHADROCK 35B)
```
-ngl 999 -fa on -ctk q8_0 -ctv q8_0 -b 4096 -ub 2048
--parallel 1 --threads 16 --no-mmap --jinja --cache-reuse 256
```
Changes vs today: +`-ngl 999`, +`--cache-reuse 256`, `-b 8192→4096` (MoE pp
band per #15396/#19674; bench `-ub 4096` as a follow-up), drop
`--threads-batch 32` (SMT), drop `--poll 100 --poll-batch 1` (no measurable
effect at full offload per thread sweeps; re-add only if a local A/B shows it).
MTP: via §4 (today: keep `mtp=true`).

### `rocm-dense` — dense chat/coder (CHADROCK 27B pi-agent, Qwopus 27B/9B, Qwen3.6-27B, gemma-4*)
```
-ngl 999 -fa on -ctk q8_0 -ctv q8_0 -b 8192 -ub 2048
--parallel 1 --threads 16 --no-mmap --jinja --cache-reuse 256
```
Replaces BOTH `rocm` and `rocm-dnse` (the plain `rocm` role — "std
broad-compat" — is just this profile with MTP off, which is a slot/model
question, not a profile). *gemma-4 models need registry defaults
`-ctk f16 -ctv f16` (KV-quant trap, fact 3) — see §5.

### `vulkan` — universal fallback (any GGUF, non-FP4, Gemma-safe)
```
-ngl 999 -fa on -b 2048 -ub 1024 --parallel 1 --threads 16 --no-mmap --jinja
```
f16 KV stays deliberate — this is the "any model, zero surprises" lane
(Gemma-safe, prefill-fastest). Document that symmetric q8_0 KV is now a valid
operator override on Vulkan when ctx memory matters (fact 2). `-ub 512→1024`
per RADV sweet spot.

### `cpu` — CPU-only llama-server (GPU-less boxes, tiny utility models)
```
--threads 16 -b 2048 -ub 512 --parallel 1 --jinja
```
Changes: threads 4→16 (physical cores; today's value leaves 3/4 of the chip
idle), batches 256→defaults (256 was RAM-scared; irrelevant at these model
sizes), **drop `--no-mmap`** (CPU-only wants the page cache), +`--jinja`.
Rename `cpu-llm`→`cpu` optional; keep the old name if slot migration cost
outweighs the cosmetic gain.

### `embed` — NEW (Qwen3-Embedding-0.6B, nomic, bge)
```
--embedding -ngl 999 -fa on -b 8192 -ub 8192 --no-mmap
```
`-ub 8192` is the correctness knob (whole input in one physical batch,
fact 10). No `--pooling` here — GGUF metadata decides; `--pooling last` rides
Qwen3-Embedding's model defaults. No KV-quant flags (meaningless for pooled
encoders). Slot ctx: 8192–32768 per model. iGPU because these are
pp-dominated; a 0.6B encoder costs ~nothing in GTT.
Closes the real gap: today `derive_profile(embed)` hands out chat-tuned
`rocm`, and the container path never injects `--embedding` at all (only the
native `llama_server.py` provider does).

### `rerank` — NEW (bge-reranker-v2-m3, jina-tiny)
```
--reranking -ngl 999 -fa on -b 8192 -ub 8192 --no-mmap
```
Serves `/v1/rerank`; `--reranking` implies embedding-mode + rank pooling.
Slot ctx 8192; for parallel scoring bump to `-c 65536 --parallel 8`
(n_seq × 8192, ggerganov's own recipe from PR #9510) once DR-1 (idle-evict
wake) is fixed. MUST stay a separate instance from `embed` (#20085).
Retires the "remember to pass --reranking in extra_args" hand-wiring noted in
`registry/curated.py`.

### Unchanged
`flm`, `tts`, `tts-qwen3`, `comfyui` — not llama-server; out of scope here.

---

## 4. Separate MTP from profiles (the structural fix)

This is what actually enables "one profile per type" — `rocm`/`rocm-dnse`/
`rocm-moe` exist mostly to pair MTP on/off with batch shapes, and every future
axis would double the catalog again. Research confirms MTP is a **model**
property (needs MTP-head GGUFs; upstream arch list qwen35/qwen35moe/step35/
glm4-moe/bailingmoe2) with **backend**-dependent execution — not a flag-template
property.

Current defects it fixes:
- `MTP_FLAG_BUNDLE` hardcodes `--spec-draft-device ROCm0` → any Vulkan/custom
  profile with `mtp=true` targets the wrong device.
- `--spec-draft-n-max 4` frozen for all models while acceptance varies per
  model (roster shows 89.4% vs weak acceptors); upstream sweet spot is 2–3.
- `--spec-draft-threads 16 --spec-draft-threads-batch 32` duplicates (and can
  contradict) the profile's own thread tuning; 32 is SMT.
- Slot-level `mtp` override "effective only on MTP-capable profiles" (docs
  caveat) — silently no-ops on wrong pairings; stacks can snapshot a non-MTP
  model onto an MTP profile and ship dead flags; #807-style coherence can't
  validate it because the truth (model has heads) isn't visible to profiles.

Target design (three concerns, three layers):
1. **Profile declares capability** — image supports spec flags (derivable like
   `runtime_family`); no more mtp=true/false profile pairs.
2. **Model declares eligibility** — registry `mtp` tag (already present on
   every MTP build in `curated.py`); `model_meta` name-sniff for untagged
   pulls; GGUF `nextn` tensors are the ground truth.
3. **Slot decides** — tri-state `auto | on | off`, default auto =
   model-eligible AND profile-capable. Preserves today's override UX.

Mechanics: `MTP_FLAG_BUNDLE` constant → `spec_flags(backend, overrides)`
builder emitting the structural part (`--spec-type draft-mtp
--spec-draft-ngl all`), device derived from profile `backend`, tuning knobs
(n-max→**2**, p-min, draft KV q8_0 symmetric, threads) centrally defaulted and
per-model overridable via `defaults.extra_args`. Inject as a labelled `"spec"`
segment in `_llama_argv_segments` between `profile` and `model_defaults` — the
provenance drawer, `normalize_argv` dedup, and preview/launch/drift parity
(WS-2) all inherit it for free. Constraint to encode: MTP forces
`--parallel 1` (upstream limitation) — the builder should assert/emit it.

Migration: (1) builder + device-from-backend, no schema change; (2) slot
tri-state auto keyed to registry tag; (3) deprecate `ProfileConfig.mtp`
(auto covers every case), collapse the catalog to §3.

---

## 5. Per-MODEL registry defaults (kept OUT of profiles, by design)

The argv layering exists precisely so these ride `Model.defaults.extra_args`:

| Model family | defaults.extra_args |
|---|---|
| Qwen3.5/3.6 thinking (CROWN, ACE-SABER, chadrock 3.6) | `--temp 1.0 --top-p 0.95 --top-k 20 --min-p 0 --presence-penalty 0` |
| Qwen3.5/3.6 instruct / non-thinking | `--temp 0.7 --top-p 0.8 --top-k 20` |
| Qwopus coder builds (precise coding) | `--temp 0.6 --top-p 0.95 --top-k 20` |
| gemma-4 (12b-it, agentic-fable5) | `--temp 1.0 --top-p 0.95 --top-k 64 -ctk f16 -ctv f16 --cache-reuse 0` (KV-quant trap + SWA cache-reuse bugs; NO `--swa-full`) |
| Qwen3-Embedding-0.6B | `--pooling last` (+ client-side L2-normalize & instruction prefix — dispatcher/docs concern) |
| Agent slots hitting thinking+tool-call parser bugs | `--reasoning-budget 0` or `--chat-template-kwargs '{"enable_thinking":false}'` |

Qwen3.5/3.6 are 262k-native — no YaRN flags needed at our ctx sizes (only add
`--rope-scaling yarn` for the *original* Qwen3 32k models when pushed past 32k).

---

## 6. Shipping caveats (sequencing)

1. **PS-2 first (platform review 2026-07-03):** seed re-tunes DON'T reach
   existing installs — the installer materializes profiles.toml and the loader
   only adds missing keys. Land "seeds are virtual / never persisted" with or
   before this, or the new flags only reach fresh installs.
2. **Re-bench + refresh `PROFILE_BENCH`** — the card hero numbers
   (52.8/30.4/90/41 t/s) are pinned to the old flag sets; also consider moving
   decode numbers to the per-model roster (which already exists and is
   auto-generated) so profile cards stop conflating model+profile+MTP.
3. **Bench matrix for the re-tune** (kyuz0-dashboard style, one run each):
   `-ub 2048` vs `4096` on rocm-moe (A3B fleet); `--spec-draft-n-max 2` vs `4`
   on one dense + one MoE MTP model; `--cache-reuse 256` on/off on an agent
   trace; `--poll` on/off; Vulkan q8_0-symmetric KV spot-check at 32k ctx.
4. **`derive_profile` / `DEVICE_DEFAULT_PROFILES` updates** for the renamed +
   new profiles (embed→`embed`, rerank→`rerank`, chat→`rocm-dense`,
   MoE models→`rocm-moe`) — fold into the PS-4 single-`profile_for()` fix so
   the three tables can't drift.
5. **Slot migration:** existing slots referencing `rocm`/`rocm-dnse`/`cpu-llm`
   need a rename map (or keep old names as aliases for one release).
6. `-fa on` syntax note: current upstream is `on|off|auto` (default auto) and
   old `-fa 1` still parses — our `-fa on` spelling is already correct for the
   fork images; keep it explicit.

## 7. Measured results — matrix run 2026-07-04 (CT105, gfx1151)

MoE=Qwen3.6-35B-A3B-HaloStrix-Dyn-MTP-v7, dense=Qwen3.6-27B-UD-Q5_K_XL. All
cells GPU-exclusive (slots `enabled=false`), noise <1.1%. Raw JSON:
`/var/lib/hal0/benchmarks/matrix-cells/` (Tier A), `.../server-ab/` (Tier B).

| Cell | Question | Winner | Delta | Action |
|---|---|---|---|---|
| moe-batch | `-ub 4096` vs `2048`? | **`-ub 1024`** (neither) | pp **+30%** (1165 vs 895) | rocm-moe `-ub 1024` |
| dense-batch | confirm `8192/2048`? | inconclusive (jagged) | — | keep seeded |
| vulkan-ub | RADV sweet spot 1024? | **`-ub 256`** (not 1024) | pp +5.4% vs 512; 1024 −6% | vulkan `-ub 256` |
| kv-rocm @32k | q8 vs f16 symmetric | f16 (tg 9.0 vs 8.0) | tg +12.5% | keep q8 (memory); note cost |
| kv-vulkan @32k | q8 valid on RADV? | **q8** (pp 168 vs 116) | pp **+45%**, tg +4% | HELD — gemma f16 guard missing (§7.1) |
| threads | `-t 8` vs `16` | tie (noise) | <3% | drop `--threads-batch 32` |
| MTP n-max (dense) | 2 vs 4 | **4** | decode **+23%** (32.6 vs 26.4) | keep seeded n-max 4 |
| cache-reuse (agent) | 256 vs 0 | tie (noise) | ~1.7% | don't adopt |
| poll (agent) | poll vs none | tie | <0.1% | drop `--poll` |
| embed / rerank | sanity | PASS | 1024-dim / spread 14.4 | — |

**Gotchas hit (see memory `bench-profile-matrix-gotchas`):** the `sweep` verb
collides on one filename per model+backend (skips cells); asymmetric KV at 32k
depth falls back to CPU (2h hang) — run symmetric only; GPU slots auto-warm
mid-run (disable them). MoE-MTP draft depth NOT run (no MoE-MTP slot; dense
result + prior data corroborate n-max 4).

### 7.1 Vulkan q8 KV verify — the gemma f16 guard does NOT exist (2026-07-04)

Fact #3 assumes gemma stays f16 via "per-MODEL registry defaults"; the vulkan q8
adoption was gated on that. **Verified false:**
- `CuratedModel` (registry/curated.py) has NO launcher-default field — it is pure
  download metadata (hf_repo/hf_file/size/license). No catalog entry can carry
  `-ctk f16`.
- The only launcher-default mechanism is `ModelDefaults.extra_args` on the SLOT
  `[model].defaults` (manual, per-slot). Precedence
  (`container._llama_argv_segments`) is `profile < model_defaults < … < extra_args`
  last-wins, so a slot-level `-ctk f16` WOULD override a profile `-ctk q8_0` — but
  nothing auto-populates it per model/family.
- Live proof: the `explore` slot (gemma4-v2-q4-k-m on `rocm-dnse`, which already
  ships `-ctk q8_0 -ctv q8_0`) resolves to `-ctk q8_0` with no override — **gemma
  is already on quantized KV today, unguarded** (pre-existing, independent of the
  vulkan change).

**Trap validated + magnitude corrected (gemma-4-12B @32k, 2026-07-04):**

| backend | pp f16 | pp q8 | Δpp | tg f16 | tg q8 | Δtg |
|---|---|---|---|---|---|---|
| ROCm | 379.1 | 369.1 | −2.6% | 20.4 | 18.3 | **−10.4%** |
| Vulkan | 394.4 | 281.9 | **−28.5%** | 23.3 | 23.6 | +1.3% |

The upstream "~10x pp cliff" (#12352/#23978) did NOT reproduce on this
ROCm-7.2.4/RADV fork — q8 stayed GPU-resident (87% CPU, no fallback). But q8 KV
still clearly regresses gemma, the **mirror image** of qwen's +45% vulkan gain.
So the guard is justified at a −28% pp (vulkan) magnitude, not 10x.

**Consequences:** (1) vulkan q8 KV stays OUT of the seed. (2) gemma-on-rocm-dnse
today pays only ~10% tg (pp fine) — a real but mild regression, not a fire.

**Fix — IMPLEMENTED as `FAMILY_DEFAULTS` (config/schema.py).** Rather than a
one-off gemma KV guard, a general per-family override layer: a `{family: flags}`
table injected into the `model_defaults` argv segment at slot resolution (family
matched from the model id/filename via `model_family()`). Precedence
`profile < FAMILY_DEFAULTS < [model].defaults < … < [server].extra_args`, riding
the existing `normalize_argv` last-wins dedup — so the family override beats the
profile but a per-slot override still beats the family. First entry
`"gemma": "-ctk f16 -ctv f16 --cache-reuse 0"` auto-protects every gemma slot
(catalog or scanned) on any profile, fixing the live rocm-dnse regression. GGUF
`general.architecture` is the future family-detection hardening (not persisted on
rows today). **Follow-up:** with gemma auto-guarded, adopting Vulkan q8 KV for
the +45% qwen-at-depth win is now a safe one-line seed change.
