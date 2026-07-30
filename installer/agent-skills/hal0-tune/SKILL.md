---
name: hal0-tune
category: homelab-ops
description: Tuning LLM inference settings on hal0 / Strix Halo for throughput, latency, or memory. Use when asked to optimize/tune a model's llama.cpp flags & values (batch, ubatch, ngl, flash-attn, KV-cache quant, threads), find the fastest config, or close a gap vs other people's numbers. Combines benchmark-driven sweeps (via hal0-bench) with external research (r/LocalLLaMA, llama.cpp GitHub issues/PRs, the kyuz0 toolbox repo). Core rule: never apply a community claim without measuring it locally first.
---

# hal0 tuning

Find the flags/values that maximize a target metric for a model on this box, by
**measuring**, not guessing. The measurement engine is the [`hal0-bench`](../hal0-bench/SKILL.md)
seam; this skill is the method that drives it.

Pick **one** target up front — they trade off:
- **tg t/s** (decode speed) — memory-bandwidth bound on Strix Halo (unified LPDDR5X).
- **pp t/s** (prefill) — compute bound.
- **TTFT / latency**, or **memory footprint** (fit a bigger model / longer context).
- Always subject to a **quality floor** (a faster config that degrades output is a regression).

## The loop (RED→GREEN per change)

1. **Baseline.** `sudo -n /usr/lib/hal0/bin/hal0-benchctl run-model <rel.gguf> --exclusive`,
   then `… aggregate`; read `/var/lib/hal0/benchmarks/SUMMARY.md`. Record current pp/tg per backend.
2. **Research** (below) → a short list of *candidate* flags/values with a hypothesis for *why*
   each should help on gfx1151. Don't apply anything yet.
3. **Sweep** each candidate against the baseline model + backend (one variable at a time, or a
   small grid). Measure.
4. **Keep winners, discard the rest.** A change that doesn't beat baseline ± noise is dropped.
5. **Iterate** on the new best; stop when gains fall below noise (~stddev) or you hit the memory wall.
6. **Quality-check** the winning config before recommending it (see Quality floor).
7. **Report** (and optionally apply to a slot).

## Sweeping (the workhorse)

`llama-bench` benchmarks the **cartesian product** of comma-separated values in one run — use
that. Through the seam:

```bash
S="sudo -n /usr/lib/hal0/bin/hal0-benchctl"
M=qwen3.6-27b/Qwen3.6-27B-UD-Q5_K_XL.gguf

# ubatch is the biggest single lever; sweep it per backend
$S sweep "$M" rocm        --exclusive -ub 512,1024,2048,4096 -p 2048 -n 64
$S sweep "$M" vulkan_radv --exclusive -ub 256,512,1024       -p 2048 -n 64

# batch x flash-attn grid
$S sweep "$M" rocm --exclusive -b 2048,4096,8192 -fa 0,1 -p 2048 -n 64

# KV-cache quant (memory + speed vs quality) at depth
$S sweep "$M" rocm --exclusive -ctk q8_0,f16 -ctv q8_0,f16 -p 2048 -n 64 -d 16384

$S aggregate   # then read SUMMARY.md — sweep rows are tagged "sweep"
```

Allowed sweep flags (seam whitelist): `-b -ub -ngl -fa -ctk -ctv -p -n -d -r -t -mmp -pg`.
Tuning needs a quiet GPU, so `--exclusive` is normally required — only when nothing is
serving; it restarts the slots on exit (see hal0-bench's GPU rule).

## What's worth tuning on Strix Halo (gfx1151, Radeon 8060S)

Priors from the production `hal0-slot@agent` config: `-b 8192 -ub 2048 -ctk q8_0 -ctv q8_0
-fa on --no-mmap`, threads 16/32. Start near these.

| Flag | Effect | Notes |
|------|--------|-------|
| `-ub` (ubatch) | biggest lever on both pp and tg | **optimum differs per backend** — ROCm likes large (2048+), Vulkan often smaller (512). Always sweep. |
| `-b` (batch) | prefill throughput | pair with ub; diminishing returns past 8192. |
| `-fa` (flash-attn) | speed + memory at depth | usually a win on; verify on this gfx — Vulkan FA support has changed across versions. |
| `-ctk`/`-ctv` (KV quant) | memory + bandwidth | `q8_0` ≈ big memory save, tiny quality cost; `f16` = baseline quality. Matters most at long context (decode is bandwidth bound). |
| `-t` (threads) | CPU-side work | sweep around physical cores (16); rarely the bottleneck for GPU offload. |
| `-ngl` | set per lane, not per sweep | 99 (full offload) on `rocm`/`vulkan_radv`, 0 on the `cpu` lane. Only pass your own to study the CPU/GPU split — llama-bench APPENDS it to the lane's value as a second sweep row rather than replacing it. |

Out of llama-bench scope (server-level, tune separately): **draft/MTP speculative decode**
(`--spec-draft-*`) and `--parallel`/slots — hal0 has `/root/bench_mtp.py` for that.

## External research (then verify locally)

Surface ideas, **then prove them with a sweep** — hardware/driver/version specifics make
generic advice unreliable.

- **r/LocalLLaMA** — search: `Strix Halo`, `Ryzen AI Max 395`, `gfx1151`, `Radeon 8060S`,
  `ROCm vs Vulkan llama.cpp`, `unified memory GTT`. Good for real-world flag combos + gotchas.
- **llama.cpp GitHub** — issues/PRs/discussions for `ROCm`/`HIP`, `Vulkan`, `gfx1151`,
  `flash attention`, `KV cache quant`. PRs often reveal which backend just got faster and the
  exact flag that unlocks it.
- **kyuz0/amd-strix-halo-toolboxes** — issues + its `benchmark/` results; this is the same
  image lineage we run, so its findings transfer best.
- **ROCm release notes / AMD community** — for gfx1151 enablement and `HSA_OVERRIDE` quirks.

Use your web tools (or the deep-research skill). For each promising claim, write down the
**hypothesis + source**, then the **measured delta** on our model. Discard claims that don't
reproduce here.

## Quality floor (don't trade quality blindly)

Speed flags can hurt output — especially aggressive KV quant. Before recommending a config
that touches `-ctk/-ctv` (or anything beyond ub/b), validate quality, e.g. the existing
`/mnt/lab/qwen3coder-bench/` capability/eval runs on the tuned settings. Report speed **and**
quality; if quality drops, the config is not a win.

## Applying a winning config to a slot

Slot configs live in `/etc/hal0/slots/<slot>.toml` (`hal0:hal0` — agent-editable). To persist
tuned flags:

1. **Back up** the toml. Change **one** thing (the validated winner).
2. Reload via the hal0 CLI (`hal0 slot ...` — check `hal0 slot --help`; it regenerates the
   unit through the `hal0-slotctl` seam). Do **not** hand-edit `/etc/systemd/system/hal0-slot@*`
   (regenerated).
3. **Verify health** (`hal0 slot status`, the slot's `/health`) and confirm live tok/s improved.
4. Keep the rollback ready. This changes **production serving** — treat as Tier-2: only when
   safe, verify after, revert on regression.

## Output

A short tuning report: target metric, baseline vs best (pp/tg per backend), the winning flags
with the measured delta and the rationale/source for each, any quality check, and the exact
`hal0 slot` change to apply it. Raw evidence is in `/var/lib/hal0/benchmarks/` (`index.json`).
