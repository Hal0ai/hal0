# ROCmFPX bench — RESULTS + next-session handoff (2026-07-05)

Strix Halo box (gfx1151, 128 GB unified, ~256 GB/s LPDDR5X). Runtime **podman**.
This closes out the exploratory ROCmFPX profile bench (the `rocmfpx-bench-handoff-2026-07-05.md`
gate is fully passed: #1068 and #1069 are merged and live), reports results against
the runbook's pre-registered hypotheses, and hands the next session a **model-selection**
research task that the bench proved is the dominant performance lever.

Companion docs: `rocmfpx-runner-bench-runbook-2026-07-05.md` (the spec: matrix,
findings 0.1–0.7, §2 gates, §3 pre-registered deltas), `rocmfpx-bench-results-2026-07-05.md`
(earlier appendix — this doc supersedes it).

---

## 0. TL;DR

- The bench **could not run as the runbook specified** — three runner-level
  blockers (per-request MTP override ignored, a cumulative GPU-memory leak, and a
  stale/wrong-image Tier-A seam) invalidate most of the pre-registered sweep. Those
  blockers are themselves the highest-value findings.
- What was delivered instead: the full prereq stack validated live, both FPX
  profiles set to their **HF-model-card-exact** flags, trustworthy production
  decode numbers for both FPX models, a draft-KV experiment, a new `saber-fpx`
  profile, and a **quantitative explanation of the "140 t/s" gap** (it's the quant,
  not the args).
- **The seeds already match the vendor cards; there is almost no flag lever left.**
  The real lever is **model/quant selection per slot** — hence the next-session task.

---

## 1. RESULTS vs the runbook's §3 pre-registered deltas (§2 gate applied)

Production numbers are greedy (temp 0), seeded/card MTP config, `hal0-rocmfpx:7aa484a`
runner. "Real task" = a genuine LRU-cache code-gen via chat template; "deterministic"
= the repeated-passage bench prompt (inflates MTP acceptance → decode ceiling).

| Model (slot) | Lane | Decode t/s | Prefill t/s | MTP accept |
|---|---|---|---|---|
| 27B dense ROCmFP4 (`code-fpx`) | ROCm0 | **~29** real / ~38.5 synthetic | ~330 | 57% / 85–92% |
| 35B-A3B MoEQuality (`moe-fpx`/saber) | Vulkan0 | **~76** real / ~95–98 deterministic | ~820 @3.8k-depth | 74% real / ~88–100% det. |

Per-hypothesis verdicts:

- **0.1 Batch shape** — NOT independently re-derived (Tier-A seam blocked, see §2).
  The merged seed already ships `-b 512 -ub 512` (27B) / `-b 2048 -ub 512` (35B),
  which **matches the HF card verbatim**. Verdict: **keep** (card-canonical), unproven-on-box.
- **0.2 KV quant** — 27B main KV `q4_0` and 35B main KV `q8_0` already baked (card).
  **Draft-KV experiment (relaunch-based, the only valid way — see 0.6):** q8_0 draft
  helped the 27B (**+6%**, 29→30.8 t/s, accept 57→61%) but **hurt** the 35B (**−4%**,
  76→73 t/s, accept 74→69%). Per user, reverted to card (27B q4_0, 35B f16). Verdict:
  **draft-KV is model-specific**; ship the card values; q8 is a live option for the dense 27B only.
- **0.3 MTP params** — n-max/p-min per card (27B n4/p0.0, 35B n3/p0.25). NOT swept
  this session (per-request override is dead, see 0.6; relaunch sweep descoped).
- **0.4 MTP × continuous batching** — NOT measured. The seed ships `--parallel 1`;
  concurrency needs a relaunch with `--parallel N -np N`, and the memory leak (§2)
  makes a long np-sweep unsafe without per-cell relaunch. **Open.**
- **0.5 Vulkan vs HIP lane crossover** — NOT cross-measured (each model stayed on its
  card lane). Data points: 27B ROCm0 decode ~29–38 t/s / prefill ~330; 35B Vulkan0
  decode ~76–98 / prefill ~820. Cross-lane runs are **open**.
- **0.6 Restart-free MTP sweep** — **REJECTED / INVALID.** The 7aa484a runner
  **ignores per-request `speculative.{n_max,n_min,p_min}`** (draft_n identical for
  n_max=1 vs 4). MTP params must be swept by **relaunch**.
- **0.7 HIP env** — **ACCEPTED.** `HSA_OVERRIDE_GFX_VERSION=11.5.1` +
  `GGML_HIP_ENABLE_UNIFIED_MEMORY=1` added to `code-fpx` `[server].env`; confirmed
  reaching podman `--env` on the live container. Ship it.

**Concrete seed-flag diffs (net):** the seeds/cards need **no flag changes** beyond
(a) HIP env on the ROCm lane (done) and (b) an optional bounded `context_size` for
bench grinds. Everything else already matches the vendor cards. The only *behavioral*
levers left are **draft-KV q8 for the dense 27B** (+6%, optional) and **model/quant
selection** (§5 — the real win).

---

## 2. Blockers / findings that reshaped the plan (all reproduced)

1. **Per-request MTP override ignored** (finding 0.6 above) — kills the restart-free sweep.
2. **Cumulative GPU memory leak → OOM** (`amdgpu BO_VA -12`/ENOMEM) after ~15–18 MTP
   requests per warm server; smaller ctx only delays it. Relaunch every ≤4 cells.
   (Source of the stray `oom` file.) NOTE: the card's `-c 262144` (27B) / `-c 65536`
   (35B) **load fine** — the OOM is the leak under sustained load, not the allocation.
3. **Tier-A llama-bench seam is stale + wrong image.** `sudo hal0-benchctl` runs
   `/usr/lib/hal0/bench/` (Jul 4, no fpx cells, old `server_ab.py`) whose `config.sh`
   hardcodes STOCK toolbox images, not the ROCmFPX fork. FPX Tier-A can't run until the
   installed harness is refreshed + `config.sh` BACKENDS repointed to `localhost/hal0-rocmfpx:7aa484a`.
4. **`server_ab.py --mode mtp` mismeasures early-EOS models** — raw `/completion` makes
   the 35B (strict template) stop at 1 token; use the chat endpoint or `ignore_eos`.
5. **MTP acceptance is non-deterministic run-to-run even on byte-identical input** —
   ~1 in 5 runs the draft acceptance collapses (~100% → ~55–60%), dropping decode
   ~35% (95 → 64 t/s). Decode t/s tracks acceptance ~linearly. Worth a root-cause pass
   (KV/scheduler state carryover on the warm server).

---

## 3. The "140 t/s" question (answered — important for §5)

A friend gets ~140 t/s on the SABER model with the *same args*; this box gets ~95
(deterministic ceiling). **It is the quant, not the config** (our resolved command
matches their paste exactly):

- Friend: `...NSC-ACE-SABER-MTP-F16-to-ROCmFP4-STRIX_LEAN.gguf` (~4 bpw, ~19 GB).
- Ours:   `...Ace-Saber-MTP-ROCmFPX-MoEQuality-7.07BPW.gguf` (7.07 bpw, 29.3 GB).

Decode on this APU is **memory-bandwidth-bound** (~256 GB/s). A3B ≈ 3B active params/token:
FP4 → ~1.5 GB/token → ~170 t/s ceiling; 7.07 BPW → ~2.6 GB/token → ~98 t/s ceiling.
Both parties sit at the ceiling for their quant. Size ratio 29.3/19 = **1.54×** predicts
95 × 1.54 = **146 t/s** — brackets the friend's 140. The FP4 STRIX_LEAN saber is **not on
the box** (only a 125-byte HF download stub: `...STRIX_LEAN.gguf.metadata`). Friend also
built a decode-tuned Vulkan branch (`wt-submit-fpx-vulkan-fp3-speed`), a secondary few-%.

**Takeaway that drives §5:** on Strix Halo, **quant choice ≈ decode speed**. Model/quant
selection per slot is a bigger lever than any flag.

---

## 4. On-box state (what changed — all documented, reversible)

- **Live/serving:** `moe-fpx` is loaded on the new `saber-fpx` profile serving
  `CHADROCK-35B-Ace-Saber-MTP-ROCmFPX-MoEQuality-7.07BPW.gguf` (ctx 32768). `code-fpx`
  is offline with card flags applied. The `hal0` gateway + remote `minimax` were never
  stopped (GPU was already idle — no assistant disruption).
- **Profiles:** new custom `[profile.saber-fpx]` in `/etc/hal0/profiles.toml` (throughput
  config, image pinned `localhost/hal0-rocmfpx:7aa484a`, `--no-mmproj` text-only,
  f16 KV, n4/p0.0, sampler baked). Seed `rocmfpx-rocm`/`rocmfpx-moe` images overridden
  to `:7aa484a` in the **venv** `SEED_PROFILES` (ephemeral — lost on release; seeds are
  virtual so `profiles.toml` edits to them are no-ops).
- **Registry:** `moe-fpx`/`code-fpx` model `defaults.extra_args` carry card flags;
  `moe-fpx` slot `[server].extra_args` forces `--spec-draft-p-min 0.0` (wins over model 0.25).
- **Contexts:** slot `context_size` set to card values (27B 262144, 35B 32768 for the saber bench).
- **venv src:** synced to origin/main (#1068 detector + #1069 seeds live in the running
  `hal0-api`; it imports from venv site-packages, NOT an editable checkout).
- **Backups:** `/etc/hal0/fpx-bench-backup-20260705/`, `/var/lib/hal0/venv-fpx-backup-20260705/`,
  `/var/lib/hal0/registry/registry.toml.bak-draftkv8-20260705`.
- **Bench display fix:** `installer/bench/generate_results_json.py` now ingests
  `server-ab/*.json` into a Tier B/C SUMMARY section (was Tier-A-only). Staged on branch
  `bench/rocmfpx-explore-2026-07-05`, not committed.

---

## 5. NEXT SESSION — research & pick optimized models for slot/stack layouts

This is the highest-value follow-up. The bench proved flags are mostly maxed and the
runner is the constraint; the open lever is **which model + which quant runs in which
slot**, and **which slots co-reside in a stack**. Spend real time here — treat it as
research, not a mechanical config edit.

**Ground truth to reason from:**
- Decode t/s ≈ `mem_bandwidth / (active_params × bytes_per_weight)`; Strix Halo ≈
  256 GB/s. So on an A3B MoE: FP4 ≈ ~140–170 t/s, 7BPW ≈ ~95 t/s, Q8 ≈ ~50 t/s.
  Dense models pay full param count every token (27B dense ROCmFP4 ≈ ~29 t/s).
- Prefill is compute-bound and lane-sensitive (Vulkan0 strong: ~820 t/s @ 3.8k; ROCm ~330).
- Total unified budget 128 GB; GTT is the real constraint for co-residency (each 35B
  MoEQuality ≈ 33 GB resident; FP4 ≈ ~20 GB). Stacks that co-load multiple GPU slots
  must fit the sum + KV.
- Quant is a **quality↔speed** trade: FP4 STRIX_LEAN = fast/leaner; MoEQuality 7.07 =
  accurate/heavy. Match to the slot's job.

**Deliverables for the next session:**
1. **Inventory** every model/quant available (and pullable) per role — `hal0 model list`,
   the registry, `/mnt/ai-models`, and the HF repos (`jcbtc/*`, `GestaltLabs/*`,
   `Qwen-AgentWorld`, `fastcontext`, etc.). Record size, bpw, active params, lane,
   MTP/vision, and the vendor card's recommended args for each.
2. **Per-slot recommendation** — for each functional slot (assistant/chat, coder,
   agent/tool-calling, nano/fast-context, explore, embed/rerank, vision), pick the
   model+quant that best fits its latency/quality/memory profile, with the predicted
   decode t/s and the reasoning. E.g.: interactive assistant → favor FP4 STRIX_LEAN for
   speed; quality-critical coding/agent → MoEQuality; nano → smallest fast draft.
3. **Stack layouts** — propose 2–3 concrete `stacks.toml` layouts (which slots co-reside)
   that fit the 128 GB / GTT budget and the GPU arbiter's single-GPU reality, e.g. a
   "coding" stack, an "agent fan-in" stack, a "voice+vision" stack. Note eviction/
   co-residency (the ~31 GB GTT floor caveat) and which lane each slot takes.
4. **The FP4-vs-MoEQuality decision for SABER** — decide whether to pull the ~19 GB FP4
   STRIX_LEAN saber (HF `jcbtc/chadrock-35b-ace-saber-rocmfp4-mtp`, only a stub on-box
   now) for the ~140 t/s throughput lane, keep MoEQuality for quality, or run BOTH as
   selectable models on the `moe-fpx`/saber slot. Bench both head-to-head on the
   deterministic prompt if pulled.
5. Optionally evaluate building the `fp3-speed` Vulkan runner for the last few %.

**Method note:** use the deep-research / model-card reading approach, not guesswork —
each `jcbtc/*` card documents the intended quant/lane/args. Cross-check against the
bandwidth math above so every recommendation has a predicted t/s, not a vibe.

---

## 6. To resume the bench itself (if flags get revisited)

- Slot is warm-restart-sensitive to the leak → run ≤4 cells then `hal0 slot load <slot>`.
- MTP param sweeps: **relaunch per value** (edit model `defaults.extra_args` or slot
  `[server].extra_args`, restart `hal0-api`, reload slot) — per-request JSON is ignored.
- Always `--runner-image hal0-rocmfpx:7aa484a`; bench via `server_ab.py` from the
  worktree (the installed seam harness is stale). Use `ignore_eos` + a real/deterministic
  prompt; report decode + acceptance together (decode tracks acceptance).
- Restore: leave FPX slots offline; backups listed in §4.
