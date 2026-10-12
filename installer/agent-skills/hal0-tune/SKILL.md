---
name: hal0-tune
category: homelab-ops
description: Tuning LLM inference settings on hal0 / Strix Halo for throughput, latency, or memory. Use when asked to optimize/tune a model's llama.cpp flags & values (batch, ubatch, ngl, flash-attn, KV-cache quant, threads), find the fastest config, or close a gap vs other people's numbers. Combines benchmark-driven sweeps (via hal0-bench) with external research (r/LocalLLaMA, llama.cpp GitHub issues/PRs, the kyuz0 toolbox repo). Core rule: never apply a community claim without measuring it locally first.
---

# hal0 tuning

> **Requires the terminal tool.** hal0 ships Hermes with no terminal tool unless the
> operator opted in (`hal0 agent install hermes --terminal-tool`). Without it this skill
> cannot run its commands — say so plainly instead of improvising.


Find the flags/values that maximize a target metric for a model on this box, by
**measuring**, not guessing. The measurement engine is the [`hal0-bench`](../hal0-bench/SKILL.md)
CLI; this skill is the method that drives it.

Pick **one** target up front — they trade off:
- **tg t/s** (decode speed) — memory-bandwidth bound on Strix Halo (unified LPDDR5X).
- **pp t/s** (prefill) — compute bound.
- **TTFT / latency**, or **memory footprint** (fit a bigger model / longer context).
- Always subject to a **quality floor** (a faster config that degrades output is a regression).

## The loop (RED→GREEN per change)

1. **Baseline.** Run the `lane-matrix` suite (or an ad-hoc one-model suite) —
   `hal0 bench run --suite lane-matrix`, or narrower per the Sweeping section below — then
   `hal0 bench results --model <id> --json`. Record current pp/tg per lane.
2. **Research** (below) → a short list of *candidate* flags/values with a hypothesis for *why*
   each should help on gfx1151. Don't apply anything yet.
3. **Sweep** each candidate against the baseline model + backend (one variable at a time, or a
   small grid). Measure.
4. **Keep winners, discard the rest.** A change that doesn't beat baseline ± noise is dropped.
5. **Iterate** on the new best; stop when gains fall below noise (~stddev) or you hit the memory wall.
6. **Quality-check** the winning config before recommending it (see Quality floor).
7. **Report** (and optionally apply to a slot).

## Sweeping (the workhorse)

There is no standalone `sweep` command any more — a flag-tuning sweep is a **suite**
whose `[matrix].configs` is a list of `{label, flags}` variants; every variant becomes
its own measured cell (`hal0.bench.suites.Matrix`). The shipped `lane-matrix` suite
(`/etc/hal0/bench/suites/lane-matrix.toml`) is the canonical tuning suite — both lanes
across the 2k/32k/128k depth axis on a small set of profile-class representative models.
For a narrower, model-specific sweep, write an ad-hoc suite TOML and point `hal0 bench
run` at its path:

```toml
# /tmp/tune-qwen.toml
[suite]
id = "tune-qwen"
exclusive = true

[selector]
include_only = true
include = ["qwen3.8-27b"]              # registry id, not the gguf path

[matrix]
lanes   = ["rocm", "vulkan_radv"]
depths  = [2048]
configs = [
  { label = "ub512",  flags = { "-ub" = "512" } },
  { label = "ub1024", flags = { "-ub" = "1024" } },
  { label = "ub2048", flags = { "-ub" = "2048" } },
  { label = "ub4096", flags = { "-ub" = "4096" } },
]

[cells]
kinds = ["pp", "tg"]

[staleness]
max_age_days = 0        # always (re)measure — this is a one-off tuning run
```

```bash
hal0 bench plan --suite /tmp/tune-qwen.toml         # sanity-check the worklist first
hal0 bench run  --suite /tmp/tune-qwen.toml
hal0 bench results --model qwen3.8-27b --json       # then compare configs
```

Allowed tuning flags (seam whitelist, enforced on the argv `hal0.bench.harness` composes
before the `hal0-benchctl` `exec` seam re-validates it): `-b -ub -ngl -fa -ctk -ctv -p -n
-d -r -t -mmp -pg`. Tuning needs a quiet GPU, so `exclusive = true` is normally required
— only when nothing is serving; the runner stops/restarts the slots around the suite (see
hal0-bench's GPU rule).

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
(`--spec-draft-*`) and `--parallel`/slots — `installer/bench/server_ab.py` (Tier B) covers
that, talking to `hal0-api` + the slot port directly, no sudo. Its arms run under temporary
profiles (`--variant "label[;ctx=N][;mtp=off]:<flags>"`, `--ctx-size`, `--mtp`).

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

A tune lives on a **profile**, not on the slot or the model (ADR-0029). The slot names
its model and its profile; the profile holds the flags (`-b`, `-ub`, `-ctk`, `-ctv`,
`-fa`, `--parallel`, sampling, ...), the context window (`ctx_size`), the thinking
default, `vision` and `mtp` (`auto`/`off`). Never put flags in `/etc/hal0/slots/<slot>.toml`
— a write carrying a tuning key is refused (`slot.tuning_key_denied`). To persist a
validated winner:

1. **Find the slot's profile**: `hal0 slot show <slot>` (or `GET /api/slots/<slot>`) names
   it; `hal0 profile show <profile>` prints its flags and window. Note it down for rollback.
2. **Put the winner on a profile.**
   - Custom profile used only by this slot: `hal0 profile edit <profile> --flags "<flags>"
     [--ctx-size N] [--mtp auto|off]`. The dashboard's Profiles page does the same.
   - Seed profile (`chat`, `agent`, `utility`, `coding`, ... — read-only) or one other slots
     share: duplicate it, then edit the copy:
     `hal0 profile create <new-name> --from <profile> --flags "<flags>" [--ctx-size N]`,
     then `hal0 slot edit <slot> --profile <new-name>`.
   Flags the profile refuses are the ones another layer owns: hardware (`-ngl`, `-dev`,
   `-t` — slot fields), managed (`--model`, `--host`, `--port`, `--alias`, `--ctx-size` — use
   `ctx_size`) and model facts (`--jinja`, `--chat-template*`, `--mmproj`, `--no-mmproj`),
   plus `--spec-draft-device` and `--reasoning-format none`.
3. **Restart the slot** to pick it up (`hal0 slot restart <slot>`); do **not** hand-edit
   `/etc/systemd/system/hal0-slot@*` (regenerated). Check the effective command with
   `GET /api/slots/<slot>/resolved` — every flag is labelled `base`/`model`/`profile`/`slot`.
4. **Verify health** (`hal0 slot status`, the slot's `/health`) and confirm live tok/s improved.
5. Keep the rollback ready: pointing the slot back at its old profile
   (`hal0 slot edit <slot> --profile <old>`) or `hal0 profile edit` with the old values.
   This changes **production serving** — treat as Tier-2: only when safe, verify after,
   revert on regression.

`installer/bench/server_ab.py` applies its arms the same way, temporarily: each arm is a
throwaway profile cloned from the slot's, the slot is pointed at it, and the original
profile is restored and the throwaway ones deleted when the run ends (also on failure and
Ctrl-C). If a run is killed hard (SIGKILL), the next `server_ab` start for that slot deletes
the leftover `tune-<slot>-*` arm profiles nothing references, logging each one; one a slot
still points at is never deleted, only warned about.

## Output

A short tuning report: target metric, baseline vs best (pp/tg per backend), the winning flags
with the measured delta and the rationale/source for each, any quality check, and the exact
profile change (and `hal0 slot edit --profile` if the slot must be re-pointed) to apply it. Raw evidence is in the v2 store (`hal0 bench results` /
`hal0 bench history`, backed by `records.jsonl` + `bench.db` under `/var/lib/hal0-bench/`).
