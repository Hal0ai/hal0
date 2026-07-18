# ROCmFPX exploratory bench — RESULTS (2026-07-05)

Ran on the Strix Halo box (gfx1151, 128 GB unified) after #1068/#1069 merged.
Runner: `localhost/hal0-rocmfpx:7aa484a` (a066ead70b2d) on both FPX slots.
Scope (per user): **FPX / FP4 / MTP models only** — `code-fpx` (27B ROCmFP4
dense, ROCm0) and `moe-fpx` (35B-A3B ROCmFPX MoEQuality, Vulkan0).

## Headline numbers (greedy temp=0, seeded MTP config, real code-gen chat task)

| Model (slot) | Lane | Decode t/s | MTP accept | Prefill @depth2k |
|---|---|---:|---:|---:|
| 27B dense (`code-fpx`) | ROCm0 | **~29** (real task) / ~38.5 (synthetic prompt) | 57% / 85–92% | ~330 |
| 35B-A3B MoE (`moe-fpx`) | Vulkan0 | **~76** | 74% | ~1040 |

**The A3B MoE on Vulkan decodes ~2.6× faster and prefills ~3× faster than the
dense 27B on ROCm** (3B active params vs 27B dense). For the two most-used FPX
models, `moe-fpx` is by far the stronger performer.

Decode is prompt-sensitive: the 27B's synthetic depth-2k prompt gave 85–92%
acceptance (→38.5 t/s); a genuine LRU-cache task gave 57% (→29 t/s). Report the
real-task numbers as production-representative.

## Blockers / findings that reshape the plan (all real, all reproduced)

1. **Per-request MTP override is IGNORED by the 7aa484a runner.** `speculative.
   {n_max,n_min,p_min}` in the `/completion` JSON does nothing: `draft_n=116,
   accepted=98` identical for n_max=1 vs n_max=4. This **invalidates runbook
   finding 0.6** (restart-free MTP sweep) — every `--mode mtp` cell measured the
   same launch-time config. MTP params must be swept by RELAUNCH (`--mode ab`
   variant flags or model `defaults.extra_args` edits), not per-request JSON.

2. **Cumulative GPU memory leak → OOM (`amdgpu BO_VA -12` / ENOMEM).** The
   runner leaks across MTP requests: crashed at cell 5 (168k ctx) / cell 7 (40k
   ctx), i.e. ~15–18 requests per server session regardless of ctx size (smaller
   ctx only delays it). Mitigation: **relaunch the slot every ≤4 cells.** This is
   why prior sessions left a stray `oom` file.

3. **168k/65k seed context OOMs on load-under-pressure.** Bounded both FPX slots
   to `context_size = 40960` (`/etc/hal0/slots/{code-fpx,moe-fpx}.toml`). **128k
   depth is memory-infeasible** for 27B+MTP here; depth >~24k fails HTTP 400
   against the 40k bound.

4. **Tier-A llama-bench FPX cells are un-runnable via the seam.** `sudo
   hal0-benchctl` runs a STALE installed harness (`/usr/lib/hal0/bench/`, Jul 4 —
   no fpx cells, old `server_ab.py`) whose `config.sh` hardcodes STOCK toolbox
   images (`amd-strix-halo-toolboxes:*`), not the ROCmFPX fork. FPX weights need
   the 7aa484a runner. → Tier A deferred; Tier B/C `server_ab.py` (real runner,
   `--runner-image`) is the only valid path. To enable Tier A: refresh the
   installed harness + point `config.sh` BACKENDS at `localhost/hal0-rocmfpx:7aa484a`.

5. **`server_ab.py --mode mtp` mismeasures early-EOS models.** Raw `/completion`
   prompts make the 35B (strict Froggeric template) stop after 1 token
   (`predicted_n=1`). Needs `ignore_eos` or the chat endpoint. Tool gap to fix.

## Seed-flag verdicts (§2 gate applied)

- **27B `-b 512 -ub 512`, `-ctk/-ctv q4_0`**: already baked in the merged seed
  (verified in resolved command). NOT re-derived via Tier A (seam blocked), but
  the seed matches the fork's own optimization doc — **keep**.
- **35B `-b 2048 -ub 512`, main KV q8/q8, draft-KV f16, n-max 3 p-min 0.25,
  `--no-spec-draft-backend-sampling`**: baked in seed; runs at ~76 t/s / 74%
  accept — **keep** (no evidence to change; draft-KV q4 hypothesis untested
  because per-request/ab sweep needs relaunch and was descoped this session).
- **HIP env** (`HSA_OVERRIDE_GFX_VERSION=11.5.1`,
  `GGML_HIP_ENABLE_UNIFIED_MEMORY=1`): **added** to `code-fpx` `[server].env`
  (confirmed reaching podman `--env`). ROCm lane was previously env-less. SHIP
  this to the seed/slot.
- **context_size**: seed 168k/65k → **40960** (OOM fix). Recommend the seed
  ship a bounded default for the FPX MTP slots on gfx1151.
- **image**: bench used `localhost/hal0-rocmfpx:7aa484a`; seed ships `:server`
  (identical intent, local-only tag). No seed change — repoint per-box.

## On-box changes left in place (documented, reversible)

- `/etc/hal0/slots/code-fpx.toml`: `[server.env]` HIP vars; `context_size 40960`.
- `/etc/hal0/slots/moe-fpx.toml`: `context_size 40960`.
- venv `SEED_PROFILES` rocmfpx-rocm/moe image → `localhost/hal0-rocmfpx:7aa484a`
  (marked `BENCH-OVERRIDE`; ephemeral — lost on next release). Backups:
  `/etc/hal0/fpx-bench-backup-20260705/`, `/var/lib/hal0/venv-fpx-backup-20260705/`.
- venv src synced to origin/main (#1068 detector + #1069 seeds now live).
- Both FPX slots left **offline** (pre-bench run state). Live `hal0` gateway +
  remote `minimax` never stopped (GPU was already idle).

## Display improvement (the other ask)

`installer/bench/generate_results_json.py` now ingests `server-ab/*.json` and
renders a **Tier B/C section** (decode t/s · draft accept % · concurrency
aggregate/per-stream/TTFT) in SUMMARY.md — previously it only showed Tier-A
llama-bench and could not display ANY of this bench's output. Also: short model
names + a `quant` column (ROCMFP4·MTP etc.). Staged on branch
`bench/rocmfpx-explore-2026-07-05`.
